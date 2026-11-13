"""Campaign operations behind the CLI: plan, prepare, build, status.

Layout of a campaign directory (the folder that holds config.yaml):

    forcefield/                 copy of the Martini 3 files the topologies include
    peptides/<name>/            helix.pdb, cg.pdb, <name>.itp, prepared.json
    conditions/<id>/            condition.yaml, rep_000 ... rep_NNN
    results/                    peptides.csv, conditions.csv
    events.jsonl                campaign-level log; each replica has its own as well
"""

import csv
import json
import shutil
from pathlib import Path

import yaml

from . import FORCEFIELD_DIR, seeds
from .builder import SystemSpec, build_system
from .config import NO_PEPTIDE, Campaign, Condition
from .descriptors import describe
from .events import EventLog
from .lipids import LipidLibrary
from .peptide.martinize import load_or_prepare

BEADS_PER_RESIDUE_ESTIMATE = 2.3  # used by `plan` before a peptide is coarse-grained


def replica_dir(campaign: Campaign, condition: Condition, replica: int) -> Path:
    return campaign.root / "conditions" / condition.id / f"rep_{replica:03d}"


def _write_csv(path: Path, rows: list) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def install_forcefield(campaign: Campaign) -> Path:
    """Copy the force-field files into the campaign, so it does not depend on the package path."""
    target = campaign.root / "forcefield"
    target.mkdir(parents=True, exist_ok=True)
    for source in sorted(FORCEFIELD_DIR.glob("*.itp")):
        destination = target / source.name
        if not destination.exists() or destination.stat().st_size != source.stat().st_size:
            shutil.copyfile(source, destination)
    return target


# plan ---------------------------------------------------------------------------------------

def plan(campaign: Campaign, library: LipidLibrary, ns_per_day: float = None) -> dict:
    """Expand the campaign without running anything."""
    rows = []
    for c in campaign.conditions:
        lipid_beads = sum(library.get(name).n_beads * n for name, n in c.lipids)
        solvent = int(round(c.water_beads_per_lipid * c.n_lipids))
        peptide_beads, exact = 0, True
        if c.peptide != NO_PEPTIDE:
            marker = campaign.root / "peptides" / c.peptide / "prepared.json"
            if marker.exists():
                peptide_beads = json.loads(marker.read_text())["beads"] * c.copies
            else:
                exact = False
                length = len(campaign.peptides[c.peptide].sequence)
                peptide_beads = int(round(BEADS_PER_RESIDUE_ESTIMATE * length)) * c.copies
        beads = lipid_beads + solvent + peptide_beads
        built = sum(
            (replica_dir(campaign, c, i) / "manifest.json").exists()
            for i in range(campaign.replicas)
        )
        rows.append({
            **c.as_row(),
            "peptide_to_lipid": f"1:{round(c.n_lipids / c.copies)}" if c.copies else "0",
            "beads": beads,
            "beads_exact": exact,
            "box_nm": round((0.125 * beads) ** (1 / 3), 2),
            "replicas": campaign.replicas,
            "built": built,
        })

    simulations = len(rows) * campaign.replicas
    ns_each = campaign.simulation["assembly_ns"] + campaign.simulation["production_ns"]

    # Measured figures from replicas that already ran, when there are any.
    measured_speed, bytes_per_bead_frame = None, None
    speeds = [r["ns_per_day"] for r in status(campaign) if r["ns_per_day"]]
    if speeds:
        measured_speed = round(sum(speeds) / len(speeds), 1)
    from .engine import read_state

    for row, c in zip(rows, campaign.conditions):
        stage = read_state(replica_dir(campaign, c, 0)).get("stages", {}).get("assembly", {})
        if stage.get("frames") and stage.get("trajectory_bytes"):
            bytes_per_bead_frame = stage["trajectory_bytes"] / stage["frames"] / row["beads"]
            break
    speed = ns_per_day or measured_speed
    disk_gb = None
    if bytes_per_bead_frame:
        frames_each = ns_each * 1000 / campaign.simulation["frame_ps"]
        disk_gb = round(sum(r["beads"] for r in rows) * campaign.replicas
                        * frames_each * bytes_per_bead_frame / 1e9, 1)
    totals = {
        "conditions": len(rows),
        "replicas_per_condition": campaign.replicas,
        "simulations": simulations,
        "ns_per_simulation": ns_each,
        "simulated_microseconds": round(simulations * ns_each / 1000, 3),
        "ns_per_day": speed,
        "ns_per_day_source": "flag" if ns_per_day else "measured" if measured_speed else None,
        "compute_days": round(simulations * ns_each / speed, 1) if speed else None,
        "trajectory_gb": disk_gb,  # trajectories only; from a measured frame of this campaign
    }
    return {"config": str(campaign.path), "master_seed": campaign.master_seed,
            "conditions": rows, "totals": totals}


