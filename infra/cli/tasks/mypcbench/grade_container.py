#!/usr/bin/env python3
"""Host-side deterministic grader for MyPCBench action tasks.

The runner invokes this as `uv run python grade_container.py` with a JSON context
on stdin (task_id, ...). It checks the real app state in the apps container
(`mypc-apps`) via `docker exec ... sqlite3` and prints ONE JSON line:
  {"score", "max_score", "checkpoints":[...], "log"}

Each task is worth 100; the check is a single COUNT query against the seeded
per-VM DB at /data/vms/<VM_ID>/<app>.sqlite, with an "absolute post-condition"
predicate (a new row carrying a unique marker, or a flag/status flipped on a
specific existing row). This is the deterministic analog of MyPCBench's offline
LLM judge — we read ground truth instead of judging prose.

To add a task: append one entry to SPECS keyed by the task's `id`. Markers were
chosen to be absent from the pristine seed (create/compose) or to target a
specific seeded row (flag/status), so graders are baseline-free and re-run-safe.
"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import Callable

CONTAINER = "mypc-apps"
VM_ID = "michael_scott"

# task_id -> (app, count-query, predicate(n)->bool, human label)
SPECS: dict[str, tuple[str, str, Callable[[int], bool], str]] = {
    # --- calendar: create event with a unique title ---
    "mypc-calendar-improv": (
        "hoolicalendar", "SELECT count(*) FROM events WHERE title LIKE '%Improv Night%';",
        lambda n: n >= 1, "events titled 'Improv Night'"),
    "mypc-calendar-dentist": (
        "hoolicalendar", "SELECT count(*) FROM events WHERE title LIKE '%Dentist Checkup%';",
        lambda n: n >= 1, "events titled 'Dentist Checkup'"),
    "mypc-calendar-teamsync": (
        "hoolicalendar", "SELECT count(*) FROM events WHERE title LIKE '%Team Sync%';",
        lambda n: n >= 1, "events titled 'Team Sync'"),
    "mypc-calendar-pto": (
        "hoolicalendar", "SELECT count(*) FROM events WHERE title LIKE '%PTO Day%';",
        lambda n: n >= 1, "events titled 'PTO Day'"),
    # --- mail: flag a specific email / send a new one ---
    "mypc-mail-star-tax": (
        "mail", "SELECT count(*) FROM mail_entry_state s JOIN emails e ON e.id=s.email_id "
                "WHERE s.starred=1 AND e.subject LIKE '%NY State Tax%';",
        lambda n: n >= 1, "starred NY-State-Tax emails"),
    "mypc-mail-read-eticket": (
        "mail", "SELECT count(*) FROM mail_entry_state s JOIN emails e ON e.id=s.email_id "
                "WHERE s.read=1 AND e.subject LIKE '%E-Ticket Receipt: Dinoco%';",
        lambda n: n >= 1, "read Dinoco e-ticket emails"),
    "mypc-mail-important-spectrum": (
        "mail", "SELECT count(*) FROM mail_entry_state s JOIN emails e ON e.id=s.email_id "
                "WHERE s.important=1 AND e.subject LIKE '%Spectrum Internet%';",
        lambda n: n >= 1, "important-flagged Spectrum emails"),
    "mypc-mail-compose-pam": (
        "mail", "SELECT count(*) FROM emails WHERE subject LIKE '%Lunch Friday%' "
                "AND to_email LIKE '%pam%';",
        lambda n: n >= 1, "sent 'Lunch Friday?' emails to Pam"),
    # --- shop: add to cart (isolated app; count delta over seed baseline of 4) ---
    "mypc-shop-cart": (
        "hoolishop", "SELECT count(*) FROM cart_items;",
        lambda n: n > 4, "cart_items (seed baseline 4)"),
    # --- sprintboard: create a task / move a task to done ---
    "mypc-sprint-create": (
        "sprintboard", "SELECT count(*) FROM tasks WHERE title LIKE '%Restock the breakroom%';",
        lambda n: n >= 1, "tasks titled 'Restock the breakroom'"),
    "mypc-sprint-trophies-done": (
        "sprintboard", "SELECT count(*) FROM tasks WHERE title LIKE 'Order custom trophies%' "
                       "AND status='done';",
        lambda n: n >= 1, "'Order custom trophies' tasks in done"),
    # --- vaultbank: send a Zelle / schedule a bill (distinctive amount marker) ---
    "mypc-bank-zelle-pam": (
        "vaultbank", "SELECT count(*) FROM zelle_transfers WHERE direction='sent' "
                     "AND contact_name LIKE 'Pam%' AND amount=12.34;",
        lambda n: n >= 1, "Zelle $12.34 sent to Pam"),
    "mypc-bank-bill-netflix": (
        "vaultbank", "SELECT count(*) FROM bill_payments WHERE payee LIKE 'Netflix%' "
                     "AND amount=9.99;",
        lambda n: n >= 1, "$9.99 Netflix bill payments"),
    # --- batbucks: place a buy order (distinctive share count marker) ---
    "mypc-batbucks-buy-aapl": (
        "batbucks", "SELECT count(*) FROM orders WHERE ticker='AAPL' AND side='buy' "
                    "AND shares=7;",
        lambda n: n >= 1, "AAPL buy orders for 7 shares"),
}


def count(app: str, query: str) -> int:
    db = f"/data/vms/{VM_ID}/{app}.sqlite"
    out = subprocess.run(
        ["docker", "exec", CONTAINER, "sqlite3", db, query],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return int((out.stdout or "").strip() or "0")
    except ValueError:
        return 0


def main() -> None:
    try:
        ctx = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        ctx = {}
    task_id = ctx.get("task_id", "")
    try:
        spec = SPECS.get(task_id)
        if spec is None:
            raise KeyError(f"no grader registered for {task_id!r}")
        app, query, predicate, label = spec
        n = count(app, query)
        passed = predicate(n)
        detail = f"{label} = {n}"
        print(json.dumps({
            "score": 100.0 if passed else 0.0,
            "max_score": 100.0,
            "checkpoints": [{"name": task_id, "passed": passed, "detail": detail, "weight": 100}],
            "log": f"{task_id}: {'PASS' if passed else 'FAIL'} — {detail}",
        }))
    except Exception as e:  # noqa: BLE001 — never crash the runner's grade fold
        print(json.dumps({
            "score": 0.0, "max_score": 100.0, "checkpoints": [],
            "log": f"{task_id}: grader error {type(e).__name__}: {e}",
        }))


if __name__ == "__main__":
    main()
