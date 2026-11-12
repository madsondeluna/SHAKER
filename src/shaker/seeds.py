"""Seed derivation.

Every random choice in a campaign comes from one master seed. A replica seed depends only on
(master seed, condition id, replica index), so replica 37 of a condition is the same box on any
machine, and raising the replica count never changes the replicas that already exist.
"""

import hashlib

import numpy as np

# GROMACS reads gen_seed as a signed 32-bit integer.
_GMX_SEED_MAX = 2**31 - 1


def _digest(*parts) -> int:
    text = "\x1f".join(str(p) for p in parts)
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


def replica_seed(master_seed: int, condition_id: str, replica: int) -> int:
    """Seed of one replica."""
    return _digest("replica", master_seed, condition_id, replica)


def derive(seed: int, purpose: str) -> int:
    """Independent sub-seed for one purpose ("builder", "velocities", ...)."""
    return _digest("purpose", seed, purpose)


def rng(seed: int, purpose: str) -> np.random.Generator:
    """Random generator for one purpose of one replica."""
    return np.random.default_rng(derive(seed, purpose))


def gromacs_seed(seed: int, purpose: str = "velocities") -> int:
    """Positive 32-bit seed for the mdp gen_seed field."""
    return derive(seed, purpose) % _GMX_SEED_MAX + 1
