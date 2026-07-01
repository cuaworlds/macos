"""Push a local run directory to the hosted backend.

Maps the CLI's on-disk results (summary.json + per-task result.json, trajectory,
screenshots) onto backend runs/rollouts/tasks and uploads artifacts to S3. The
run directory is the source format for both live runs and retroactive re-push.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable

from benchmark.backend import BackendClient, BackendError
from benchmark.task import Task, load_tasks

ENVIRONMENT = "cuaworld-macos"

CONTENT_TYPES = {".jsonl": "application/x-ndjson", ".json": "application/json", ".png": "image/png"}

Echo = Callable[[str], None]


# -- task registration -----------------------------------------------------


def ensure_tasks(client: BackendClient, tasks: list[Task]) -> dict[str, int]:
    """Upsert tasks in the backend and return the local-id -> backend-id map.

    The backend is keyed on local_task_id and the upsert is idempotent, so this
    keeps no local state — ids are resolved fresh from the remote on every push.
    """
    mapping: dict[str, int] = {}
    for t in tasks:
        task = client.create_task(
            {
                "environment": ENVIRONMENT,
                "prompt": t.instruction,
                "tags": [t.category],
                "metadata": {
                    "local_task_id": t.id,
                    "category": t.category,
                    "pre_command": t.pre_command,
                    "grading_command": [list(g) for g in t.grading_command],
                },
            }
        )
        mapping[t.id] = task["id"]
    return mapping


# -- result -> rollout mapping (pure) --------------------------------------


def rollout_outcome(result: dict) -> tuple[str, dict | None, dict | None]:
    """(status, result, error) for a rollout from a local result row.

    A local `error` is an execution failure -> status=failed. Otherwise the
    rollout completed; pass/fail lives in `result.passed`.
    """
    if result.get("status") == "error":
        return "failed", None, {"message": result.get("error") or "execution error", "type": "execution"}
    score = result.get("score") or 0
    max_score = result.get("max_score") or 0
    passed = result.get("passed")
    if passed is None:
        passed = max_score > 0 and score >= max_score
    return "completed", {"passed": bool(passed), "agent_passed": result.get("status") == "done"}, None


def rollout_metadata(result: dict, artifacts: dict | None) -> dict:
    md = {
        "score": result.get("score"),
        "max_score": result.get("max_score"),
        "n_steps": result.get("n_steps"),
        "category": result.get("category"),
        "base_task_id": result.get("base_task_id"),
        "trial": result.get("trial"),
        "cost_usd": result.get("cost_usd"),
        "sandbox_id": result.get("sandbox_id"),
        "terminal_reason": result.get("status"),
    }
    if result.get("grade_log") is not None:
        md["grade_log"] = result["grade_log"]
    if artifacts:
        md["artifacts"] = artifacts
    return md


def is_pass(result: dict) -> bool:
    if result.get("status") == "error":
        return False
    passed = result.get("passed")
    if passed is not None:
        return bool(passed)
    max_score = result.get("max_score") or 0
    return max_score > 0 and (result.get("score") or 0) >= max_score


# -- artifact upload -------------------------------------------------------


def _content_type(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix, "application/octet-stream")


def _upload(client: BackendClient, rollout_id: int, task_dir: Path, relpath: str) -> None:
    path = task_dir / relpath
    ct = _content_type(path)
    presigned = client.presign_artifact(rollout_id, relpath, ct)
    client.upload_artifact(presigned["upload_url"], path, ct)


def upload_artifacts(client: BackendClient, rollout_id: int, task_dir: Path) -> dict:
    """Upload trajectory, result, and screenshots present in a task dir to S3."""
    artifacts: dict = {}
    if (task_dir / "trajectory.jsonl").exists():
        _upload(client, rollout_id, task_dir, "trajectory.jsonl")
        artifacts["trajectory"] = "trajectory.jsonl"
    if (task_dir / "result.json").exists():
        _upload(client, rollout_id, task_dir, "result.json")
        artifacts["result"] = "result.json"
    context = task_dir / "context"
    screenshots = []
    if context.is_dir():
        for f in sorted(context.iterdir()):
            if f.is_file():
                rel = f"context/{f.name}"
                _upload(client, rollout_id, task_dir, rel)
                screenshots.append(rel)
    if screenshots:
        artifacts["screenshots"] = screenshots
    return artifacts


# -- orchestration ---------------------------------------------------------


def push_run_dir(
    client: BackendClient,
    run_dir: Path,
    *,
    delete_after: bool = True,
    echo: Echo = lambda _: None,
) -> dict:
    """Create a run + its rollouts in the backend, upload artifacts, finalize stats."""
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        raise BackendError(f"no summary.json in {run_dir}")
    results = json.loads(summary_path.read_text())
    if not results:
        echo("(empty run — nothing to push)")
        return {}

    model = results[0].get("model", "")
    run_meta = {"model": model}
    trials_path = run_dir / "trials.json"
    if trials_path.exists():
        agg = json.loads(trials_path.read_text())
        run_meta.update(n_trials=agg.get("n_trials"), pass_threshold=agg.get("pass_threshold"))

    # Run name persisted at run time; fall back to the dir name for older local runs.
    name_path = run_dir / "run.json"
    name = json.loads(name_path.read_text())["name"] if name_path.exists() else run_dir.name

    client.whoami()  # validate auth up front

    base_ids = sorted({r.get("base_task_id") or r["task_id"] for r in results})
    manifest = ensure_tasks(client, load_tasks(ids=base_ids))

    # Idempotent on session id: drop any prior run before recreating, so a retry
    # after a partial push reconciles into one run instead of forking a new one.
    for stale in client.runs_by_session(run_dir.name):
        client.delete_run(stale["id"])
        echo(f"replaced stale run #{stale['id']} for session {run_dir.name}")

    run = client.create_run(
        {
            "session_id": run_dir.name,
            "name": name,
            "environment": ENVIRONMENT,
            "total_tasks": len(base_ids),
            "status": "running",
            "metadata": run_meta,
        }
    )
    run_id = run["id"]
    echo(f"run #{run_id} ({run_dir.name}) — {len(results)} rollouts")

    for r in results:
        base_id = r.get("base_task_id") or r["task_id"]
        task_dir = run_dir / r["task_id"]
        grade_log = _read_grade_log(task_dir)
        if grade_log is not None:
            r = {**r, "grade_log": grade_log}

        rollout = client.create_rollout(
            {
                "run_id": run_id,
                "task_id": manifest[base_id],
                "model": model,
                "mode": "local",
                "session_id": run_dir.name,
            }
        )
        rollout_id = rollout["id"]
        artifacts = upload_artifacts(client, rollout_id, task_dir)
        status, result_obj, error_obj = rollout_outcome(r)
        in_tok, out_tok = r.get("input_tokens") or 0, r.get("output_tokens") or 0
        patch = {
            "status": status,
            "duration_seconds": r.get("duration_s"),
            "tokens": {"input": in_tok, "output": out_tok, "total": in_tok + out_tok},
            "metadata": rollout_metadata(r, artifacts),
        }
        if result_obj:
            patch["result"] = result_obj
        if error_obj:
            patch["error"] = error_obj
        client.patch_rollout(rollout_id, patch)
        echo(f"  rollout #{rollout_id}  {r['task_id']}  {status}")

    passed = sum(1 for r in results if is_pass(r))
    total_tokens = sum((r.get("input_tokens") or 0) + (r.get("output_tokens") or 0) for r in results)
    client.patch_run(
        run_id,
        {
            "status": "completed",
            "total_rollouts": len(results),
            "passed_rollouts": passed,
            "total_tokens": total_tokens,
        },
    )

    if delete_after:
        shutil.rmtree(run_dir, ignore_errors=True)
        echo(f"removed local {run_dir.name}")
    return {"run_id": run_id, "rollouts": len(results), "passed": passed}


def _read_grade_log(task_dir: Path):
    path = task_dir / "result.json"
    try:
        return json.loads(path.read_text()).get("grade_log")
    except (FileNotFoundError, json.JSONDecodeError):
        return None
