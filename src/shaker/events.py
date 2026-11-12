"""Append-only event log, one JSON object per line, written and flushed as things happen.

The file stays readable after a crash because every completed line is already on disk and
valid on its own. Logging never raises: a full disk costs the log, not the run it observes.
"""

import datetime
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

FILENAME = "events.jsonl"


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


class EventLog:
    def __init__(self, directory, **context):
        self.path = Path(directory) / FILENAME
        self.context = context

    def write(self, event: str, status: str = "ok", **fields) -> None:
        record = {"time": _now(), "event": event, "status": status, **self.context, **fields}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, default=str) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            pass

    @contextmanager
    def step(self, event: str, **fields):
        """Record the start of a step, then its outcome with the elapsed time."""
        self.write(event, status="started", **fields)
        start = time.monotonic()
        try:
            yield
        except BaseException as exc:
            self.write(event, status="failed", seconds=round(time.monotonic() - start, 3),
                       error=type(exc).__name__, message=str(exc), **fields)
            raise
        self.write(event, status="ok", seconds=round(time.monotonic() - start, 3), **fields)


def read_events(directory) -> list:
    path = Path(directory) / FILENAME
    if not path.exists():
        return []
    records = []
    for line in path.read_text().splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a line cut short by a crash
    return records
