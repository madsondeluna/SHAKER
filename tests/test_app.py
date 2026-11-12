"""The local interface: its API reads the campaign directory as it is."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from shaker import cli
from shaker.app import make_server
from shaker.config import EXAMPLE


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(EXAMPLE.replace("replicas: 100", "replicas: 2"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SHAKER_NO_QUOTES", "1")
    assert cli.main(["prepare", "-q"]) == 0
    assert cli.main(["build", "-q", "--replicas", "1"]) == 0
    server = make_server(tmp_path / "config.yaml", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown(); server.server_close()


def get(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.status, response.headers.get("Content-Type"), response.read()


def test_overview_reports_states(campaign):
    status, kind, body = get(campaign + "/api/overview")
    data = json.loads(body)
    assert status == 200 and kind.startswith("application/json")
    assert len(data["conditions"]) == 3
    assert all(c["states"] == ["built", "unbuilt"] for c in data["conditions"])
    assert data["totals"]["simulations"] == 6


def test_replica_and_structure(campaign):
    condition = json.loads(get(campaign + "/api/overview")[2])["conditions"][0]["id"]
    detail = json.loads(get(f"{campaign}/api/replica?condition={condition}&replica=0")[2])
    assert detail["state"] == "built" and detail["manifest"]["total_beads"] > 0
    assert any(f["name"] == "box.gro" for f in detail["files"])
    beads = json.loads(get(f"{campaign}/api/structure?condition={condition}&replica=0")[2])
    assert beads["source"] == "box.gro"
    assert len(beads["xyz"]) == 3 * len(beads["kinds"]) == 3 * detail["manifest"]["total_beads"]
    assert set(beads["kinds"]) <= set(range(7)) and 0 in beads["kinds"] and 5 in beads["kinds"]


def test_events_and_peptides(campaign):
    events = json.loads(get(campaign + "/api/events")[2])
    assert events["total"] >= 4 and events["events"][-1]["event"] == "build_replica"
    rows = json.loads(get(campaign + "/api/peptides")[2])["rows"]
    assert rows[0]["peptide"] == "mag2"


@pytest.mark.parametrize("path, status", [
    ("/api/replica?condition=nope&replica=0", 404),
    ("/api/replica?condition=../../etc&replica=0", 404),
    ("/api/nothing", 404),
    ("/../../pyproject.toml", 404),
    ("/%2e%2e/%2e%2e/pyproject.toml", 404),
])
def test_bad_requests_are_refused(campaign, path, status):
    with pytest.raises(urllib.error.HTTPError) as error:
        get(campaign + path)
    assert error.value.code == status


def test_out_of_range_replica(campaign):
    condition = json.loads(get(campaign + "/api/overview")[2])["conditions"][0]["id"]
    with pytest.raises(urllib.error.HTTPError) as error:
        get(f"{campaign}/api/replica?condition={condition}&replica=99")
    assert error.value.code == 404


def test_static_page(campaign):
    status, kind, body = get(campaign + "/")
    assert status == 200 and kind.startswith("text/html") and b"<title>SHAKER</title>" in body
    assert get(campaign + "/app.js")[0] == 200 and get(campaign + "/tokens.css")[0] == 200


def test_progress_reports_steps_and_outputs(campaign):
    data = json.loads(get(campaign + "/api/progress")[2])
    steps = {s["step"]: (s["done"], s["total"]) for s in data["steps"]}
    assert steps["Peptides coarse-grained"] == (1, 1)
    assert steps["Boxes built"] == (3, 6)
    assert steps["Assembled"] == (0, 6) and steps["Environment pinned"] == (0, 1)
    assert data["running"] == [] and data["pace"]["remaining"] == 6
    kinds = {k["kind"]: k["files"] for k in data["outputs"]["kinds"]}
    assert kinds["Starting boxes"] == 3 and kinds["Records"] == 3
    assert any(t["name"] == "peptides.csv" for t in data["outputs"]["tables"])
    assert all(row["built"] == 1 and row["em"] == 0 for row in data["conditions"])
