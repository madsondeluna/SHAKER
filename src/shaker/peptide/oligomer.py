"""Homomultimers by geometry: n copies of a helix on a ring, axes parallel.

The multimer is a starting condition only. No restraint holds the copies together, so it is
free to fall apart once the simulation starts.
"""

import numpy as np

# Eisenberg consensus hydrophobicity scale.
EISENBERG = {
    "ALA": 0.62, "ARG": -2.53, "ASN": -0.78, "ASP": -0.90, "CYS": 0.29,
    "GLN": -0.85, "GLU": -0.74, "GLY": 0.48, "HIS": -0.40, "ILE": 1.38,
    "LEU": 1.06, "LYS": -1.50, "MET": 0.64, "PHE": 1.19, "PRO": 0.12,
    "SER": -0.18, "THR": -0.05, "TRP": 0.81, "TYR": 0.26, "VAL": 1.08,
}

MIN_BEAD_DISTANCE = 0.35  # nm between beads of different copies
DISTANCE_STEP = 0.05  # nm, increment when the requested axis distance gives overlaps
MAX_AXIS_DISTANCE = 4.0


def helix_axis(coords, atoms) -> np.ndarray:
    """Unit vector along the helix, N to C terminus, from the backbone beads."""
    backbone = np.array([coords[a.index] for a in atoms if a.name == "BB"])
    centred = backbone - backbone.mean(axis=0)
    axis = np.linalg.svd(centred, full_matrices=False)[2][0]
    if np.dot(backbone[-1] - backbone[0], axis) < 0:
        axis = -axis
    return axis / np.linalg.norm(axis)


def hydrophobic_direction(coords, atoms, axis) -> np.ndarray:
    """Unit vector, perpendicular to the axis, pointing to the hydrophobic face.

    Each residue contributes its radial direction (from the axis to the residue's beads)
    weighted by its Eisenberg hydrophobicity.
    """
    centre = coords.mean(axis=0)
    residues = {}
    for a in atoms:
        residues.setdefault(a.resnr, []).append(a)
    moment = np.zeros(3)
    for members in residues.values():
        position = np.mean([coords[a.index] for a in members], axis=0) - centre
        radial = position - np.dot(position, axis) * axis
        norm = np.linalg.norm(radial)
        if norm < 1e-6:
            continue
        moment += EISENBERG.get(members[0].resname, 0.0) * radial / norm
    norm = np.linalg.norm(moment)
    if norm < 1e-6:  # no amphipathicity: any perpendicular direction is as good as another
        trial = np.cross(axis, [1.0, 0.0, 0.0])
        if np.linalg.norm(trial) < 1e-6:
            trial = np.cross(axis, [0.0, 1.0, 0.0])
        return trial / np.linalg.norm(trial)
    return moment / norm


def _rotation_to_frame(axis, face) -> np.ndarray:
    """Rotation taking the helix axis to +z and the hydrophobic face to +x."""
    z = axis
    x = face - np.dot(face, z) * z
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    return np.array([x, y, z])


def _rot_z(angle) -> np.ndarray:
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def _min_distance_between_copies(copies) -> float:
    smallest = np.inf
    for i in range(len(copies)):
        for j in range(i + 1, len(copies)):
            delta = copies[i][:, None, :] - copies[j][None, :, :]
            smallest = min(smallest, float(np.linalg.norm(delta, axis=-1).min()))
    return smallest


def _ring(oriented, size, arrangement, hydrophobic_face, axis_distance):
    radius = axis_distance / (2.0 * np.sin(np.pi / size))
    flip_x = np.diag([1.0, -1.0, -1.0])  # 180 degrees about x: reverses the axis, keeps the face
    copies = []
    for k in range(size):
        copy = oriented.copy()
        if hydrophobic_face == "inward":
            copy = copy @ _rot_z(np.pi).T  # face to -x, the ring centre once shifted to +x
        if arrangement == "antiparallel" and k % 2 == 1:
            copy = copy @ flip_x.T
        copy = copy + np.array([radius, 0.0, 0.0])
        copies.append(copy @ _rot_z(2.0 * np.pi * k / size).T)
    return copies


def build_oligomer(
    coords,
    atoms,
    size: int,
    arrangement: str = "antiparallel",
    hydrophobic_face: str = "inward",
    axis_distance: float = 1.0,
):
    """Return (list of per-copy coordinates, axis distance actually used).

    The requested axis distance is increased in small steps until no two beads of different
    copies are closer than MIN_BEAD_DISTANCE.
    """
    if size < 1:
        raise ValueError("oligomer size must be at least 1")
    if arrangement not in ("parallel", "antiparallel"):
        raise ValueError("arrangement must be 'parallel' or 'antiparallel'")
    if hydrophobic_face not in ("inward", "outward"):
        raise ValueError("hydrophobic_face must be 'inward' or 'outward'")
    coords = np.asarray(coords, dtype=float)
    if size == 1:
        return [coords - coords.mean(axis=0)], 0.0

    axis = helix_axis(coords, atoms)
    face = hydrophobic_direction(coords, atoms, axis)
    frame = _rotation_to_frame(axis, face)
    oriented = (coords - coords.mean(axis=0)) @ frame.T

    distance = axis_distance
    while distance <= MAX_AXIS_DISTANCE:
        copies = _ring(oriented, size, arrangement, hydrophobic_face, distance)
        if _min_distance_between_copies(copies) >= MIN_BEAD_DISTANCE:
            centre = np.concatenate(copies).mean(axis=0)
            return [c - centre for c in copies], round(distance, 3)
        distance += DISTANCE_STEP
    raise ValueError("could not place the copies without overlap; check the peptide geometry")