# prepare ------------------------------------------------------------------------------------

def prepare(campaign: Campaign, force: bool = False, on_done=None) -> list:
    """Coarse-grain every peptide the conditions use. Returns one row per peptide."""
    log = EventLog(campaign.root, stage="prepare")
    rows = []
    for definition in campaign.used_peptides():
        outdir = campaign.root / "peptides" / definition.name
        with log.step("prepare_peptide", peptide=definition.name):
            peptide, reused = load_or_prepare(
                definition.name, definition.sequence, outdir,
                definition.secondary_structure, definition.c_terminus, force=force,
            )
        row = describe(definition.name, definition.sequence, definition.c_terminus,
                       peptide.secondary_structure, peptide.n_beads, peptide.charge)
        row["reused"] = reused
        row["warnings"] = "; ".join(peptide.warnings)
        (outdir / "descriptors.json").write_text(json.dumps(row, indent=2) + "\n")
        for warning in peptide.warnings:
            log.write("prepare_warning", status="warning", peptide=definition.name, message=warning)
        rows.append(row)
        if on_done:
            on_done(row)
    _write_csv(campaign.root / "results" / "peptides.csv",
               [{k: v for k, v in r.items() if k != "reused"} for r in rows])
    return rows


# build --------------------------------------------------------------------------------------

def build_targets(campaign: Campaign, condition_ids=None, replicas: int = None) -> list:
    """(condition, replica index) pairs to build, in a stable order."""
    conditions = campaign.conditions
    if condition_ids:
        conditions = [campaign.condition(cid) for cid in condition_ids]
    count = campaign.replicas if replicas is None else min(replicas, campaign.replicas)
    return [(c, i) for c in conditions for i in range(count)]


def build(campaign: Campaign, targets: list, library: LipidLibrary, force: bool = False,
          on_done=None) -> list:
    """Build the boxes. One failure never stops the others; each outcome is logged."""
    forcefield = install_forcefield(campaign)
    log = EventLog(campaign.root, stage="build")
    _write_csv(campaign.root / "results" / "conditions.csv",
               [c.as_row() for c in campaign.conditions])

    peptides = {}
    outcomes = []
    for condition, index in targets:
        directory = replica_dir(campaign, condition, index)
        seed = seeds.replica_seed(campaign.master_seed, condition.id, index)
        outcome = {"condition": condition.id, "replica": index, "seed": seed,
                   "directory": str(directory.relative_to(campaign.root))}
        manifest = directory / "manifest.json"
        try:
            if manifest.exists() and not force:
                if json.loads(manifest.read_text()).get("seed") != seed:
                    raise RuntimeError(
                        "existing box was built with a different seed; "
                        "the master seed changed. Use --force to rebuild."
                    )
                outcome["status"] = "skipped"
            else:
                condition_dir = directory.parent
                condition_dir.mkdir(parents=True, exist_ok=True)
                (condition_dir / "condition.yaml").write_text(
                    yaml.safe_dump(condition.as_row(), sort_keys=False))
                peptide = None
                if condition.peptide != NO_PEPTIDE:
                    if condition.peptide not in peptides:
                        d = campaign.peptides[condition.peptide]
                        peptides[condition.peptide], _ = load_or_prepare(
                            d.name, d.sequence, campaign.root / "peptides" / d.name,
                            d.secondary_structure, d.c_terminus)
                    peptide = peptides[condition.peptide]
                spec = SystemSpec(
                    lipids=dict(condition.lipids),
                    water_beads_per_lipid=condition.water_beads_per_lipid,
                    salt_molar=condition.salt_molar,
                    peptide=peptide,
                    copies=condition.copies,
                    oligomer_size=condition.oligomer_size,
                    arrangement=condition.arrangement or "antiparallel",
                    hydrophobic_face=condition.hydrophobic_face or "inward",
                    axis_distance=condition.axis_distance or 1.0,
                    oligomer_fraction=1.0 if condition.oligomer_fraction is None
                    else condition.oligomer_fraction,
                    periodicity=campaign.simulation["periodicity"],
                    wall_margin=campaign.simulation["walls"]["margin_nm"],
                )
                replica_log = EventLog(directory, condition=condition.id, replica=index)
                with replica_log.step("build", seed=seed):
                    result = build_system(spec, seed, directory, forcefield_dir=forcefield,
                                          library=library,
                                          title=f"SHAKER {condition.id} rep_{index:03d}")
                outcome.update(status="built", beads=result.info["total_beads"],
                               box_nm=result.info["box_nm"][0])
        except Exception as exc:  # a failed replica is recorded, never dropped silently
            outcome.update(status="failed", error=type(exc).__name__, message=str(exc))
        log.write("build_replica", status=outcome["status"],
                  **{k: v for k, v in outcome.items() if k != "status"})
        outcomes.append(outcome)
        if on_done:
            on_done(outcome)
    return outcomes


