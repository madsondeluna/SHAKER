import json

import pytest

from shaker import cli
from shaker.config import EXAMPLE, ConfigError, load_campaign
from shaker.events import read_events


@pytest.fixture
def campaign_dir(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(EXAMPLE.replace("replicas: 100", "replicas: 3"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SHAKER_NO_QUOTES", "1")
    return tmp_path


def run(capfd, *argv):
    # the consoles were created at import; point them at the streams pytest captures now
    import sys

    from shaker import ui

    ui.err.file, ui.console.file = sys.stderr, sys.stdout
    try:
        code = cli.main(list(argv))
    except SystemExit as exc:
        code = exc.code
    captured = capfd.readouterr()
    return code, captured.out, captured.err


# config -----------------------------------------------------------------------------------

def test_example_expands_to_three_conditions(campaign_dir):
    campaign = load_campaign(campaign_dir / "config.yaml")
    ids = [c.id for c in campaign.conditions]
    # mag2 with monomers, mag2 with hexamers, and one peptide-free control (not two)
    assert len(ids) == 3 and len(set(ids)) == 3
    assert sum(c.peptide == "none" for c in campaign.conditions) == 1
    assert ids == [c.id for c in load_campaign(campaign_dir / "config.yaml").conditions]


def test_condition_id_follows_the_composition(campaign_dir):
    before = {c.id for c in load_campaign(campaign_dir / "config.yaml").conditions}
    path = campaign_dir / "config.yaml"
    path.write_text(path.read_text().replace("POPE: 96, POPG: 32", "POPE: 64, POPG: 64"))
    after = {c.id for c in load_campaign(path).conditions}
    assert before.isdisjoint(after)


@pytest.mark.parametrize("old, new, expected", [
    ("master_seed: 20261112", "master_seed: abc", "master_seed"),
    ("membrane: bacterial", "membrane: nowhere", "not defined under 'membranes'"),
    ("arrangement: antiparallel", "arrangement: sideways", "arrangement"),
    ("copies: 6", "copies: 4", "smaller than oligomer_size"),
    ("salt_molar: 0.15", "salt_molar: 0.15\n  bogus: 1", "unknown key"),
    ("sequence: GIGKFLHSAKKFGKAFVGEIMNS", "sequence: GIGXB", "canonical"),
])
def test_invalid_config_names_the_problem(campaign_dir, old, new, expected):
    path = campaign_dir / "config.yaml"
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))
    with pytest.raises(ConfigError, match=expected):
        load_campaign(path)


# cli --------------------------------------------------------------------------------------

def test_plan_json(campaign_dir, capfd):
    code, out, _ = run(capfd, "plan", "--json")
    data = json.loads(out)
    assert code == 0
    assert data["totals"]["simulations"] == 9
    assert len(data["conditions"]) == 3


def test_prepare_build_status(campaign_dir, capfd):
    assert run(capfd, "prepare", "--json")[0] == 0
    assert (campaign_dir / "results" / "peptides.csv").exists()

    code, out, _ = run(capfd, "build", "--replicas", "2", "--json")
    data = json.loads(out)
    assert code == 0 and data["counts"] == {"built": 6, "skipped": 0, "failed": 0}

    # second call builds only what is missing, and never rebuilds
    code, out, _ = run(capfd, "build", "--json")
    assert json.loads(out)["counts"] == {"built": 3, "skipped": 6, "failed": 0}

    code, out, _ = run(capfd, "status", "--json")
    assert all(row["built"] == 3 for row in json.loads(out))

    replica = next((campaign_dir / "conditions").glob("mag2*o6*/rep_000"))
    for name in ("box.gro", "topol.top", "manifest.json", "events.jsonl"):
        assert (replica / name).exists()
    assert [e["status"] for e in read_events(replica)] == ["started", "ok"]
    assert (campaign_dir / "forcefield" / "martini_v3.0.0.itp").exists()
    events = read_events(campaign_dir)
    assert sum(e["event"] == "build_replica" for e in events) == 15


def test_build_refuses_a_changed_master_seed(campaign_dir, capfd):
    run(capfd, "build", "--condition",
        load_campaign(campaign_dir / "config.yaml").conditions[-1].id, "--replicas", "1")
    path = campaign_dir / "config.yaml"
    path.write_text(path.read_text().replace("master_seed: 20261112", "master_seed: 7"))
    code, out, err = run(capfd, "build", "--replicas", "1", "--json")
    assert code == 1
    assert json.loads(out.splitlines()[0])["counts"]["failed"] == 1
    assert "different seed" in err


def test_dry_run_writes_nothing(campaign_dir, capfd):
    code, out, _ = run(capfd, "build", "--dry-run", "--json")
    assert code == 0 and len(json.loads(out)) == 9
    assert not (campaign_dir / "conditions").exists()


def test_exit_codes(campaign_dir, capfd):
    assert run(capfd, "--config", "missing.yaml", "plan")[0] == 3
    code, _, err = run(capfd, "build", "--replicsa", "2")
    assert code == 2 and "did you mean `--replica" in err
    code, _, err = run(capfd, "buidl")
    assert code == 2 and "did you mean `build`" in err
    code, _, err = run(capfd, "lipids", "show", "POPX")
    assert code == 1 and "POPS" in err
    assert run(capfd, "init")[0] == 1  # config.yaml already exists


def test_json_error_document(campaign_dir, capfd):
    code, out, _ = run(capfd, "--config", "missing.yaml", "plan", "--json")
    assert code == 3 and json.loads(out)["error"]["code"] == "config"


def test_lipids_plain(campaign_dir, capfd):
    code, out, _ = run(capfd, "lipids", "list", "--head", "PG", "--plain")
    lines = out.strip().splitlines()
    assert code == 0 and lines[0].split("\t")[0] == "name" and len(lines) == 24


def test_check_pin_then_detect_drift(campaign_dir, capfd):
    assert run(capfd, "check", "--pin", "--json")[0] == 0
    lock = campaign_dir / "environment.lock.yaml"
    lock.write_text(lock.read_text().replace("numpy: ", "numpy: 0.0.0-was-"))
    code, out, err = run(capfd, "check", "--json")
    assert code == 1
    assert json.loads(out.splitlines()[0])["ok"] is False
    assert "numpy" in err
