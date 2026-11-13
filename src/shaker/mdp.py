"""mdp.yaml: every GROMACS run parameter of a campaign, in one file beside config.yaml.

The file has three kinds of content. `periodicity` and `walls` say how the box is closed, and
are read by the box builder as well as by the run. `schedule` holds the quantities SHAKER turns
into per-stage values (temperature, time-step ramp, lengths, frame interval). `common`, `em` and
`md` are plain mdp options, written to the .mdp files as they are.

A campaign without the file runs on the defaults below, so they are the documented protocol.
"""

from pathlib import Path

import yaml

FILE_NAME = "mdp.yaml"
PERIODICITIES = ("xyz", "xy")
SCHEDULE_DEFAULTS = {
    "temperature": 310,
    "timestep_ps": 0.02,
    "equilibration": [[0.002, 5000], [0.005, 5000], [0.010, 5000], [0.020, 10000]],
    "assembly_ns": 400,
    "production_ns": 0,
    "frame_ps": 1000,
}
WALL_DEFAULTS = {"atomtype": "W", "type": "12-6", "r_linpot": 0.0, "density": None,
                 "margin_nm": 0.3}
WALL_TYPES = ("12-6", "9-3", "10-4")
MDP_DEFAULTS = {
    "common": {
        "cutoff-scheme": "Verlet",
        "nstlist": 20,
        "verlet-buffer-tolerance": 0.005,
        "coulombtype": "reaction-field",
        "rcoulomb": 1.1,
        "epsilon-r": 15,
        "epsilon-rf": 0,
        "vdw-type": "cutoff",
        "vdw-modifier": "Potential-shift-verlet",
        "rvdw": 1.1,
    },
    "em": {
        "integrator": "steep",
        "nsteps": 10000,
        "emtol": 1000,
        "emstep": 0.01,
    },
    "md": {
        "integrator": "md",
        "nstcomm": 100,
        "tcoupl": "v-rescale",
        "tc-grps": "System",
        "tau-t": 1.0,
        "pcoupl": "c-rescale",
        "pcoupltype": "isotropic",
        "tau-p": 4.0,
        "compressibility": "3e-4",
        "ref-p": 1.0,
        "constraints": "none",
        "constraint-algorithm": "lincs",
    },
}
# Options SHAKER writes itself, from `periodicity`, `walls`, `schedule` and the replica seed.
RESERVED = {
    "common": {"pbc", "nwall", "wall-atomtype", "wall-type", "wall-r-linpot", "wall-density"},
    "em": {"define"},
    "md": {"dt", "nsteps", "nstxout-compressed", "nstenergy", "nstlog", "ref-t", "gen-vel",
           "gen-temp", "gen-seed", "continuation"},
}
TOP_LEVEL = {"periodicity", "walls", "schedule", "common", "em", "md"}

EXAMPLE = """\
# Every GROMACS run parameter of the campaign. `shaker build` reads periodicity and walls;
# `shaker run` writes one .mdp per stage from this file.

periodicity: xyz                    # xyz: periodic in x, y and z
                                    # xy:  periodic in x and y, two walls close the box in z

walls:                              # read only when periodicity is xy
  atomtype: W                       # Martini bead type the two walls are made of
  type: 12-6                        # 12-6, 9-3 or 10-4 (the last two need `density`, in nm^-3)
  r_linpot: 0.0                     # nm, a bead beyond the wall feels a linear force back
  margin_nm: 0.3                    # the builder keeps every bead this far from a wall

schedule:
  temperature: 310                  # K
  timestep_ps: 0.02                 # of the assembly stage
  equilibration:                    # time-step ramp: [dt in ps, steps]
    - [0.002, 5000]
    - [0.005, 5000]
    - [0.010, 5000]
    - [0.020, 10000]
  assembly_ns: 400                  # self-assembly
  production_ns: 0                  # after alignment (not run yet)
  frame_ps: 1000                    # trajectory frame interval

common:                             # mdp options of every stage
  cutoff-scheme: Verlet
  nstlist: 20
  verlet-buffer-tolerance: 0.005
  coulombtype: reaction-field
  rcoulomb: 1.1
  epsilon-r: 15
  epsilon-rf: 0
  vdw-type: cutoff
  vdw-modifier: Potential-shift-verlet
  rvdw: 1.1

em:                                 # mdp options of the minimization
  integrator: steep
  nsteps: 10000
  emtol: 1000
  emstep: 0.01

md:                                 # mdp options of the ramp and of the assembly
  integrator: md
  nstcomm: 100
  tcoupl: v-rescale
  tc-grps: System
  tau-t: 1.0
  pcoupl: c-rescale
  pcoupltype: isotropic             # with periodicity xy the pressure acts through z
  tau-p: 4.0
  compressibility: 3e-4
  ref-p: 1.0
  constraints: none
  constraint-algorithm: lincs
"""


class MdpError(ValueError):
    """mdp.yaml is malformed or sets something SHAKER sets itself."""


def _key(name) -> str:
    """mdp option names in one spelling: GROMACS reads `tau_t` and `tau-t` as the same option."""
    return str(name).strip().lower().replace("_", "-")


def _require(condition, message):
    if not condition:
        raise MdpError(message)


