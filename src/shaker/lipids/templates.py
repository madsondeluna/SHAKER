"""Bead coordinates for a lipid, derived from its bonded graph.

insane keeps a hand-written table of bead positions per lipid. Here the template is computed
from the topology itself, so a template can never disagree with the itp it belongs to, and a
lipid added by the user gets one without anybody drawing it.

The layout is the same idea as insane's: the molecule is stretched along z, head on top. Each
bead sits one step below the bead it hangs from, and a branch (the second acyl chain) is shifted
sideways so the two tails run parallel. The result is not an equilibrium geometry. It only has
to be free of overlaps, since every box is energy-minimized before dynamics.
"""

from collections import defaultdict

import numpy as np

Z_STEP = 0.37  # nm between consecutive beads along the molecule
LANE_STEP = 0.50  # nm between parallel branches
MIN_SEPARATION = 0.30  # nm, lower bound accepted between any two beads of a template


def template_from_bonds(n_atoms: int, bonds) -> np.ndarray:
    """Return an (n_atoms, 3) array of coordinates in nm, centred on the origin."""
    neighbours = defaultdict(list)
    for i, j in set(bonds):
        neighbours[i].append(j)
        neighbours[j].append(i)

    depth = {0: 0}
    lane = {0: 0}
    next_lane = 1
    order = [0]
    for node in order:  # breadth-first walk from the first bead (the head)
        children = sorted(n for n in neighbours[node] if n not in depth)
        for k, child in enumerate(children):
            depth[child] = depth[node] + 1
            if k == 0:
                lane[child] = lane[node]
            else:
                lane[child] = next_lane
                next_lane += 1
            order.append(child)

    if len(depth) != n_atoms:
        raise ValueError("molecule is not fully connected by bonds; cannot build a template")

    coords = np.zeros((n_atoms, 3))
    for i in range(n_atoms):
        # Lanes alternate around the first one: 0, +1, -1, +2, ... along x.
        k = lane[i]
        offset = ((k + 1) // 2) * (1 if k % 2 else -1)
        coords[i] = (offset * LANE_STEP, 0.0, -depth[i] * Z_STEP)

    _separate_overlaps(coords)
    return coords - coords.mean(axis=0)


def _separate_overlaps(coords: np.ndarray) -> None:
    """Push apart beads that landed on the same spot (ring closures), along y."""
    n = len(coords)
    for _ in range(20):
        moved = False
        for i in range(n):
            for j in range(i + 1, n):
                if np.linalg.norm(coords[i] - coords[j]) < MIN_SEPARATION:
                    coords[j, 1] += LANE_STEP
                    moved = True
        if not moved:
            return
    raise ValueError("could not resolve overlapping beads in the template")


def min_separation(coords: np.ndarray) -> float:
    """Smallest distance between two beads of a template."""
    if len(coords) < 2:
        return float("inf")
    delta = coords[:, None, :] - coords[None, :, :]
    dist = np.linalg.norm(delta, axis=-1)
    dist[np.diag_indices(len(coords))] = np.inf
    return float(dist.min())
