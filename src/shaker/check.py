"""Environment verification: is this machine ready, and is it the same as the pinned one?

`run_checks` reports; it changes nothing. `pin` writes the versions found now into the
campaign, and from then on a different version is a failure rather than a silent drift.
"""

import hashlib
import importlib.metadata
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from . import FORCEFIELD_DIR, __version__
from .lipids import LipidLibrary

LOCK_FILE = "environment.lock.yaml"
PYTHON_PACKAGES = ("vermouth", "numpy", "scipy", "PeptideBuilder", "biopython", "PyYAML")


def gromacs_version():
    executable = shutil.which("gmx") or shutil.which("gmx_mpi")
    if executable is None:
        return None
    try:
        result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    match = re.search(r"GROMACS version:\s+(\S+)", result.stdout + result.stderr)
    return match.group(1) if match else None


def current_versions() -> dict:
    versions = {
        "shaker": __version__,
        "python": platform.python_version(),
        "gromacs": gromacs_version(),
    }
    for package in PYTHON_PACKAGES:
        try:
            versions[package.lower()] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package.lower()] = None
    return versions


def forcefield_hashes(forcefield_dir=FORCEFIELD_DIR) -> dict:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(Path(forcefield_dir).glob("*.itp"))
    }


def pin(root) -> Path:
    """Write the versions and force-field hashes found now as the campaign's reference."""
    path = Path(root) / LOCK_FILE
    path.write_text(yaml.safe_dump(
        {"versions": current_versions(), "forcefield": forcefield_hashes()}, sort_keys=True))
    return path


def _check(name, status, detail):
    return {"name": name, "status": status, "detail": detail}


def run_checks(root) -> list:
    """Return a list of {name, status, detail}; status is ok, warn or fail."""
    root = Path(root)
    checks = []
    versions = current_versions()

    lock_path = root / LOCK_FILE
    lock = yaml.safe_load(lock_path.read_text()) if lock_path.exists() else None
    pinned = (lock or {}).get("versions", {})

    for name, found in versions.items():
        if found is None:
            checks.append(_check(name, "fail", "not found"))
        elif name in pinned and pinned[name] != found:
            checks.append(_check(name, "fail", f"{found} found, {pinned[name]} pinned"))
        elif name in pinned:
            checks.append(_check(name, "ok", f"{found} (pinned)"))
        else:
            checks.append(_check(name, "ok", found))

    beside_python = Path(sys.executable).parent / "martinize2"
    martinize = str(beside_python) if beside_python.exists() else shutil.which("martinize2")
    checks.append(_check("martinize2", "ok" if martinize else "fail", martinize or "not found"))

    sources = yaml.safe_load((FORCEFIELD_DIR / "sources.yaml").read_text())["files"]
    found_hashes = forcefield_hashes()
    reference = (lock or {}).get("forcefield", sources)
    changed = sorted(n for n in reference if found_hashes.get(n) != reference[n])
    checks.append(_check(
        "force field",
        "fail" if changed else "ok",
        f"hash mismatch: {', '.join(changed)}" if changed else f"{len(reference)} files match",
    ))

    library = LipidLibrary.load()
    problems = library.validate()
    checks.append(_check(
        "lipid library",
        "fail" if problems else "ok",
        problems[0] if problems else f"{len(library)} lipids, templates valid",
    ))

    vmd = shutil.which("vmd") or next(iter(sorted(Path("/Applications").glob("VMD*.app"))), None)
    checks.append(_check("vmd", "ok" if vmd else "warn",
                         str(vmd) if vmd else "not found, renders will be skipped"))

    free_gb = shutil.disk_usage(root).free / 1e9
    checks.append(_check("disk", "ok" if free_gb > 5 else "warn", f"{free_gb:.0f} GB free"))

    checks.append(_check(
        "pinned environment",
        "ok" if lock else "warn",
        str(lock_path.name) if lock else "not pinned, run `shaker check --pin`",
    ))
    return checks


def blocking(checks) -> list:
    return [c for c in checks if c["status"] == "fail"]
