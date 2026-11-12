"""Solvent sites on a jittered grid, kept only where the solute leaves room.

This is insane's idea for water: lay a grid over the box and fill the cells the solute does not
occupy. The grid makes the sites mutually non-overlapping by construction, and the jitter
removes the lattice order.
"""

import numpy as np
from scipy.spatial import cKDTree

GRID_SPACING = 0.42  # nm
JITTER = 0.03  # nm, each coordinate moves by at most this much
CLEARANCES = (0.35, 0.32, 0.30, 0.28)  # nm from any solute bead, tried in this order


class SolventError(RuntimeError):
    pass


def free_sites(box, solute_points, n_sites: int, rng: np.random.Generator):
    """Return (coordinates of n_sites free grid sites in random order, clearance used).

    The clearance to the solute starts at the first value of CLEARANCES and is relaxed only if
    the box cannot hold the requested number of sites.
    """
    box = np.asarray(box, dtype=float)
    counts = np.maximum(np.floor(box / GRID_SPACING).astype(int), 1)
    spacing = box / counts
    grid = np.stack(
        np.meshgrid(*[np.arange(c) for c in counts], indexing="ij"), axis=-1
    ).reshape(-1, 3)
    sites = (grid + 0.5) * spacing + rng.uniform(-JITTER, JITTER, size=(len(grid), 3))
    sites = np.mod(sites, box)
    sites[sites >= box] = 0.0

    if n_sites > len(sites):
        raise SolventError(
            f"{n_sites} solvent beads requested but the box holds only {len(sites)} grid sites. "
            "Raise bead_volume."
        )

    order = rng.permutation(len(sites))
    if len(solute_points) == 0:
        return sites[order[:n_sites]], CLEARANCES[0]

    tree = cKDTree(np.asarray(solute_points), boxsize=box)
    nearest, _ = tree.query(sites, k=1)
    available = 0
    for clearance in CLEARANCES:
        free = nearest[order] >= clearance
        available = int(free.sum())
        if available >= n_sites:
            return sites[order[free][:n_sites]], clearance
    raise SolventError(
        f"{n_sites} solvent beads requested but only {available} sites are free of solute "
        f"at {CLEARANCES[-1]} nm clearance. Raise bead_volume."
    )