# run ----------------------------------------------------------------------------------------

def run_targets(campaign: Campaign, condition_ids=None, replicas: int = None,
                replica_indices=None, until: str = None) -> dict:
    """Sort the selected replicas into: to run, already done, not built yet."""
    from .engine import read_state, stage_names

    target = until or stage_names(campaign.simulation)[-1]
    conditions = campaign.conditions
    if condition_ids:
        conditions = [campaign.condition(cid) for cid in condition_ids]
    if replica_indices:
        indices = sorted(set(replica_indices))
        bad = [i for i in indices if not 0 <= i < campaign.replicas]
        if bad:
            raise ValueError(f"replica index out of range (0 to {campaign.replicas - 1}): {bad[0]}")
    else:
        indices = range(campaign.replicas if replicas is None else min(replicas, campaign.replicas))
    todo, done, unbuilt = [], [], []
    for condition in conditions:
        for index in indices:
            directory = replica_dir(campaign, condition, index)
            if not (directory / "manifest.json").exists():
                unbuilt.append((condition, index))
            elif (directory / f"{target}.gro").exists():
                done.append((condition, index))
            else:
                todo.append((condition, index))
    return {"todo": todo, "done": done, "unbuilt": unbuilt, "target": target}


def run_one(campaign: Campaign, condition: Condition, index: int, **options) -> dict:
    """Run one replica and return a flat outcome row. Never raises for a failed simulation."""
    from .engine import run_replica

    directory = replica_dir(campaign, condition, index)
    seed = seeds.replica_seed(campaign.master_seed, condition.id, index)
    outcome = {"condition": condition.id, "replica": index}
    try:
        state = run_replica(directory, seed, campaign.simulation,
                            context={"condition": condition.id, "replica": index}, **options)
        stages = state["stages"]
        finished = [n for n, s in stages.items() if s.get("status") == "done"]
        outcome.update(
            status=state["status"], reason=state.get("reason"),
            last_stage=finished[-1] if finished else None,
            ns_per_day=next((stages[n].get("ns_per_day") for n in reversed(finished)
                             if stages[n].get("ns_per_day")), None),
            warning=stages.get("em", {}).get("warning"),
        )
    except Exception as exc:  # recorded, never dropped
        outcome.update(status="failed", reason=f"{type(exc).__name__}: {exc}", last_stage=None,
                       ns_per_day=None, warning=None)
    EventLog(campaign.root, stage="run").write(
        "run_replica", status=outcome["status"],
        **{k: v for k, v in outcome.items() if k != "status"})
    return outcome


# status -------------------------------------------------------------------------------------

def status(campaign: Campaign) -> list:
    from .engine import read_state, stage_names

    names = stage_names(campaign.simulation)
    rows = []
    for c in campaign.conditions:
        built = minimized = equilibrated = assembled = failed = 0
        speeds = []
        for i in range(campaign.replicas):
            directory = replica_dir(campaign, c, i)
            if not (directory / "manifest.json").exists():
                continue
            built += 1
            minimized += (directory / "em.gro").exists()
            equilibrated += (directory / f"{names[-2]}.gro").exists()
            assembled += (directory / "assembly.gro").exists()
            state = read_state(directory)
            failed += state.get("status") == "failed"
            speed = state.get("stages", {}).get("assembly", {}).get("ns_per_day")
            if speed:
                speeds.append(speed)
        rows.append({"condition": c.id, "peptide": c.peptide, "round": c.round_name,
                     "membrane": c.membrane,
                     "copies": c.copies, "oligomer_size": c.oligomer_size,
                     "replicas": campaign.replicas, "built": built, "minimized": minimized,
                     "equilibrated": equilibrated, "assembled": assembled, "failed": failed,
                     "ns_per_day": round(sum(speeds) / len(speeds), 1) if speeds else None})
    return rows
