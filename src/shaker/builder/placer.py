"""Random insertion of rigid molecules into a periodic box, rejecting overlaps."""

import numpy as np
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation


class PlacementError(RuntimeError):
    pass


class Placer:
    """Keeps the beads placed so far and tests new molecules against them.

    Distances use the minimum-image convention, so a molecule near one face of the box is
    tested against molecules near the opposite face.
    """

    def __init__(self, box, min_distance: float, rng: np.random.Generator, max_tries: int = 5000):
        self.box = np.asarray(box, dtype=float)
        self.min_distance = float(min_distance)
        self.rng = rng
        self.max_tries = int(max_tries)
        self._points = np.empty((0, 3))
        self._tree = None
        self.tries = 0

    @property
    def points(self) -> np.ndarray:
        """Placed beads, wrapped into the box."""
        return self._points

    def _wrap(self, coords) -> np.ndarray:
        wrapped = np.mod(coords, self.box)
        # np.mod can return exactly the box length for tiny negative inputs.
        wrapped[wrapped >= self.box] = 0.0
        return wrapped

    def _overlaps(self, wrapped) -> bool:
        if self._tree is None:
            return False
        nearest, _ = self._tree.query(wrapped, k=1, distance_upper_bound=self.min_distance)
        return bool(np.isfinite(nearest).any())

    def _commit(self, wrapped) -> None:
        self._points = np.vstack([self._points, wrapped])
        self._tree = cKDTree(self._points, boxsize=self.box)

    def insert(self, template) -> np.ndarray:
        """Place one rigid body with a uniform random position and orientation.

        `template` is an (n, 3) array centred on the origin. The returned coordinates keep the
        molecule whole: beads may lie outside the box, which GROMACS accepts.
        """
        template = np.asarray(template, dtype=float)
        for _ in range(self.max_tries):
            self.tries += 1
            rotation = Rotation.random(random_state=self.rng).as_matrix()
            centre = self.rng.random(3) * self.box
            coords = template @ rotation.T + centre
            wrapped = self._wrap(coords)
            if not self._overlaps(wrapped):
                self._commit(wrapped)
                return coords
        raise PlacementError(
            f"no free position found after {self.max_tries} attempts. "
            "The box is too crowded: raise bead_volume or lower the number of molecules."
        )
