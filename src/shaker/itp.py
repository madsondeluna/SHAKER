"""Minimal reader for GROMACS itp files: molecule types, their atoms and their bonded graph."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Atom:
    index: int  # 0-based
    type: str
    resnr: int
    resname: str
    name: str
    charge: float


@dataclass
class MoleculeType:
    name: str
    atoms: list = field(default_factory=list)
    bonds: list = field(default_factory=list)  # 0-based pairs, bonds and constraints together

    @property
    def charge(self) -> float:
        return round(sum(a.charge for a in self.atoms), 6)

    @property
    def n_atoms(self) -> int:
        return len(self.atoms)


def read_itp(path) -> dict:
    """Return {name: MoleculeType} for every molecule type in the file.

    Preprocessor lines are skipped, so bonds listed under both branches of an #ifdef are read
    twice. That is harmless here: the bonds are only used as a connectivity graph.
    """
    molecules = {}
    current = None
    section = None
    expect_name = False
    for raw in Path(path).read_text().splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            section = line.strip("[] \t").lower()
            expect_name = section == "moleculetype"
            continue
        fields = line.split()
        if expect_name:
            current = MoleculeType(name=fields[0])
            molecules[current.name] = current
            expect_name = False
        elif current is None:
            continue
        elif section == "atoms":
            current.atoms.append(
                Atom(
                    index=len(current.atoms),
                    type=fields[1],
                    resnr=int(fields[2]),
                    resname=fields[3],
                    name=fields[4],
                    charge=float(fields[6]) if len(fields) > 6 else 0.0,
                )
            )
        elif section in ("bonds", "constraints"):
            i, j = int(fields[0]) - 1, int(fields[1]) - 1
            current.bonds.append((min(i, j), max(i, j)))
    return molecules
