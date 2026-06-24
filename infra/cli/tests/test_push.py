"""Push mapping + orchestration — hermetic, no network (fake backend client)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from mw import push


class FakeClient:
    def __init__(self):
        self.calls = []
        self._run = self._rollout = self._task = 0
        self.rollout_patches: dict[int, dict] = {}
        self.run_patches: dict[int, dict] = {}
        self.uploaded: list[tuple] = []

    def whoami(self):
        return {"id": 7, "username": "tester"}

    def create_task(self, payload):
        self._task += 1
        self.calls.append(("create_task", payload))
        return {"id": 100 + self._task}

    def create_run(self, payload):
        self._run += 1
        self.calls.append(("create_run", payload))
        return {"id": self._run}

    def patch_run(self, run_id, payload):
        self.run_patches[run_id] = payload
        return {"id": run_id}

    def create_rollout(self, payload):
        self._rollout += 1
        self.calls.append(("create_rollout", payload))
        return {"id": 500 + self._rollout}

    def patch_rollout(self, rollout_id, payload):
        self.rollout_patches[rollout_id] = payload
        return {"id": rollout_id}

    def presign_artifact(self, rollout_id, filename, content_type):
        return {"upload_url": f"https://s3/{rollout_id}/{filename}", "s3_key": filename}

    def upload_artifact(self, upload_url, path, content_type):
        self.uploaded.append((upload_url, str(path), content_type))


# -- mapping ---------------------------------------------------------------


def test_outcome_error():
    status, result, error = push.rollout_outcome({"status": "error", "error": "boom"})
    assert status == "failed" and result is None and error["message"] == "boom"


def test_outcome_pass_from_score():
    status, result, error = push.rollout_outcome({"status": "done", "score": 100, "max_score": 100})
    assert status == "completed"
    assert result == {"passed": True, "agent_passed": True}
    assert error is None


def test_outcome_fail_when_not_done():
    status, result, _ = push.rollout_outcome({"status": "max_steps", "score": 0, "max_score": 100})
    assert status == "completed"
    assert result == {"passed": False, "agent_passed": False}


def test_outcome_respects_passed_flag():
    _, result, _ = push.rollout_outcome(
        {"status": "done", "score": 1, "max_score": 100, "passed": True}
    )
    assert result["passed"] is True


def test_is_pass():
    assert push.is_pass({"status": "done", "score": 100, "max_score": 100})
    assert not push.is_pass({"status": "error"})
    assert push.is_pass({"passed": True})
    assert not push.is_pass({"passed": False, "score": 100, "max_score": 100})


# -- orchestration ---------------------------------------------------------


def _make_run(tmp_path):
    run = tmp_path / "model-x-1"
    task_dir = run / "taskA"
    (task_dir / "context").mkdir(parents=True)
    (task_dir / "trajectory.jsonl").write_text('{"step":1}\n')
    (task_dir / "result.json").write_text(json.dumps({"task_id": "taskA", "grade_log": [{"cmd": "x"}]}))
    (task_dir / "context" / "step_001.png").write_bytes(b"png")
    summary = [
        {
            "task_id": "taskA",
            "base_task_id": "taskA",
            "category": "web",
            "model": "model-x",
            "score": 100,
            "max_score": 100,
            "n_steps": 5,
            "status": "done",
            "duration_s": 1.5,
            "input_tokens": 10,
            "output_tokens": 20,
            "cost_usd": 0.1,
            "sandbox_id": "s1",
            "passed": True,
        }
    ]
    (run / "summary.json").write_text(json.dumps(summary))
    return run


def _patch_deps(monkeypatch, tmp_path):
    monkeypatch.setattr(push, "MANIFEST_PATH", tmp_path / "manifest.json")
    monkeypatch.setattr(
        push,
        "load_tasks",
        lambda ids=None: [
            SimpleNamespace(
                id="taskA",
                instruction="do it",
                category="web",
                pre_command="",
                grading_command=[("cmd", 100)],
            )
        ],
    )


def test_push_run_dir(tmp_path, monkeypatch):
    run = _make_run(tmp_path)
    _patch_deps(monkeypatch, tmp_path)
    client = FakeClient()

    info = push.push_run_dir(client, run, delete_after=False)

    assert info == {"run_id": 1, "rollouts": 1, "passed": 1}
    assert any(c[0] == "create_task" for c in client.calls)

    patch = client.rollout_patches[501]
    assert patch["status"] == "completed"
    assert patch["result"] == {"passed": True, "agent_passed": True}
    assert patch["tokens"] == {"input": 10, "output": 20, "total": 30}
    assert patch["metadata"]["artifacts"]["trajectory"] == "trajectory.jsonl"
    assert patch["metadata"]["artifacts"]["screenshots"] == ["context/step_001.png"]
    assert patch["metadata"]["grade_log"] == [{"cmd": "x"}]

    assert client.run_patches[1] == {
        "status": "completed",
        "total_rollouts": 1,
        "passed_rollouts": 1,
        "total_tokens": 30,
    }
    assert len(client.uploaded) == 3
    assert run.exists()


def test_push_run_dir_deletes_after(tmp_path, monkeypatch):
    run = _make_run(tmp_path)
    _patch_deps(monkeypatch, tmp_path)
    push.push_run_dir(FakeClient(), run, delete_after=True)
    assert not run.exists()
