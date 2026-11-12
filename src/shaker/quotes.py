"""End-of-stage quotes, in the GROMACS tradition.

The pick uses its own random source, unrelated to the simulation seeds, and the text goes to
the terminal only: never to a log, a table or a manifest.
"""

import os
import random
from functools import lru_cache

import yaml

from . import PACKAGE_DIR

_RANDOM = random.SystemRandom()


@lru_cache(maxsize=1)
def _load() -> dict:
    return yaml.safe_load((PACKAGE_DIR / "quotes.yaml").read_text())


def enabled() -> bool:
    return not os.environ.get("SHAKER_NO_QUOTES")


def pick(stage: str):
    """Return (text, author or None) for a stage, or None if the stage has no quotes."""
    entries = _load().get(stage) or []
    if not entries:
        return None
    entry = _RANDOM.choice(entries)
    return entry["text"], entry.get("author")
