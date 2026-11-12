"""Coarse-grain a peptide with martinize2 and check what came out."""

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..itp import MoleculeType, read_itp
from .helix import internal_prolines, validate_sequence, write_ideal_helix

FORCE_FIELD = "martini3001"
POSITIVE = set("KR")
NEGATIVE = set("DE")


class PrepareError(RuntimeError):
    pass


@dataclass
class Peptide:
    name: str
    sequence: str
    secondary_structure: str
    c_terminus: str
    molecule: MoleculeType
    coords: np.ndarray  # nm, one row per bead, centred on the origin
    itp: Path
    warnings: list

    @property
    def charge(self) -> float:
        return self.molecule.charge

    @property
    def n_beads(self) -> int:
        return self.molecule.n_atoms


def expected_charge(sequence: str, c_terminus: str = "free") -> int:
    """Net charge at pH 7 as Martini 3 assigns it: Lys/Arg +1, Asp/Glu -1, His neutral."""
    charge = sum(aa in POSITIVE for aa in sequence) - sum(aa in NEGATIVE for aa in sequence)
    charge += 1  # N-terminus
    if c_terminus == "free":
        charge -= 1
    return charge


def secondary_structure_string(spec: str, length: int) -> str:
    """Expand 'helix' / 'coil' or validate an explicit DSSP string."""
    if spec == "helix":
        return "H" * length
    if spec == "coil":
        return "C" * length
    if len(spec) != length:
        raise ValueError(
            f"secondary structure string has {len(spec)} letters, sequence has {length} residues"
        )
    return spec.upper()


def _martinize_executable() -> str:
    beside_python = Path(sys.executable).parent / "martinize2"
    if beside_python.exists():
        return str(beside_python)
    found = shutil.which("martinize2")
    if found is None:
        raise PrepareError("martinize2 not found. Install vermouth in this environment.")
    return found


def _read_cg_pdb(path) -> np.ndarray:
    coords = []
    for line in Path(path).read_text().splitlines():
        if line.startswith(("ATOM", "HETATM")):
            coords.append((float(line[30:38]), float(line[38:46]), float(line[46:54])))
    return np.array(coords) / 10.0  # angstrom to nm


def prepare_peptide(
    name: str,
    sequence: str,
    outdir,
    secondary_structure: str = "helix",
    c_terminus: str = "free",
) -> Peptide:
    """Build the ideal helix, run martinize2 and return the coarse-grained peptide.

    Files written to outdir: helix.pdb, cg.pdb, <name>.itp, martinize2.log, command.txt.
    """
    if c_terminus not in ("free", "amidated"):
        raise ValueError("c_terminus must be 'free' or 'amidated'")
    sequence = validate_sequence(sequence)
    ss = secondary_structure_string(secondary_structure, len(sequence))
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    warnings = []
    prolines = internal_prolines(sequence)
    if prolines and set(ss) == {"H"}:
        warnings.append(
            f"proline at position(s) {', '.join(map(str, prolines))} inside a full helix: "
            "an ideal helix ignores the kink. Consider an explicit secondary_structure string."
        )

    write_ideal_helix(sequence, outdir / "helix.pdb")

    command = [
        _martinize_executable(),
        "-f", "helix.pdb",
        "-ff", FORCE_FIELD,
        "-x", "cg.pdb",
        "-o", "martinize.top",
        "-ss", ss,
        "-name", name,
    ]
    if c_terminus == "amidated":
        command += ["-cter", "NH2-ter"]
    (outdir / "command.txt").write_text(" ".join(command) + "\n")

    result = subprocess.run(command, cwd=outdir, capture_output=True, text=True)
    (outdir / "martinize2.log").write_text(result.stdout + "\n" + result.stderr)
    produced = sorted(p for p in outdir.glob("*.itp"))
    if result.returncode != 0 or not produced or not (outdir / "cg.pdb").exists():
        raise PrepareError(
            f"martinize2 failed for '{name}' (exit {result.returncode}). See {outdir / 'martinize2.log'}"
        )

    # martinize2 names the file <name>_0.itp; keep one stable name.
    itp = outdir / f"{name}.itp"
    if produced[0] != itp:
        produced[0].replace(itp)

    molecules = read_itp(itp)
    if len(molecules) != 1:
        raise PrepareError(f"expected one molecule type in {itp}, found {len(molecules)}")
    molecule = next(iter(molecules.values()))

    coords = _read_cg_pdb(outdir / "cg.pdb")
    if len(coords) != molecule.n_atoms:
        raise PrepareError(
            f"cg.pdb has {len(coords)} beads but {itp.name} has {molecule.n_atoms} atoms"
        )

    expected = expected_charge(sequence, c_terminus)
    if abs(molecule.charge - expected) > 1e-6:
        raise PrepareError(
            f"net charge of '{name}' is {molecule.charge:+g} in the itp, "
            f"{expected:+d} expected from the sequence and termini"
        )

    return Peptide(
        name=molecule.name,
        sequence=sequence,
        secondary_structure=ss,
        c_terminus=c_terminus,
        molecule=molecule,
        coords=coords - coords.mean(axis=0),
        itp=itp,
        warnings=warnings,
    )


MARKER = "prepared.json"


def _fingerprint(name, sequence, secondary_structure, c_terminus) -> dict:
    import importlib.metadata

    return {
        "name": name,
        "sequence": sequence,
        "secondary_structure": secondary_structure,
        "c_terminus": c_terminus,
        "force_field": FORCE_FIELD,
        "vermouth": importlib.metadata.version("vermouth"),
    }


def load_or_prepare(name, sequence, outdir, secondary_structure="helix", c_terminus="free",
                    force=False):
    """Return (Peptide, reused). Re-runs martinize2 only if the inputs or its version changed."""
    import json

    outdir = Path(outdir)
    sequence = validate_sequence(sequence)
    fingerprint = _fingerprint(name, sequence, secondary_structure, c_terminus)
    marker = outdir / MARKER
    itp = outdir / f"{name}.itp"
    if not force and marker.exists() and itp.exists() and (outdir / "cg.pdb").exists():
        if json.loads(marker.read_text()).get("inputs") == fingerprint:
            molecule = next(iter(read_itp(itp).values()))
            coords = _read_cg_pdb(outdir / "cg.pdb")
            ss = secondary_structure_string(secondary_structure, len(sequence))
            prolines = internal_prolines(sequence)
            warnings = []
            if prolines and set(ss) == {"H"}:
                warnings.append(
                    f"proline at position(s) {', '.join(map(str, prolines))} inside a full helix"
                )
            return Peptide(molecule.name, sequence, ss, c_terminus, molecule,
                           coords - coords.mean(axis=0), itp, warnings), True
    peptide = prepare_peptide(name, sequence, outdir, secondary_structure, c_terminus)
    marker.write_text(json.dumps({"inputs": fingerprint, "charge": peptide.charge,
                                  "beads": peptide.n_beads}, indent=2) + "\n")
    return peptide, False
