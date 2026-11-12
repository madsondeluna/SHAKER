"""SHAKER: Stochastic Homogeneous-mixture Assembly Kit for Ensemble Replicas."""

from pathlib import Path

__version__ = "0.1.0"

PACKAGE_DIR = Path(__file__).resolve().parent
FORCEFIELD_DIR = PACKAGE_DIR / "forcefield" / "martini3"
MDP_DIR = PACKAGE_DIR / "mdp"
