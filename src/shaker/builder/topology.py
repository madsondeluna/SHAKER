"""GROMACS topology written from the molecules that are actually in the box."""

import os
from pathlib import Path

FORCEFIELD_INCLUDES = (
    "martini_v3.0.0.itp",
    "martini_v3.0.0_phospholipids_v1.itp",
    "martini_v3.0.0_solvents_v2.itp",
    "martini_v3.0.0_ions_v1.itp",
)


def write_topology(path, title, molecules, forcefield_dir, extra_itps=()):
    """Write a .top file.

    `molecules` is an ordered list of (molecule name, count), in the same order as the
    coordinate file. Include paths are written relative to the topology, so a campaign
    directory can be moved as a whole.
    """
    path = Path(path)
    here = path.parent.resolve()
    lines = []
    for name in FORCEFIELD_INCLUDES:
        include = os.path.relpath(Path(forcefield_dir).resolve() / name, here)
        lines.append(f'#include "{include}"')
    for itp in extra_itps:
        lines.append(f'#include "{os.path.relpath(Path(itp).resolve(), here)}"')
    lines += ["", "[ system ]", title, "", "[ molecules ]"]
    lines += [f"{name:<12s} {count:d}" for name, count in molecules if count > 0]
    path.write_text("\n".join(lines) + "\n")
    return path
