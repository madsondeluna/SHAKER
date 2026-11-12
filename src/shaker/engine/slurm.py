"""SLURM job array for a set of replicas.

Each array task runs `shaker run --backend local` on a few replicas. Because a replica resumes
from its checkpoint, a requeued or resubmitted task continues instead of starting over, and
submitting again covers only the replicas that are not done.
"""

import datetime
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


class SlurmError(RuntimeError):
    pass


def chunk_tasks(targets: list, replicas_per_task: int) -> list:
    """Group (condition id, replica index) pairs: same condition, at most n per task."""
    by_condition = {}
    for condition_id, index in targets:
        by_condition.setdefault(condition_id, []).append(index)
    tasks = []
    for condition_id, indices in by_condition.items():
        for start in range(0, len(indices), replicas_per_task):
            tasks.append((condition_id, indices[start:start + replicas_per_task]))
    return tasks


def shaker_command() -> str:
    candidate = Path(sys.argv[0])
    if candidate.name == "shaker" and candidate.exists():
        return str(candidate.resolve())
    return shutil.which("shaker") or "shaker"


def write_array(root, config_path, targets, replicas_per_task=1, run_options=(), partition=None,
                time_limit="24:00:00", cpus_per_task=4, gres=None, max_parallel=None,
                setup=None, sbatch_args=()):
    """Write slurm/<stamp>.sbatch and its task list. Returns (script path, number of tasks)."""
    root = Path(root)
    tasks = chunk_tasks(targets, replicas_per_task)
    if not tasks:
        raise SlurmError("nothing to submit: every selected replica is already done")
    directory = root / "slurm"
    (directory / "logs").mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    task_file = directory / f"{stamp}.tasks"
    task_file.write_text("".join(
        f"--condition {condition_id} " + " ".join(f"--replica {i}" for i in indices) + "\n"
        for condition_id, indices in tasks
    ))

    array = f"0-{len(tasks) - 1}" + (f"%{max_parallel}" if max_parallel else "")
    header = [
        "#!/bin/bash",
        "#SBATCH --job-name=shaker",
        f"#SBATCH --array={array}",
        f"#SBATCH --time={time_limit}",
        f"#SBATCH --cpus-per-task={cpus_per_task}",
        "#SBATCH --output=slurm/logs/%A_%a.out",
        "#SBATCH --requeue",
    ]
    if partition:
        header.append(f"#SBATCH --partition={partition}")
    if gres:
        header.append(f"#SBATCH --gres={gres}")
    header += [f"#SBATCH {argument}" for argument in sbatch_args]

    command = [shaker_command(), "run", "--config", str(Path(config_path).resolve()),
               "--backend", "local", "--no-quotes", "--jobs", str(replicas_per_task)]
    command += list(run_options)
    body = [
        "",
        "set -euo pipefail",
        f"cd {shlex.quote(str(root.resolve()))}",
    ]
    if setup:
        body.append(f"source {shlex.quote(str(Path(setup).resolve()))}")
    body += [
        f'TASK=$(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" {shlex.quote(str(task_file.relative_to(root)))})',
        "# TASK holds only flags written by shaker (--condition <id> --replica <n>), split on purpose",
        " ".join(shlex.quote(part) for part in command) + " $TASK",
        "",
    ]
    script = directory / f"{stamp}.sbatch"
    script.write_text("\n".join(header + body))
    return script, len(tasks)


def submit(script) -> str:
    """Call sbatch and return its answer."""
    if shutil.which("sbatch") is None:
        raise SlurmError("sbatch not found on this machine; the script was written, submit it "
                         "where SLURM is available")
    script = Path(script)
    result = subprocess.run(["sbatch", str(script.relative_to(script.parent.parent))],
                            cwd=script.parent.parent, capture_output=True, text=True)
    if result.returncode != 0:
        raise SlurmError(f"sbatch failed (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()
