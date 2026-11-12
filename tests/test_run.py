"""`shaker run` against real GROMACS, on a small box with very short stages."""

import json
import shutil

import pytest

from shaker import cli
from shaker.config import EXAMPLE, load_campaign
from shaker.engine import read_state
from shaker.engine.gromacs import stage_plan
from shaker.engine.slurm import chunk_tasks
from shaker.events import read_events

pytestmark = pytest.mark.skipif(shutil.which("gmx") is None, reason="GROMACS not installed")

SHORT = """simulation:
  temperature: 310
  equilibration:
    - [0.002, 200]
    - [0.010, 200]
  assembly_ns: 0.004
  frame_ps: 2
"""


@pytest.fixture
def campaign_dir(tmp_path, monkeypatch):
    text = EXAMPLE[: EXAMPLE.index("simulation:")] + SHORT
    text = text.replace("replicas: 100", "replicas: 2")
    text = text.replace("{POPE: 96, POPG: 32}", "{POPE: 48, POPG: 16}")
    text = text.replace("oligomer_size: [1, 6]", "oligomer_size: 1")
    (tmp_path / "config.yaml").write_text(text)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SHAKER_NO_QUOTES", "1")
    return tmp_path


def run(capfd, *argv):
    import sys

    from shaker import ui

    ui.err.file, ui.console.file = sys.stderr, sys.stdout
    try:
        code = cli.main(list(argv))
    except SystemExit as exc:
        code = exc.code
    captured = capfd.readouterr()
    return code, captured.out, captured.err


def test_stage_plan_uses_the_replica_seed(campaign_dir):
    campaign = load_campaign(campaign_dir / "config.yaml")
    plan_a = dict(stage_plan(campaign.simulation, 1))
    plan_b = dict(stage_plan(campaign.simulation, 2))
    assert list(plan_a) == ["em", "eq1", "eq2", "assembly"]
    assert "gen_vel                  = yes" in plan_a["eq1"]
    assert "gen_vel                  = no" in plan_a["eq2"]
    assert plan_a["eq1"] != plan_b["eq1"]  # different gen_seed
    assert "nsteps                   = 200" in plan_a["assembly"]


def test_chunk_tasks_keeps_conditions_apart():
    tasks = chunk_tasks([("a", 0), ("a", 1), ("a", 2), ("b", 0)], 2)
    assert tasks == [("a", [0, 1]), ("a", [2]), ("b", [0])]


def test_run_resumes_and_reports(campaign_dir, capfd):
    assert run(capfd, "build", "--json")[0] == 0

    # stop after the first ramp stage
    code, out, _ = run(capfd, "run", "--until", "eq1", "--threads", "2", "--jobs", "2",
                       "--no-check", "--json")
    data = json.loads(out)
    assert code == 0, data
    assert data["counts"] == {"done": 4, "incomplete": 0, "failed": 0}

    replica = next((campaign_dir / "conditions").glob("mag2*/rep_000"))
    assert (replica / "eq1.gro").exists() and not (replica / "eq2.tpr").exists()
    em_time = read_state(replica)["stages"]["em"]["seconds"]

    # continue to the end: finished stages are not repeated
    code, out, _ = run(capfd, "run", "--threads", "2", "--jobs", "2", "--no-check", "--json")
    data = json.loads(out)
    assert code == 0, data
    assert data["counts"]["done"] == 4 and data["target"] == "assembly"
    state = read_state(replica)
    assert state["status"] == "done"
    assert state["stages"]["em"]["seconds"] == em_time
    assert state["stages"]["assembly"]["ns_per_day"] > 0
    assert state["stages"]["assembly"]["frames"] == 3  # 200 steps of 0.02 ps, one frame every 2 ps
    assert state["stages"]["assembly"]["trajectory_bytes"] > 0
    assert (replica / "assembly.xtc").exists()

    events = [e["event"] for e in read_events(replica) if e["status"] == "ok"]
    assert events == ["build", "em", "eq1", "eq2", "assembly"]

    # nothing left to do
    code, out, _ = run(capfd, "run", "--no-check", "--json")
    assert code == 0 and json.loads(out)["already_done"] == 4

    code, out, _ = run(capfd, "plan", "--json")
    totals = json.loads(out)["totals"]
    assert totals["ns_per_day_source"] == "measured" and totals["trajectory_gb"] is not None

    code, out, _ = run(capfd, "status", "--json")
    assert all(row["assembled"] == 2 and row["failed"] == 0 for row in json.loads(out))


def test_failed_replica_is_recorded_with_its_reason(campaign_dir, capfd):
    run(capfd, "build", "--replicas", "1", "--json")
    replica = next((campaign_dir / "conditions").glob("none*/rep_000"))
    topology = replica / "topol.top"
    topology.write_text(topology.read_text().replace("POPE ", "NOPE "))
    code, out, err = run(capfd, "run", "--until", "em", "--no-check", "--json")
    data = json.loads(out.splitlines()[0])
    assert code == 1 and data["counts"]["failed"] == 1
    failed = next(o for o in data["replicas"] if o["status"] == "failed")
    assert "grompp" in failed["reason"]
    assert read_state(replica)["status"] == "failed"
    assert any(e["status"] == "failed" for e in read_events(replica))
    # the other replica still ran
    assert data["counts"]["done"] == 1


def test_slurm_array_script(campaign_dir, capfd):
    run(capfd, "build", "--json")
    code, out, err = run(capfd, "run", "--backend", "slurm-array", "--replicas-per-task", "2",
                         "--gres", "gpu:1", "--time", "12:00:00", "--max-parallel", "5",
                         "--mdrun-args", "-nb gpu", "--no-check", "--json")
    data = json.loads(out)
    assert code == 0 and data["tasks"] == 2 and data["replicas"] == 4 and not data["submitted"]
    script = (campaign_dir / "slurm").glob("*.sbatch").__next__().read_text()
    assert "#SBATCH --array=0-1%5" in script
    assert "#SBATCH --gres=gpu:1" in script and "#SBATCH --requeue" in script
    assert "--jobs 2" in script and "'-nb gpu'" in script
    tasks = (campaign_dir / "slurm").glob("*.tasks").__next__().read_text().splitlines()
    assert len(tasks) == 2 and all(t.count("--replica") == 2 for t in tasks)


def test_run_usage_errors(campaign_dir, capfd):
    code, _, err = run(capfd, "run", "--until", "eq9")
    assert code == 2 and "valid: em, eq1, eq2, assembly" in err
    code, _, err = run(capfd, "run", "--replica", "7")
    assert code == 2 and "out of range" in err
