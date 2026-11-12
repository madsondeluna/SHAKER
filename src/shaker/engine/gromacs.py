"""Take one replica through its GROMACS stages, resumable at any point.

Stages, in order: em (minimization), eq1..eqN (time-step ramp), assembly (self-assembly with
isotropic pressure coupling). A stage is done when its final .gro exists; mdrun writes that
file only on normal completion. A stage with a checkpoint and no .gro is continued from the
checkpoint. So running a replica twice never repeats finished work, and a replica cut by a
wall-time limit or a node failure picks up where it stopped.

Production in a semi-isotropic box comes after the bilayer normal is known; that step belongs
to the analysis module and is not part of this file.
"""

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .. import MDP_DIR, seeds
from ..events import EventLog

STATE_FILE = "run.json"
EM_TOLERANCE = 1000  # kJ/mol/nm, the emtol of em.mdp


class EngineError(RuntimeError):
    pass


def gmx_executable() -> str:
    override = os.environ.get("SHAKER_GMX")
    found = override or shutil.which("gmx") or shutil.which("gmx_mpi")
    if not found:
        raise EngineError("GROMACS not found: no `gmx` or `gmx_mpi` on PATH, SHAKER_GMX unset")
    return found


def stage_names(simulation: dict) -> list:
    return ["em"] + [f"eq{i}" for i in range(1, len(simulation["equilibration"]) + 1)] + ["assembly"]


