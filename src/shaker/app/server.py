"""Local interface: a small HTTP server that reads a campaign directory as it is, on every call.

Nothing is cached and nothing is written. The page polls, so what it shows is what is on disk
now. It binds to 127.0.0.1 unless told otherwise, and every path it opens is built from a
condition id and a replica index that were checked against the campaign first.
"""

import csv
import datetime
import json
import mimetypes
import re
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import __version__
from .. import campaign as ops
from ..config import ConfigError, load_campaign
from ..engine import read_state, stage_names
from ..events import read_events
from ..gro import read_gro
from ..lipids import LipidLibrary

STATIC = Path(__file__).resolve().parent / "static"
AMINO = {"ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE", "LEU", "LYS",
         "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL"}
HEAD_BEADS = {"NC3", "NH3", "PO4", "GL0", "CNO"}
GLYCEROL_BEADS = {"GL1", "GL2"}
KINDS = ["backbone", "sidechain", "head", "glycerol", "tail", "water", "ion"]
OUTPUT_SCAN_SECONDS = 30          # the file inventory walks every replica, so it is refreshed at this pace
OUTPUT_KINDS = (                  # (label, test on the file name), first match wins
    ("Starting boxes", lambda n: n == "box.gro"),
    ("Topologies", lambda n: n.endswith((".top", ".itp"))),
    ("Trajectories", lambda n: n.endswith(".xtc")),
    ("Final frames", lambda n: n.endswith(".gro")),
    ("Checkpoints", lambda n: n.endswith(".cpt")),
    ("Energies", lambda n: n.endswith(".edr")),
    ("Run inputs", lambda n: n.endswith((".tpr", ".mdp"))),
    ("Logs", lambda n: n.endswith((".log", ".jsonl"))),
    ("Records", lambda n: n.endswith(".json")),
)


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def replica_state(directory: Path, last_stage: str) -> str:
    """One word for where a replica is, from the files it has."""
    if not (directory / "manifest.json").exists():
        return "unbuilt"
    state = read_state(directory)
    if state.get("status") == "failed":
        return "failed"
    if (directory / f"{last_stage}.gro").exists():
        return "assembled"
    if state.get("status") == "running":
        return "running"
    if state.get("status") == "incomplete":
        return "paused"
    if (directory / "em.gro").exists():
        return "equilibrating"
    return "built"


