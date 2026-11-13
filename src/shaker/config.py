"""Campaign configuration: one YAML file, expanded into conditions.

A condition is one combination of the controlled variables. Any key under `conditions` may be
a single value or a list; lists are expanded as a full factorial grid. Replicas of a condition
differ only in their seed.
"""

import hashlib
import itertools
import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import mdp
from .peptide.helix import validate_sequence

NO_PEPTIDE = "none"

CONDITION_DEFAULTS = {
    "peptide": NO_PEPTIDE,
    "membrane": None,
    "copies": 0,
    "oligomer_size": 1,
    "arrangement": "antiparallel",
    "hydrophobic_face": "inward",
    "axis_distance": 1.0,
    "oligomer_fraction": 1.0,
    "water_beads_per_lipid": 15,
    "salt_molar": 0.15,
}
CHOICES = {
    "arrangement": ("parallel", "antiparallel"),
    "hydrophobic_face": ("inward", "outward"),
}
SIMULATION_DEFAULTS = mdp.SCHEDULE_DEFAULTS  # `simulation:` of config.yaml, kept for older campaigns
PEPTIDE_DEFAULTS = {"secondary_structure": "helix", "c_terminus": "free"}
TOP_LEVEL_KEYS = {"master_seed", "replicas", "peptides", "membranes", "conditions", "simulation"}

EXAMPLE = """\
# SHAKER campaign. Lists under `conditions` are expanded into a full grid.
master_seed: 20261112
replicas: 100

peptides:
  mag2:
    sequence: GIGKFLHSAKKFGKAFVGEIMNS
    secondary_structure: helix      # helix, coil, or one DSSP letter per residue
    c_terminus: free                # free or amidated

membranes:
  bacterial: {POPE: 96, POPG: 32}
  mammalian: {POPC: 128}

conditions:
  peptide: [mag2, none]             # `none` is the peptide-free control
  membrane: bacterial
  copies: 6
  oligomer_size: [1, 6]             # 1 starts from monomers
  arrangement: antiparallel         # parallel or antiparallel
  hydrophobic_face: inward          # inward or outward
  axis_distance: 1.0                # nm between neighbouring helix axes
  oligomer_fraction: 1.0            # fraction of copies that start in multimers
  water_beads_per_lipid: 15         # one bead is four water molecules
  salt_molar: 0.15
"""


class ConfigError(ValueError):
    """The configuration file is missing, malformed or inconsistent."""


@dataclass(frozen=True)
class PeptideDef:
    name: str
    sequence: str
    secondary_structure: str
    c_terminus: str


@dataclass(frozen=True)
class Condition:
    id: str
    peptide: str
    membrane: str
    lipids: tuple  # ((name, count), ...)
    copies: int
    oligomer_size: int
    arrangement: str
    hydrophobic_face: str
    axis_distance: float
    oligomer_fraction: float
    water_beads_per_lipid: float
    salt_molar: float

    @property
    def n_lipids(self) -> int:
        return sum(n for _, n in self.lipids)

    @property
    def round_name(self) -> str:
        """What the box holds at time zero: `6 monomers`, `1 hexamer`, `2 dimers + 2 monomers`."""
        if self.peptide == NO_PEPTIDE or not self.copies:
            return "lipids only"
        names = ("", "monomer", "dimer", "trimer", "tetramer", "pentamer", "hexamer", "heptamer",
                 "octamer", "nonamer", "decamer")

        def count(size, k):
            noun = names[size] if size < len(names) else f"{size}-mer"
            return f"{k} {noun}{'' if k == 1 else 's'}"

        size = self.oligomer_size or 1
        if size == 1:
            return count(1, self.copies)
        fraction = 1.0 if self.oligomer_fraction is None else self.oligomer_fraction
        k = round(self.copies * fraction / size)
        free = self.copies - k * size
        return count(size, k) + (f" + {count(1, free)}" if free > 0 else "")

    def as_row(self) -> dict:
        row = {k: getattr(self, k) for k in (
            "id", "peptide", "membrane", "copies", "oligomer_size", "arrangement",
            "hydrophobic_face", "axis_distance", "oligomer_fraction",
            "water_beads_per_lipid", "salt_molar")}
        row["lipids"] = " ".join(f"{name}:{n}" for name, n in self.lipids)
        row["n_lipids"] = self.n_lipids
        row["round"] = self.round_name
        return row


