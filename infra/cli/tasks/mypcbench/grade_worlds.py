#!/usr/bin/env python3
"""Host-side grader for ported MyPCBench tasks run on worlds instances.

The REST analog of grade_container.py: instead of `docker exec sqlite3`, it reads
each instance's own REST API over HTTP. The runner passes a JSON context on stdin
with `worlds_instances` ({app_key: instance_url}); each task's checks GET a path on
the matching instance and assert on the JSON. Prints one JSON line:
  {"score","max_score","checkpoints":[...],"log"}.
"""
from __future__ import annotations

import json
import sys
from typing import Callable

import httpx

# task_id -> list of (app_key, rest_path, predicate(json)->bool, weight, label)
SPECS: dict[str, list[tuple[str, str, Callable[[object], bool], float, str]]] = {
    "mypc-dinoco-seat-dn1563": [
        ("dinoco", "/api/flights",
         lambda j: any(f.get("flight_number") == "DN1563" and str(f.get("seat")) == "16A"
                       for f in (j.get("flights", []) if isinstance(j, dict) else j)),
         100, "DN1563 seat = 16A"),
    ],
    "mypc-gringotts-zelle-send": [
        ("gringotts", "/api/transactions",
         lambda j: any(abs(float(t.get("amount", 0)) + 246.81) < 0.005
                       for t in (j if isinstance(j, list) else j.get("transactions", []))),
         100, "Zelle debit of $246.81"),
    ],
}


def _get(url: str, path: str) -> object:
    r = httpx.get(url.rstrip("/") + path, timeout=httpx.Timeout(20.0), follow_redirects=True)
    r.raise_for_status()
    return r.json()


def main() -> None:
    try:
        ctx = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        ctx = {}
    task_id = ctx.get("task_id", "")
    instances = ctx.get("worlds_instances") or {}
    try:
        checks = SPECS.get(task_id)
        if checks is None:
            raise KeyError(f"no worlds grader for {task_id!r}")
        cps, score, maxs = [], 0.0, 0.0
        for app, path, predicate, weight, label in checks:
            url = instances.get(app)
            ok = bool(url) and predicate(_get(url, path))
            maxs += weight
            score += weight if ok else 0.0
            cps.append({"name": label, "passed": ok, "detail": label, "weight": weight})
        print(json.dumps({
            "score": round(score, 2), "max_score": round(maxs, 2), "checkpoints": cps,
            "log": f"{task_id}: {score:g}/{maxs:g} — " + "; ".join(
                f"{'PASS' if c['passed'] else 'FAIL'} {c['name']}" for c in cps),
        }))
    except Exception as e:  # noqa: BLE001 — never crash the runner's grade fold
        print(json.dumps({
            "score": 0.0, "max_score": 100.0, "checkpoints": [],
            "log": f"{task_id}: worlds grader error {type(e).__name__}: {e}",
        }))


if __name__ == "__main__":
    main()