class Reader:
    """Everything the page asks for, read from the campaign directory at the time of asking."""

    def __init__(self, config_path):
        self.config_path = Path(config_path)
        self.library = LipidLibrary.load()
        self._outputs = (0.0, None)

    def campaign(self):
        try:
            return load_campaign(self.config_path, self.library)
        except ConfigError as exc:
            raise ApiError(409, str(exc)) from None

    def _replica_dir(self, campaign, condition_id, replica):
        try:
            condition = campaign.condition(condition_id)
            index = int(replica)
        except (ConfigError, TypeError, ValueError):
            raise ApiError(404, "unknown condition or replica") from None
        if not 0 <= index < campaign.replicas:
            raise ApiError(404, "replica index out of range")
        return condition, index, ops.replica_dir(campaign, condition, index)

    def overview(self):
        campaign = self.campaign()
        plan = ops.plan(campaign, self.library)
        last = stage_names(campaign.simulation)[-1]
        conditions = []
        for row, status, condition in zip(plan["conditions"], ops.status(campaign), campaign.conditions):
            states = [replica_state(ops.replica_dir(campaign, condition, i), last)
                      for i in range(campaign.replicas)]
            conditions.append({**row, **status, "states": states})
        return {
            "name": campaign.root.name, "version": __version__, "time": time.time(),
            "master_seed": campaign.master_seed, "replicas": campaign.replicas,
            "simulation": campaign.simulation, "stages": stage_names(campaign.simulation),
            "totals": plan["totals"], "conditions": conditions,
        }

    def events(self, after=0, limit=300):
        campaign = self.campaign()
        records = read_events(campaign.root)
        start = max(int(after), len(records) - limit)
        return {"total": len(records), "after": start, "events": records[start:]}

    def replica(self, condition_id, replica):
        campaign = self.campaign()
        condition, index, directory = self._replica_dir(campaign, condition_id, replica)
        manifest = directory / "manifest.json"
        files = sorted(
            ({"name": p.name, "bytes": p.stat().st_size} for p in directory.iterdir() if p.is_file()),
            key=lambda f: f["name"]) if directory.exists() else []
        return {
            "condition": condition.as_row(), "replica": index,
            "state": replica_state(directory, stage_names(campaign.simulation)[-1]),
            "stages": stage_names(campaign.simulation),
            "manifest": json.loads(manifest.read_text()) if manifest.exists() else None,
            "run": read_state(directory), "events": read_events(directory), "files": files,
        }

    def structure(self, condition_id, replica):
        """Beads of the latest frame a replica has, for the viewer."""
        campaign = self.campaign()
        _, _, directory = self._replica_dir(campaign, condition_id, replica)
        candidates = [f"{name}.gro" for name in reversed(stage_names(campaign.simulation))] + ["box.gro"]
        source = next((directory / name for name in candidates if (directory / name).exists()), None)
        if source is None:
            raise ApiError(404, "this replica has no structure yet")
        _, resnames, names, coords, box = read_gro(source)
        kinds = []
        for resname, name in zip(resnames, names):
            if resname in AMINO:
                kinds.append(0 if name == "BB" else 1)
            elif resname == "W":
                kinds.append(5)
            elif resname == "ION":
                kinds.append(6)
            elif name in HEAD_BEADS:
                kinds.append(2)
            elif name in GLYCEROL_BEADS:
                kinds.append(3)
            else:
                kinds.append(4)
        return {"source": source.name, "box": [round(float(v), 3) for v in box], "kinds": kinds,
                "kind_names": KINDS, "xyz": [round(float(v), 2) for v in coords.reshape(-1)]}

    # ---- progress ---------------------------------------------------------------------------
    @staticmethod
    def _stage_fraction(directory: Path, stage: str):
        """How far a running stage is, from the last step in its log and nsteps in its mdp."""
        log, mdp = directory / f"{stage}.log", directory / f"{stage}.mdp"
        if not (log.exists() and mdp.exists()):
            return None
        total = re.search(r"^nsteps\s*=\s*(\d+)", mdp.read_text(), re.M)
        with open(log, "rb") as handle:
            handle.seek(max(0, log.stat().st_size - 6000))
            tail = handle.read().decode("utf-8", "replace")
        steps = re.findall(r"Step\s+Time\s*\n\s*(\d+)\s", tail)
        if not (total and steps and int(total.group(1))):
            return None
        return round(min(1.0, int(steps[-1]) / int(total.group(1))), 4)

    def _output_inventory(self, campaign):
        """Files on disk by kind. Cached for a while: it stats every file of every replica."""
        stamp, cached = self._outputs
        if cached is not None and time.time() - stamp < OUTPUT_SCAN_SECONDS:
            return cached
        kinds = {label: [0, 0] for label, _ in OUTPUT_KINDS}
        kinds["Other"] = [0, 0]
        root = campaign.root / "conditions"
        for path in root.rglob("*") if root.exists() else ():
            if not path.is_file():
                continue
            label = next((l for l, test in OUTPUT_KINDS if test(path.name)), "Other")
            kinds[label][0] += 1
            kinds[label][1] += path.stat().st_size
        results = campaign.root / "results"
        tables = sorted(
            ({"name": q.name, "bytes": q.stat().st_size, "modified": q.stat().st_mtime}
             for q in results.iterdir() if q.is_file()), key=lambda r: r["name"]) if results.exists() else []
        inventory = {
            "kinds": [{"kind": k, "files": v[0], "bytes": v[1]} for k, v in kinds.items() if v[0]],
            "tables": tables, "scanned": time.time(),
            "disk_free": shutil.disk_usage(campaign.root).free,
        }
        self._outputs = (time.time(), inventory)
        return inventory

    def progress(self):
        """What is done, what is running and what is left, for the whole campaign."""
        campaign = self.campaign()
        stages = stage_names(campaign.simulation)
        last = stages[-1]
        total = len(campaign.conditions) * campaign.replicas
        per_condition, running, done_per_stage = [], [], {name: 0 for name in stages}
        built = failed = 0
        for condition in campaign.conditions:
            counts = {name: 0 for name in stages}
            c_built = c_failed = 0
            for index in range(campaign.replicas):
                directory = ops.replica_dir(campaign, condition, index)
                if not (directory / "manifest.json").exists():
                    continue
                c_built += 1
                for name in stages:
                    if (directory / f"{name}.gro").exists():
                        counts[name] += 1
                state = replica_state(directory, last)
                c_failed += state == "failed"
                if state == "running":
                    current = next((n for n in stages if not (directory / f"{n}.gro").exists()), last)
                    running.append({"condition": condition.id, "replica": index, "stage": current,
                                    "fraction": self._stage_fraction(directory, current)})
            built += c_built; failed += c_failed
            for name in stages:
                done_per_stage[name] += counts[name]
            per_condition.append({"condition": condition.id, "replicas": campaign.replicas,
                                  "built": c_built, "failed": c_failed, **counts})

        peptides = campaign.used_peptides()
        prepared = sum((campaign.root / "peptides" / p.name / "prepared.json").exists() for p in peptides)
        steps = [
            {"step": "Environment pinned", "command": "shaker check --pin",
             "done": int((campaign.root / "environment.lock.yaml").exists()), "total": 1},
            {"step": "Peptides coarse-grained", "command": "shaker prepare", "done": prepared, "total": len(peptides)},
            {"step": "Boxes built", "command": "shaker build", "done": built, "total": total},
            {"step": "Minimized", "command": "shaker run", "done": done_per_stage["em"], "total": total},
            {"step": "Equilibrated", "command": "shaker run", "done": done_per_stage[stages[-2]], "total": total},
            {"step": "Assembled", "command": "shaker run", "done": done_per_stage[last], "total": total},
        ]

        # pace: replicas that reached the end in the last six hours, from the campaign log
        now = datetime.datetime.now().astimezone()
        finished = []
        for event in read_events(campaign.root):
            if event.get("event") == "run_replica" and event.get("status") == "done":
                try:
                    finished.append(datetime.datetime.fromisoformat(event["time"]))
                except (KeyError, ValueError):
                    continue
        recent = [t for t in finished if (now - t).total_seconds() <= 6 * 3600]
        per_hour = round(len(recent) / 6, 2) if recent else None
        remaining = total - done_per_stage[last]
        return {
            "time": time.time(), "stages": stages, "total": total, "failed": failed,
            "steps": steps, "conditions": per_condition, "running": running,
            "pace": {"finished_last_6h": len(recent), "per_hour": per_hour, "remaining": remaining,
                     "hours_left": round(remaining / per_hour, 1) if per_hour else None,
                     "last_finished": max(finished).isoformat(timespec="seconds") if finished else None},
            "outputs": self._output_inventory(campaign),
        }

    def peptides(self):
        campaign = self.campaign()
        table = campaign.root / "results" / "peptides.csv"
        if not table.exists():
            return {"rows": []}
        with open(table, newline="") as handle:
            return {"rows": list(csv.DictReader(handle))}


