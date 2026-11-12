"""Writer and reader for the GROMACS .gro coordinate format."""

from pathlib import Path

import numpy as np


def write_gro(path, title, resnrs, resnames, names, coords, box):
    """Write a .gro file. Residue and atom numbers wrap at 100000, as the format requires."""
    coords = np.asarray(coords, dtype=float)
    lines = [title, f"{len(coords):d}"]
    for i, (resnr, resname, name, xyz) in enumerate(zip(resnrs, resnames, names, coords)):
        lines.append(
            f"{resnr % 100000:5d}{resname:<5.5s}{name:>5.5s}{(i + 1) % 100000:5d}"
            f"{xyz[0]:8.3f}{xyz[1]:8.3f}{xyz[2]:8.3f}"
        )
    lines.append(f"{box[0]:10.5f}{box[1]:10.5f}{box[2]:10.5f}")
    Path(path).write_text("\n".join(lines) + "\n")


def read_gro(path):
    """Return (resnrs, resnames, names, coords, box) from a .gro file."""
    lines = Path(path).read_text().splitlines()
    n = int(lines[1])
    resnrs, resnames, names = [], [], []
    coords = np.empty((n, 3))
    for i, line in enumerate(lines[2 : 2 + n]):
        resnrs.append(int(line[0:5]))
        resnames.append(line[5:10].strip())
        names.append(line[10:15].strip())
        coords[i] = (float(line[20:28]), float(line[28:36]), float(line[36:44]))
    box = np.array([float(v) for v in lines[2 + n].split()[:3]])
    return resnrs, resnames, names, coords, box