def _md_mdp(dt, nsteps, temperature, frame_ps, first, gen_seed) -> str:
    every = max(1, min(nsteps, int(round(frame_ps / dt))))
    energy = max(1, min(nsteps, every // 10 or 1))
    text = (MDP_DIR / "md.mdp").read_text()
    for key, value in {
        "@DT@": dt, "@NSTEPS@": nsteps, "@TEMPERATURE@": temperature,
        "@NSTXOUT@": every, "@NSTENERGY@": energy,
        "@GEN_VEL@": "yes" if first else "no",
        "@CONTINUATION@": "no" if first else "yes",
        "@GEN_SEED@": gen_seed,
    }.items():
        text = text.replace(key, str(value))
    return text


def stage_plan(simulation: dict, seed: int) -> list:
    """[(stage name, mdp text)] for a replica."""
    gen_seed = seeds.gromacs_seed(seed)
    temperature = simulation["temperature"]
    frame_ps = simulation["frame_ps"]
    plan = [("em", (MDP_DIR / "em.mdp").read_text())]
    for i, (dt, nsteps) in enumerate(simulation["equilibration"], start=1):
        plan.append((f"eq{i}", _md_mdp(dt, int(nsteps), temperature, frame_ps, i == 1, gen_seed)))
    dt = simulation["timestep_ps"]
    nsteps = int(round(simulation["assembly_ns"] * 1000 / dt))
    plan.append(("assembly", _md_mdp(dt, nsteps, temperature, frame_ps, False, gen_seed)))
    return plan


def read_state(directory) -> dict:
    path = Path(directory) / STATE_FILE
    if not path.exists():
        return {"status": "pending", "stages": {}}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"status": "pending", "stages": {}}


def _write_state(directory, state) -> None:
    path = Path(directory) / STATE_FILE
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    temporary.replace(path)  # atomic: a crash never leaves half a file


def _fatal_message(text: str) -> str:
    """The reason GROMACS gave, in one line."""
    match = re.search(r"Fatal error:\s*\n(.*?)\n\s*\n", text, re.S)
    if match and "Too many warnings" in match.group(1):
        # The useful part is the warning itself, not the count.
        warning = re.search(r"WARNING \d+ \[.*?\]:\s*\n(.*?)\n\s*\n", text, re.S)
        if warning:
            return "warning: " + " ".join(warning.group(1).split())[:300]
    if match:
        return " ".join(match.group(1).split())[:300]
    for marker in ("LINCS", "Segmentation fault", "nan", "Too many"):
        for line in text.splitlines():
            if marker in line:
                return line.strip()[:300]
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return (lines[-1] if lines else "no output")[:300]


def _performance(log_path: Path):
    if not log_path.exists():
        return None
    match = re.search(r"^Performance:\s+(\S+)", log_path.read_text(errors="replace"), re.M)
    try:
        return float(match.group(1)) if match else None
    except ValueError:
        return None


def _run(command, cwd, log_name, append=False):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    with open(Path(cwd) / log_name, "a" if append else "w") as handle:
        handle.write(f"$ {' '.join(command)}\n{result.stdout}\n{result.stderr}\n")
    return result


def run_replica(directory, seed: int, simulation: dict, until: str = None, threads: int = None,
                mdrun_args=(), maxh: float = None, retries: int = 1, context: dict = None) -> dict:
    """Run the stages of one replica up to `until`. Returns the state written to run.json.

    Status of the replica: done (reached `until`), incomplete (stopped cleanly before the end,
    for example by -maxh), failed (a stage could not finish; `reason` says why).
    """
    directory = Path(directory)
    names = stage_names(simulation)
    last = until or names[-1]
    if last not in names:
        raise EngineError(f"unknown stage '{last}'. Valid: {', '.join(names)}")
    gmx = gmx_executable()
    log = EventLog(directory, **(context or {}))
    state = read_state(directory)
    state.setdefault("stages", {})
    state.update(status="running", reason=None, target=last, gromacs=gmx)
    _write_state(directory, state)

    previous = "box"
    previous_is_md = False
    for name, mdp in stage_plan(simulation, seed):
        record = state["stages"].setdefault(name, {})
        gro = directory / f"{name}.gro"
        if not gro.exists():
            started = time.monotonic()
            log.write(name, status="started")
            outcome, detail = _run_stage(directory, gmx, name, mdp, previous, previous_is_md,
                                         threads, mdrun_args, maxh, retries)
            seconds = round(record.get("seconds", 0) + time.monotonic() - started, 2)
            record.update(status=outcome, seconds=seconds)
            if outcome != "done":
                record["reason"] = detail
                state.update(status=outcome, reason=f"{name}: {detail}")
                _write_state(directory, state)
                log.write(name, status=outcome, seconds=seconds, message=detail)
                return state
            record.pop("reason", None)
            if name == "em":
                warning = _em_warning(directory)
                if warning:
                    record["warning"] = warning
                    log.write("em_check", status="warning", message=warning)
            else:
                record["ns_per_day"] = _performance(directory / f"{name}.log")
                record.update(_trajectory_size(directory, name))
            log.write(name, status="ok", seconds=seconds, ns_per_day=record.get("ns_per_day"))
        else:
            record.setdefault("status", "done")
        _write_state(directory, state)
        previous, previous_is_md = name, name != "em"
        if name == last:
            break

    state.update(status="done", reason=None)
    _write_state(directory, state)
    return state


def _run_stage(directory, gmx, name, mdp, previous, previous_is_md, threads, mdrun_args, maxh,
               retries):
    """Return ('done' | 'incomplete' | 'failed', detail)."""
    tpr = directory / f"{name}.tpr"
    if not tpr.exists():
        if name == "em" and not _uses_flexible(directory):
            # -DFLEXIBLE turns the peptide constraints into bonds for minimization. A box
            # without a peptide has nothing that reads the macro, and grompp warns about it.
            mdp = "\n".join(l for l in mdp.splitlines() if not l.startswith("define")) + "\n"
        (directory / f"{name}.mdp").write_text(mdp)
        command = [gmx, "-quiet", "-nocopyright", "grompp", "-f", f"{name}.mdp",
                   "-c", f"{previous}.gro", "-p", "topol.top", "-o", f"{name}.tpr",
                   "-po", f"{name}_mdout.mdp", "-maxwarn", "0"]
        if previous_is_md and (directory / f"{previous}.cpt").exists():
            command += ["-t", f"{previous}.cpt"]
        result = _run(command, directory, f"{name}_grompp.log")
        if result.returncode != 0 or not tpr.exists():
            return "failed", "grompp: " + _fatal_message(result.stdout + result.stderr)

    detail = ""
    for attempt in range(max(1, retries + 1)):
        command = [gmx, "-quiet", "-nocopyright", "mdrun", "-deffnm", name]
        if (directory / f"{name}.cpt").exists():
            command += ["-cpi", f"{name}.cpt"]
        if threads:
            command += ["-nt", str(threads)]
        if maxh and name != "em":
            command += ["-maxh", str(maxh)]
        command += list(mdrun_args)
        result = _run(command, directory, f"{name}_mdrun.log", append=attempt > 0)
        if (directory / f"{name}.gro").exists() and result.returncode == 0:
            return "done", ""
        if result.returncode == 0:
            return "incomplete", "stopped before the last step (wall-time limit); run again to continue"
        detail = "mdrun: " + _fatal_message(result.stdout + result.stderr)
        if not (directory / f"{name}.cpt").exists():
            break  # nothing to resume from: the same input would fail the same way
    return "failed", detail


def _trajectory_size(directory, name) -> dict:
    """Frames written and bytes on disk for a finished dynamics stage."""
    settings = dict(
        re.findall(r"^(nsteps|nstxout-compressed)\s*=\s*(\d+)",
                   (directory / f"{name}.mdp").read_text(), re.M))
    trajectory = directory / f"{name}.xtc"
    if len(settings) < 2 or not trajectory.exists():
        return {}
    frames = int(settings["nsteps"]) // int(settings["nstxout-compressed"]) + 1
    return {"frames": frames, "trajectory_bytes": trajectory.stat().st_size}


def _uses_flexible(directory) -> bool:
    """True if a molecule definition local to the replica reads the FLEXIBLE macro."""
    return any("FLEXIBLE" in path.read_text(errors="replace") for path in directory.glob("*.itp"))


def _em_warning(directory):
    text = (directory / "em_mdrun.log").read_text(errors="replace")
    if "converged to Fmax" in text and "did not converge" not in text:
        return None
    force = re.search(r"Maximum force\s+=\s+(\S+)", text)
    return (f"minimization did not reach Fmax < {EM_TOLERANCE} kJ/mol/nm"
            + (f" (Fmax {float(force.group(1)):.0f})" if force else ""))