def make_handler(reader: Reader):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"shaker/{__version__}"

        def log_message(self, *args):          # the terminal stays quiet; the page is the output
            pass

        def _send(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload, status=200):
            self._send(status, json.dumps(payload, default=str).encode(), "application/json; charset=utf-8")

        def do_GET(self):
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/api/overview":
                    return self._json(reader.overview())
                if url.path == "/api/events":
                    return self._json(reader.events(query.get("after", 0)))
                if url.path == "/api/replica":
                    return self._json(reader.replica(query.get("condition"), query.get("replica")))
                if url.path == "/api/structure":
                    return self._json(reader.structure(query.get("condition"), query.get("replica")))
                if url.path == "/api/progress":
                    return self._json(reader.progress())
                if url.path == "/api/peptides":
                    return self._json(reader.peptides())
                if url.path.startswith("/api/"):
                    raise ApiError(404, "no such endpoint")
            except ApiError as exc:
                return self._json({"error": str(exc)}, exc.status)
            except (BrokenPipeError, ConnectionResetError):
                return None
            except Exception as exc:               # a half-written file must not kill the page
                return self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

            name = "index.html" if url.path in ("/", "") else url.path.lstrip("/")
            target = (STATIC / name).resolve()
            if STATIC not in target.parents or not target.is_file():
                return self._send(404, b"not found", "text/plain; charset=utf-8")
            kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if kind.startswith("text/") or kind in ("application/javascript", "image/svg+xml"):
                kind += "; charset=utf-8"
            return self._send(200, target.read_bytes(), kind)

    return Handler


def make_server(config_path, host="127.0.0.1", port=8765):
    return ThreadingHTTPServer((host, port), make_handler(Reader(config_path)))


def serve(config_path, host="127.0.0.1", port=8765, ready=None):
    """Run until interrupted. `ready(url)` is called once the socket is listening."""
    server = make_server(config_path, host, port)
    url = f"http://{host}:{server.server_address[1]}/"
    if ready:
        ready(url)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        while thread.is_alive():
            thread.join(0.5)
    finally:
        server.shutdown()
        server.server_close()