@dataclass(frozen=True)
class Campaign:
    root: Path
    path: Path
    master_seed: int
    replicas: int
    peptides: dict  # {name: PeptideDef}
    membranes: dict  # {name: {lipid: count}}
    conditions: tuple
    simulation: dict

    def used_peptides(self) -> list:
        names = sorted({c.peptide for c in self.conditions if c.peptide != NO_PEPTIDE})
        return [self.peptides[n] for n in names]

    def condition(self, condition_id: str) -> Condition:
        for c in self.conditions:
            if c.id == condition_id:
                return c
        raise ConfigError(f"no condition with id '{condition_id}'")


def _slug(value) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "-", str(value)).strip("-")


def _as_list(value) -> list:
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _require(condition, message):
    if not condition:
        raise ConfigError(message)


def _canonical(values: dict) -> dict:
    """Drop the settings that have no effect, so equivalent conditions collapse into one."""
    values = dict(values)
    if values["peptide"] == NO_PEPTIDE or values["copies"] == 0:
        values.update(peptide=NO_PEPTIDE, copies=0, oligomer_size=1)
    if values["oligomer_size"] == 1:
        values.update(arrangement=None, hydrophobic_face=None, axis_distance=None,
                      oligomer_fraction=None)
    return values


def _condition_id(values: dict, peptide: PeptideDef, lipids: dict) -> str:
    payload = dict(values)
    payload["peptide_def"] = None if peptide is None else [
        peptide.sequence, peptide.secondary_structure, peptide.c_terminus]
    payload["lipids"] = sorted(lipids.items())
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:8]
    return "__".join([
        _slug(values["peptide"]),
        _slug(values["membrane"]),
        f"n{values['copies']}",
        f"o{values['oligomer_size']}",
        digest,
    ])