def load(directory, legacy_schedule=None) -> dict:
    """Settings of a campaign: defaults, then `simulation:` of config.yaml, then mdp.yaml.

    `legacy_schedule` is the `simulation:` block of config.yaml, kept for campaigns written
    before mdp.yaml existed. A value given in both files must be the same in both.
    """
    path = Path(directory) / FILE_NAME
    legacy = dict(legacy_schedule or {})
    data = {}
    if path.is_file():
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise MdpError(f"{path} is not valid YAML: {exc}") from None
        _require(isinstance(data, dict), f"{path}: the top level must be a mapping")
    extra = sorted(set(data) - TOP_LEVEL)
    _require(not extra, f"{FILE_NAME}: unknown key(s): {', '.join(extra)}. "
                        f"Valid: {', '.join(sorted(TOP_LEVEL))}")

    given = data.get("schedule") or {}
    _require(isinstance(given, dict), f"{FILE_NAME}: schedule must be a mapping")
    extra = sorted(set(given) - set(SCHEDULE_DEFAULTS))
    _require(not extra, f"{FILE_NAME}: unknown key(s) under schedule: {', '.join(extra)}")
    clash = sorted(k for k in given if k in legacy and legacy[k] != given[k])
    _require(not clash, f"{', '.join(clash)} set to different values in config.yaml "
                        f"(simulation) and {FILE_NAME} (schedule): keep one")
    settings = {**SCHEDULE_DEFAULTS, **legacy, **given}

    periodicity = str(data.get("periodicity", "xyz")).lower()
    _require(periodicity in PERIODICITIES,
             f"{FILE_NAME}: periodicity must be one of {', '.join(PERIODICITIES)}, "
             f"not '{periodicity}'. GROMACS has no other partial periodicity; to leave x or y "
             "open instead of z, that axis has to be the z of the box")
    walls = dict(WALL_DEFAULTS)
    given_walls = data.get("walls") or {}
    _require(isinstance(given_walls, dict), f"{FILE_NAME}: walls must be a mapping")
    extra = sorted(set(given_walls) - set(WALL_DEFAULTS))
    _require(not extra, f"{FILE_NAME}: unknown key(s) under walls: {', '.join(extra)}")
    walls.update(given_walls)
    walls["type"] = str(walls["type"])
    _require(walls["type"] in WALL_TYPES,
             f"{FILE_NAME}: walls.type must be one of {', '.join(WALL_TYPES)}")
    _require(walls["type"] == "12-6" or walls["density"],
             f"{FILE_NAME}: walls.type {walls['type']} needs walls.density (nm^-3)")
    _require(isinstance(walls["margin_nm"], (int, float)) and walls["margin_nm"] >= 0,
             f"{FILE_NAME}: walls.margin_nm must be >= 0")

    options = {}
    for section, defaults in MDP_DEFAULTS.items():
        block = data.get(section) or {}
        _require(isinstance(block, dict), f"{FILE_NAME}: {section} must be a mapping")
        merged = dict(defaults)
        for name, value in block.items():
            key = _key(name)
            taken = key in RESERVED["common"] or key in RESERVED.get(section, ()) or (
                section == "common" and key in RESERVED["md"])
            _require(not taken,
                     f"{FILE_NAME}: {section}.{name} is written by shaker "
                     "(from periodicity, walls, schedule or the replica seed); remove it")
            merged[key] = value
        options[section] = merged

    settings.update(periodicity=periodicity, walls=walls, mdp=options,
                    mdp_file=str(path) if path.is_file() else None)
    return settings


def _value(value) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return " ".join(_value(v) for v in value)
    return str(value)


def _text(title, options) -> str:
    lines = [f"; SHAKER {title} (Martini 3). Written from mdp.yaml; edit that file, not this one."]
    lines += [f"{name:<24} = {_value(value)}" for name, value in options.items()]
    return "\n".join(lines) + "\n"


def _boundary(settings) -> dict:
    """pbc and, for a box open in z, the two walls that close it."""
    if settings.get("periodicity", "xyz") == "xyz":
        return {"pbc": "xyz"}
    walls = settings["walls"]
    options = {"pbc": "xy", "nwall": 2, "wall-atomtype": [walls["atomtype"]] * 2,
               "wall-type": walls["type"], "wall-r-linpot": walls["r_linpot"]}
    if walls.get("density"):
        options["wall-density"] = [walls["density"]] * 2
    return options


def _options(settings, section) -> dict:
    options = settings.get("mdp") or MDP_DEFAULTS
    return {**options["common"], **options[section]}


def em_text(settings) -> str:
    """Minimization. -DFLEXIBLE turns the peptide constraints into bonds."""
    return _text("energy minimization of a randomly mixed box",
                 {"define": "-DFLEXIBLE", **_options(settings, "em"), **_boundary(settings)})


def md_text(settings, dt, nsteps, first, gen_seed) -> str:
    """One dynamics stage: the given time step and length, velocities generated only at `first`."""
    every = max(1, min(nsteps, int(round(settings["frame_ps"] / dt))))
    energy = max(1, min(nsteps, every // 10 or 1))
    temperature = settings["temperature"]
    return _text("dynamics stage of a randomly mixed box", {
        **_options(settings, "md"), **_boundary(settings),
        "dt": dt, "nsteps": nsteps,
        "nstxout-compressed": every, "nstenergy": energy, "nstlog": energy,
        "ref-t": temperature,
        "gen-vel": first, "gen-temp": temperature, "gen-seed": gen_seed,
        "continuation": not first,
    })
