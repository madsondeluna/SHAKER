"""Build one randomly mixed box: peptides, lipids, water and ions, from a single seed."""

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .. import FORCEFIELD_DIR, __version__, seeds
from ..gro import write_gro
from ..lipids import LipidLibrary
from ..peptide.martinize import Peptide
from ..peptide.oligomer import build_oligomer
from .placer import Placer
from .solvent import free_sites
from .topology import FORCEFIELD_INCLUDES, write_topology

WATERS_PER_BEAD = 4  # one Martini W bead stands for four water molecules
WATER_MOLARITY = 55.5  # mol/L


@dataclass
class SystemSpec:
    """Everything that defines a box, apart from the seed."""

    lipids: dict  # {lipid name: count}
    water_beads_per_lipid: float = 15.0
    salt_molar: float = 0.15
    peptide: Peptide = None
    copies: int = 0
    oligomer_size: int = 1
    arrangement: str = "antiparallel"
    hydrophobic_face: str = "inward"
    axis_distance: float = 1.0
    oligomer_fraction: float = 1.0
    bead_volume: float = 0.125  # nm^3 per bead, sets the starting box volume
    min_distance: float = 0.35  # nm between beads of different solute molecules

    def validate(self) -> None:
        if not self.lipids or any(n < 0 for n in self.lipids.values()):
            raise ValueError("lipids must map names to non-negative counts")
        if sum(self.lipids.values()) == 0:
            raise ValueError("the box needs at least one lipid")
        if self.copies < 0 or self.oligomer_size < 1:
            raise ValueError("copies must be >= 0 and oligomer_size >= 1")
        if self.copies > 0 and self.peptide is None:
            raise ValueError("copies > 0 but no peptide was given")
        if not 0.0 <= self.oligomer_fraction <= 1.0:
            raise ValueError("oligomer_fraction must be between 0 and 1")
        if self.water_beads_per_lipid <= 0 or self.salt_molar < 0:
            raise ValueError("water_beads_per_lipid must be > 0 and salt_molar >= 0")


@dataclass
class BuildResult:
    gro: Path
    top: Path
    manifest: Path
    box: np.ndarray
    molecules: list  # ordered (name, count)
    net_charge: float
    info: dict = field(default_factory=dict)


def _ion_counts(net_solute_charge: float, salt_molar: float, n_solvent_sites: int):
    """Sodium and chloride counts: salt pairs for the concentration, plus counterions."""
    charge = int(round(net_solute_charge))
    if abs(net_solute_charge - charge) > 1e-6:
        raise ValueError(f"solute net charge {net_solute_charge} is not an integer")
    pairs = int(round(salt_molar * n_solvent_sites * WATERS_PER_BEAD / WATER_MOLARITY))
    return pairs + max(0, -charge), pairs + max(0, charge)


