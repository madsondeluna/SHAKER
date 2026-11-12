"""Local lipid library: every lipid of the bundled Martini 3 files, with a coordinate template."""

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import numpy as np

from .. import FORCEFIELD_DIR
from ..itp import MoleculeType, read_itp
from .templates import MIN_SEPARATION, min_separation, template_from_bonds

LIPID_FILES = ("martini_v3.0.0_phospholipids_v1.itp",)

HEAD_GROUPS = {
    "PC": "phosphatidylcholine",
    "PE": "phosphatidylethanolamine",
    "PS": "phosphatidylserine",
    "PA": "phosphatidic acid",
    "PG": "phosphatidylglycerol",
}


@dataclass(frozen=True)
class Lipid:
    name: str
    molecule: MoleculeType
    source: str  # itp file name

    @property
    def head(self) -> str:
        return self.name[-2:] if self.name[-2:] in HEAD_GROUPS else "other"

    @property
    def charge(self) -> float:
        return self.molecule.charge

    @property
    def n_beads(self) -> int:
        return self.molecule.n_atoms

    @property
    def bead_names(self) -> list:
        return [a.name for a in self.molecule.atoms]

    @cached_property
    def template(self) -> np.ndarray:
        return template_from_bonds(self.molecule.n_atoms, self.molecule.bonds)


class LipidLibrary:
    """Lipids available to the builder, read from the itp files shipped with the package."""

    def __init__(self, lipids: dict):
        self._lipids = lipids

    @classmethod
    def load(cls, forcefield_dir=FORCEFIELD_DIR, extra_itps=()):
        lipids = {}
        files = [Path(forcefield_dir) / f for f in LIPID_FILES] + [Path(p) for p in extra_itps]
        for path in files:
            for name, molecule in read_itp(path).items():
                lipids[name] = Lipid(name=name, molecule=molecule, source=path.name)
        return cls(lipids)

    def names(self) -> list:
        return sorted(self._lipids)

    def __contains__(self, name) -> bool:
        return name in self._lipids

    def __len__(self) -> int:
        return len(self._lipids)

    def get(self, name: str) -> Lipid:
        try:
            return self._lipids[name]
        except KeyError:
            raise KeyError(
                f"lipid '{name}' is not in the library. "
                f"Run 'shaker lipids list' to see the {len(self._lipids)} available."
            ) from None

    def validate(self) -> list:
        """Return a list of problems; empty when every template is usable."""
        problems = []
        for name in self.names():
            lipid = self._lipids[name]
            try:
                template = lipid.template
            except ValueError as exc:
                problems.append(f"{name}: {exc}")
                continue
            if len(template) != lipid.n_beads:
                problems.append(f"{name}: template has {len(template)} beads, itp has {lipid.n_beads}")
            if min_separation(template) < MIN_SEPARATION:
                problems.append(f"{name}: beads closer than {MIN_SEPARATION} nm in the template")
        return problems
