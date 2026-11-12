"""Atomistic ideal alpha-helix from a one-letter sequence."""

from pathlib import Path

import PeptideBuilder
from Bio.PDB import PDBIO

HELIX_PHI = -57.0
HELIX_PSI = -47.0

CANONICAL = set("ACDEFGHIKLMNPQRSTVWY")


def validate_sequence(sequence: str) -> str:
    sequence = sequence.strip().upper()
    if not sequence:
        raise ValueError("empty peptide sequence")
    unknown = sorted(set(sequence) - CANONICAL)
    if unknown:
        raise ValueError(
            f"sequence has residues outside the 20 canonical amino acids: {', '.join(unknown)}"
        )
    return sequence


def internal_prolines(sequence: str, margin: int = 2) -> list:
    """1-based positions of prolines away from the termini, where an ideal helix is doubtful."""
    return [i + 1 for i, aa in enumerate(sequence) if aa == "P" and margin <= i < len(sequence) - margin]


def write_ideal_helix(sequence: str, path) -> Path:
    """Write a PDB of the sequence as an ideal alpha-helix (phi -57, psi -47)."""
    sequence = validate_sequence(sequence)
    n = len(sequence)
    structure = PeptideBuilder.make_structure(
        sequence, [HELIX_PHI] * (n - 1), [HELIX_PSI] * (n - 1)
    )
    PeptideBuilder.add_terminal_OXT(structure)
    io = PDBIO()
    io.set_structure(structure)
    path = Path(path)
    io.save(str(path))
    return path
