"""Unit tests for multi-trial (pass@k) aggregation — pure, no I/O, no network.

Exercise the pure seams added for `--trials`: `build_work_items` (task×trial
cross product, trial-major) and `aggregate_trials` (grouping + pass threshold).
Run: `cd infra/cli && uv run --group dev pytest tests/test_trials_aggregate.py`.
"""
from __future__ import annotations

from benchmark.runner import TaskResult, aggregate_trials, build_work_items
from benchmark.task import Task


def _task(tid: str) -> Task:
    return Task(
        id=tid,
        category="cat",
        instruction="do a thing",
        pre_command="",
        before_action_delay=0,
        before_grading_delay=0,
        grading_command=[("check", 100)],
        raw={},
    )


def _result(base_id: str, trial: int, score: float, max_score: float = 100.0,
            status: str = "done") -> TaskResult:
    return TaskResult(
        task_id=f"{base_id}__t{trial:02d}",
        category="cat",
        model="claude-test",
        score=score,
        max_score=max_score,
        n_steps=5,
        status=status,
        duration_s=1.0,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        sandbox_id="sb",
        base_task_id=base_id,
        trial=trial,
    )


# --- build_work_items -------------------------------------------------------


def test_single_trial_passes_none():
    """trials==1 -> trial component is None (byte-identical single-trial layout)."""
    a, b = _task("a"), _task("b")
    items = build_work_items([a, b], 1)
    assert items == [(a, None), (b, None)]


def test_trials_one_each_item_trial_is_none():
    """The pure seam run_task receives trial=None when trials==1."""
    a = _task("a")
    items = build_work_items([a], 1)
    assert all(trial is None for _, trial in items)


def test_work_items_are_trial_major():
    """trials>1 -> trial 0 of every task, then trial 1 of every task, ..."""
    a, b = _task("a"), _task("b")
    items = build_work_items([a, b], 3)
    assert items == [
        (a, 0), (b, 0),
        (a, 1), (b, 1),
        (a, 2), (b, 2),
    ]


# --- aggregate_trials grouping ----------------------------------------------


def test_aggregate_groups_two_tasks_three_trials():
    results = [
        _result("a", 0, 100.0), _result("a", 1, 0.0), _result("a", 2, 100.0),
        _result("b", 0, 0.0), _result("b", 1, 0.0), _result("b", 2, 0.0),
    ]
    agg = aggregate_trials(results, n_trials=3, pass_threshold=0.99)
    assert agg["n_trials"] == 3
    assert agg["pass_threshold"] == 0.99
    assert agg["model"] == "claude-test"
    assert set(agg["tasks"]) == {"a", "b"}

    a = agg["tasks"]["a"]
    assert a["n_trials"] == 3
    assert a["passes"] == 2
    assert a["pass_rate"] == 2 / 3
    assert [t["trial"] for t in a["trials"]] == [0, 1, 2]

    b = agg["tasks"]["b"]
    assert b["passes"] == 0
    assert b["pass_rate"] == 0.0


def test_trials_ordered_by_trial_index():
    """Out-of-order results are sorted by trial within a task."""
    results = [
        _result("a", 2, 100.0), _result("a", 0, 100.0), _result("a", 1, 100.0),
    ]
    agg = aggregate_trials(results, n_trials=3, pass_threshold=0.5)
    assert [t["trial"] for t in agg["tasks"]["a"]["trials"]] == [0, 1, 2]


def test_trial_record_shape():
    agg = aggregate_trials([_result("a", 0, 100.0)], n_trials=1, pass_threshold=0.99)
    t = agg["tasks"]["a"]["trials"][0]
    assert set(t) == {
        "trial", "dir", "score", "max_score", "passed",
        "status", "n_steps", "duration_s", "cost_usd",
    }
    assert t["dir"] == "a__t00"


# --- pass threshold edges ---------------------------------------------------


def test_threshold_edge_98_9_fails_at_099_passes_at_05():
    r = _result("a", 0, 98.9, max_score=100.0)
    high = aggregate_trials([r], n_trials=1, pass_threshold=0.99)
    low = aggregate_trials([r], n_trials=1, pass_threshold=0.5)
    assert high["tasks"]["a"]["trials"][0]["passed"] is False
    assert high["tasks"]["a"]["passes"] == 0
    assert low["tasks"]["a"]["trials"][0]["passed"] is True
    assert low["tasks"]["a"]["passes"] == 1


def test_exact_threshold_passes():
    """score/max == threshold counts as a pass (>=)."""
    r = _result("a", 0, 50.0, max_score=100.0)
    agg = aggregate_trials([r], n_trials=1, pass_threshold=0.5)
    assert agg["tasks"]["a"]["trials"][0]["passed"] is True


def test_max_score_zero_never_passes():
    """An errored rollout that never graded (max_score==0) can't pass at any threshold."""
    r = _result("a", 0, 0.0, max_score=0.0, status="error")
    agg = aggregate_trials([r], n_trials=1, pass_threshold=0.99)
    assert agg["tasks"]["a"]["trials"][0]["passed"] is False
    assert agg["tasks"]["a"]["passes"] == 0


# --- dir naming -------------------------------------------------------------


def test_dir_naming_zero_padded_two_digits():
    results = [_result("widget", 0, 100.0), _result("widget", 7, 100.0)]
    agg = aggregate_trials(results, n_trials=8, pass_threshold=0.99)
    dirs = [t["dir"] for t in agg["tasks"]["widget"]["trials"]]
    assert "widget__t00" in dirs
    assert "widget__t07" in dirs


def test_empty_results_is_safe():
    agg = aggregate_trials([], n_trials=3, pass_threshold=0.99)
    assert agg["tasks"] == {}
    assert agg["model"] == ""