def build_system(
    spec: SystemSpec,
    seed: int,
    outdir,
    forcefield_dir=FORCEFIELD_DIR,
    library: LipidLibrary = None,
    title: str = "SHAKER random mixture",
) -> BuildResult:
    """Write box.gro, topol.top and manifest.json for one replica into outdir."""
    spec.validate()
    library = library or LipidLibrary.load(forcefield_dir)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    rng = seeds.rng(seed, "builder")

    lipid_counts = {name: n for name, n in spec.lipids.items() if n > 0}
    lipids = {name: library.get(name) for name in lipid_counts}
    n_lipids = sum(lipid_counts.values())

    # Peptide units: multimers first, the remaining copies as monomers.
    n_oligomers = 0
    if spec.copies and spec.oligomer_size > 1:
        n_oligomers = int(spec.copies * spec.oligomer_fraction) // spec.oligomer_size
    n_monomers = spec.copies - n_oligomers * spec.oligomer_size
    axis_distance_used = 0.0
    oligomer_template = None
    if n_oligomers:
        oligomer_template, axis_distance_used = build_oligomer(
            spec.peptide.coords,
            spec.peptide.molecule.atoms,
            spec.oligomer_size,
            spec.arrangement,
            spec.hydrophobic_face,
            spec.axis_distance,
        )

    n_solvent = int(round(spec.water_beads_per_lipid * n_lipids))
    peptide_beads = spec.copies * (spec.peptide.n_beads if spec.peptide else 0)
    lipid_beads = sum(lipids[name].n_beads * n for name, n in lipid_counts.items())
    total_beads = peptide_beads + lipid_beads + n_solvent
    box = np.full(3, (spec.bead_volume * total_beads) ** (1.0 / 3.0))

    placer = Placer(box, spec.min_distance, rng)

    # Peptides go in first (largest bodies), then lipids in a shuffled order so no lipid type
    # is systematically inserted into a more crowded box than another.
    peptide_coords = []
    for _ in range(n_oligomers):
        sizes = [len(c) for c in oligomer_template]
        placed = placer.insert(np.concatenate(oligomer_template))
        peptide_coords.extend(np.split(placed, np.cumsum(sizes)[:-1]))
    for _ in range(n_monomers):
        peptide_coords.append(placer.insert(spec.peptide.coords))

    lipid_order = [name for name, n in lipid_counts.items() for _ in range(n)]
    rng.shuffle(lipid_order)
    lipid_coords = {name: [] for name in lipid_counts}
    for name in lipid_order:
        lipid_coords[name].append(placer.insert(lipids[name].template))

    solute_charge = spec.copies * (spec.peptide.charge if spec.peptide else 0.0) + sum(
        lipids[name].charge * n for name, n in lipid_counts.items()
    )
    n_na, n_cl = _ion_counts(solute_charge, spec.salt_molar, n_solvent)
    if n_na + n_cl > n_solvent:
        raise ValueError("more ions than solvent sites; raise water_beads_per_lipid")
    n_water = n_solvent - n_na - n_cl
    sites, clearance = free_sites(box, placer.points, n_solvent, rng)

    # Assemble coordinates in topology order.
    resnrs, resnames, names, coords = [], [], [], []
    resnr = 0
    for xyz in peptide_coords:
        first = spec.peptide.molecule.atoms[0].resnr
        for atom, position in zip(spec.peptide.molecule.atoms, xyz):
            resnrs.append(resnr + atom.resnr - first + 1)
            resnames.append(atom.resname)
            names.append(atom.name)
            coords.append(position)
        resnr = resnrs[-1]
    for name in lipid_counts:
        for xyz in lipid_coords[name]:
            resnr += 1
            for atom, position in zip(lipids[name].molecule.atoms, xyz):
                resnrs.append(resnr)
                resnames.append(atom.resname)
                names.append(atom.name)
                coords.append(position)
    for label, resname, block in (
        ("W", "W", sites[:n_water]),
        ("NA", "ION", sites[n_water : n_water + n_na]),
        ("CL", "ION", sites[n_water + n_na :]),
    ):
        for position in block:
            resnr += 1
            resnrs.append(resnr)
            resnames.append(resname)
            names.append(label)
            coords.append(position)

    molecules = []
    if spec.copies:
        molecules.append((spec.peptide.name, spec.copies))
    molecules += [(name, n) for name, n in lipid_counts.items()]
    molecules += [("W", n_water), ("NA", n_na), ("CL", n_cl)]
    net_charge = solute_charge + n_na - n_cl

    gro = outdir / "box.gro"
    write_gro(gro, title, resnrs, resnames, names, np.array(coords), box)

    extra_itps = []
    if spec.copies:
        local_itp = outdir / spec.peptide.itp.name
        if spec.peptide.itp.resolve() != local_itp.resolve():
            shutil.copyfile(spec.peptide.itp, local_itp)
        extra_itps.append(local_itp)
    top = write_topology(outdir / "topol.top", title, molecules, forcefield_dir, extra_itps)

    info = {
        "shaker_version": __version__,
        "seed": int(seed),
        "builder_seed": seeds.derive(seed, "builder"),
        "box_nm": [round(float(v), 5) for v in box],
        "total_beads": len(coords),
        "molecules": {name: count for name, count in molecules},
        "net_charge": net_charge,
        "solute_charge": solute_charge,
        "peptide": None
        if not spec.copies
        else {
            "name": spec.peptide.name,
            "sequence": spec.peptide.sequence,
            "secondary_structure": spec.peptide.secondary_structure,
            "c_terminus": spec.peptide.c_terminus,
            "charge": spec.peptide.charge,
            "copies": spec.copies,
            "oligomers": n_oligomers,
            "oligomer_size": spec.oligomer_size if n_oligomers else 1,
            "monomers": n_monomers,
            "arrangement": spec.arrangement if n_oligomers else None,
            "hydrophobic_face": spec.hydrophobic_face if n_oligomers else None,
            "axis_distance_requested": spec.axis_distance if n_oligomers else None,
            "axis_distance_used": axis_distance_used if n_oligomers else None,
        },
        "parameters": {
            "water_beads_per_lipid": spec.water_beads_per_lipid,
            "salt_molar": spec.salt_molar,
            "bead_volume": spec.bead_volume,
            "min_distance": spec.min_distance,
            "solvent_clearance_used": clearance,
        },
        "insertion_attempts": placer.tries,
        "forcefield_files": list(FORCEFIELD_INCLUDES),
    }
    manifest = outdir / "manifest.json"
    manifest.write_text(json.dumps(info, indent=2) + "\n")

    return BuildResult(gro, top, manifest, box, molecules, net_charge, info)