def load_campaign(path, library=None) -> Campaign:
    """Read and validate a campaign file. Raises ConfigError with a message naming the key."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from None
    _require(isinstance(data, dict), f"{path}: the top level must be a mapping")

    unknown = sorted(set(data) - TOP_LEVEL_KEYS)
    _require(not unknown, f"unknown key(s) in {path.name}: {', '.join(unknown)}. "
             f"Valid keys: {', '.join(sorted(TOP_LEVEL_KEYS))}")

    master_seed = data.get("master_seed")
    _require(isinstance(master_seed, int), "master_seed must be an integer")
    replicas = data.get("replicas", 1)
    _require(isinstance(replicas, int) and replicas >= 1, "replicas must be an integer >= 1")

    peptides = {}
    for name, body in (data.get("peptides") or {}).items():
        _require(isinstance(body, dict) and "sequence" in body,
                 f"peptides.{name} needs a 'sequence'")
        _require(re.fullmatch(r"[A-Za-z0-9]+", str(name)) and str(name).lower() != NO_PEPTIDE,
                 f"peptide name '{name}' must be letters and digits, and not '{NO_PEPTIDE}'")
        extra = sorted(set(body) - {"sequence", *PEPTIDE_DEFAULTS})
        _require(not extra, f"unknown key(s) in peptides.{name}: {', '.join(extra)}")
        try:
            sequence = validate_sequence(str(body["sequence"]))
        except ValueError as exc:
            raise ConfigError(f"peptides.{name}: {exc}") from None
        c_terminus = body.get("c_terminus", PEPTIDE_DEFAULTS["c_terminus"])
        _require(c_terminus in ("free", "amidated"),
                 f"peptides.{name}.c_terminus must be 'free' or 'amidated'")
        ss = str(body.get("secondary_structure", PEPTIDE_DEFAULTS["secondary_structure"]))
        _require(ss in ("helix", "coil") or len(ss) == len(sequence),
                 f"peptides.{name}.secondary_structure must be 'helix', 'coil' or "
                 f"{len(sequence)} letters")
        peptides[str(name)] = PeptideDef(str(name), sequence, ss, c_terminus)

    membranes = {}
    for name, body in (data.get("membranes") or {}).items():
        _require(isinstance(body, dict) and body, f"membranes.{name} must map lipids to counts")
        for lipid, count in body.items():
            _require(isinstance(count, int) and count >= 0,
                     f"membranes.{name}.{lipid} must be a non-negative integer")
            if library is not None:
                _require(lipid in library, f"membranes.{name}: lipid '{lipid}' is not in the "
                         "library. Run `shaker lipids list`.")
        _require(sum(body.values()) > 0, f"membranes.{name} has no lipids")
        membranes[str(name)] = {str(k): int(v) for k, v in body.items()}
    _require(membranes, "at least one entry under 'membranes' is required")

    grid = data.get("conditions") or {}
    _require(isinstance(grid, dict), "'conditions' must be a mapping")
    extra = sorted(set(grid) - set(CONDITION_DEFAULTS))
    _require(not extra, f"unknown key(s) under conditions: {', '.join(extra)}. "
             f"Valid keys: {', '.join(CONDITION_DEFAULTS)}")
    _require("membrane" in grid, "conditions.membrane is required")

    axes = {key: _as_list(grid.get(key, default)) for key, default in CONDITION_DEFAULTS.items()}
    for key, values in axes.items():
        _require(values, f"conditions.{key} is an empty list")
    for value in axes["peptide"]:
        _require(str(value) == NO_PEPTIDE or str(value) in peptides,
                 f"conditions.peptide: '{value}' is not defined under 'peptides'")
    for value in axes["membrane"]:
        _require(str(value) in membranes,
                 f"conditions.membrane: '{value}' is not defined under 'membranes'")
    for key, valid in CHOICES.items():
        for value in axes[key]:
            _require(value in valid, f"conditions.{key}: '{value}' is not one of {', '.join(valid)}")
    for key, low in (("copies", 0), ("oligomer_size", 1)):
        for value in axes[key]:
            _require(isinstance(value, int) and value >= low,
                     f"conditions.{key} must be an integer >= {low}")
    for key in ("axis_distance", "water_beads_per_lipid"):
        for value in axes[key]:
            _require(isinstance(value, (int, float)) and value > 0, f"conditions.{key} must be > 0")
    for value in axes["oligomer_fraction"]:
        _require(isinstance(value, (int, float)) and 0 <= value <= 1,
                 "conditions.oligomer_fraction must be between 0 and 1")
    for value in axes["salt_molar"]:
        _require(isinstance(value, (int, float)) and value >= 0, "conditions.salt_molar must be >= 0")

    conditions = {}
    for combo in itertools.product(*axes.values()):
        values = _canonical(dict(zip(axes, combo)))
        values["peptide"], values["membrane"] = str(values["peptide"]), str(values["membrane"])
        if values["oligomer_size"] > 1:
            _require(values["copies"] >= values["oligomer_size"],
                     f"copies ({values['copies']}) is smaller than oligomer_size "
                     f"({values['oligomer_size']})")
        peptide = peptides.get(values["peptide"])
        lipids = membranes[values["membrane"]]
        cid = _condition_id(values, peptide, lipids)
        conditions[cid] = Condition(id=cid, lipids=tuple(lipids.items()), **values)

    given = data.get("simulation") or {}
    _require(isinstance(given, dict), "simulation must be a mapping")
    extra = sorted(set(given) - set(SIMULATION_DEFAULTS))
    _require(not extra, f"unknown key(s) under simulation: {', '.join(extra)}")
    try:
        simulation = mdp.load(path.resolve().parent, given)
    except mdp.MdpError as exc:
        raise ConfigError(str(exc)) from None
    ramp = simulation["equilibration"]
    _require(isinstance(ramp, list) and ramp and all(
        isinstance(s, (list, tuple)) and len(s) == 2 and s[0] > 0 and int(s[1]) > 0 for s in ramp),
        "schedule.equilibration must be a list of [dt_ps, steps] pairs")
    for key in ("temperature", "timestep_ps", "assembly_ns", "frame_ps"):
        _require(isinstance(simulation[key], (int, float)) and simulation[key] > 0,
                 f"schedule.{key} must be > 0")
    _require(isinstance(simulation["production_ns"], (int, float))
             and simulation["production_ns"] >= 0, "schedule.production_ns must be >= 0")

    return Campaign(
        root=path.resolve().parent,
        path=path.resolve(),
        master_seed=master_seed,
        replicas=replicas,
        peptides=peptides,
        membranes=membranes,
        conditions=tuple(conditions.values()),
        simulation=simulation,
    )
