"""A tiny in-memory activity log so the Runs tab shows real, recent runs.

Server-side + process-local (a session log, not a database — it resets on restart).
Every QoE stream, ask, and analysis records one entry here when it finishes.
"""

from __future__ import annotations

import itertools
import threading
import time

_LOCK = threading.Lock()
_RUNS: list = []
_CTR = itertools.count(1)
_MAX = 200


def record(workflow: str, company: str, label: str, **fields) -> dict:
    with _LOCK:
        rec = {"run_id": f"r{next(_CTR):04d}", "workflow": workflow, "company": company,
               "label": label, "ts": time.time(), "status": fields.pop("status", "done"),
               **fields}
        _RUNS.insert(0, rec)
        del _RUNS[_MAX:]
        return rec


def list_runs() -> list:
    with _LOCK:
        return list(_RUNS)


def get_run(run_id: str):
    with _LOCK:
        for r in _RUNS:
            if r["run_id"] == run_id:
                return r
    return None
