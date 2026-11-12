"""Sequence and coarse-grained descriptors of a peptide, for the `peptides` table."""

import math

from Bio.SeqUtils.ProtParam import ProteinAnalysis

from .peptide.martinize import expected_charge

# Eisenberg consensus hydrophobicity scale, one-letter codes.
EISENBERG = {
    "A": 0.62, "R": -2.53, "N": -0.78, "D": -0.90, "C": 0.29, "Q": -0.85, "E": -0.74,
    "G": 0.48, "H": -0.40, "I": 1.38, "L": 1.06, "K": -1.50, "M": 0.64, "F": 1.19,
    "P": 0.12, "S": -0.18, "T": -0.05, "W": 0.81, "Y": 0.26, "V": 1.08,
}
HYDROPHOBIC = set("AILMFVWC")
CATIONIC = set("KR")
ANIONIC = set("DE")
AROMATIC = set("FWY")
HELIX_ANGLE = 100.0  # degrees per residue in an alpha-helix
HELIX_RISE_NM = 0.15


def hydrophobic_moment(sequence: str, angle: float = HELIX_ANGLE) -> float:
    """Mean helical hydrophobic moment (Eisenberg), per residue."""
    sin_sum = sum(EISENBERG[aa] * math.sin(math.radians(i * angle)) for i, aa in enumerate(sequence))
    cos_sum = sum(EISENBERG[aa] * math.cos(math.radians(i * angle)) for i, aa in enumerate(sequence))
    return math.hypot(sin_sum, cos_sum) / len(sequence)


def describe(name: str, sequence: str, c_terminus: str, secondary_structure: str,
             n_beads: int = None, cg_charge: float = None) -> dict:
    n = len(sequence)
    fraction = lambda group: round(sum(aa in group for aa in sequence) / n, 4)
    return {
        "peptide": name,
        "sequence": sequence,
        "length": n,
        "c_terminus": c_terminus,
        "secondary_structure": secondary_structure,
        "net_charge": expected_charge(sequence, c_terminus),
        "isoelectric_point": round(ProteinAnalysis(sequence).isoelectric_point(), 2),
        "hydrophobicity_mean": round(sum(EISENBERG[aa] for aa in sequence) / n, 4),
        "hydrophobic_moment": round(hydrophobic_moment(sequence), 4),
        "hydrophobicity_scale": "Eisenberg consensus",
        "fraction_hydrophobic": fraction(HYDROPHOBIC),
        "fraction_cationic": fraction(CATIONIC),
        "fraction_anionic": fraction(ANIONIC),
        "fraction_aromatic": fraction(AROMATIC),
        "helix_length_nm": round(n * HELIX_RISE_NM, 2),
        "cg_beads": n_beads,
        "cg_charge": cg_charge,
    }
