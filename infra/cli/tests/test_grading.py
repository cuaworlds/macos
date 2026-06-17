"""Unit tests for the substrate-blind grading helpers (benchmark.grading).

These exercise the checkpoint contract with a stub exec callable — no live VM,
no SSH, no SDK. Run: `uv run pytest infra/cli/tests/test_grading.py`.
"""
from __future__ import annotations

from benchmark.grading import (
    fold_script_result,
    grade_checkpoints,
    normalize_score,
)


def _stub_exec(outputs: dict[str, str], rcs: dict[str, int] | None = None):
    """Build an exec_fn that returns canned stdout (and optional rc) per command."""
    rcs = rcs or {}

    def _exec(cmd: str) -> tuple[int, str, str]:
        return rcs.get(cmd, 0), outputs.get(cmd, ""), ""

    return _exec


# --- Feature 1: weighted checkpoint scoring ---------------------------------


def test_legacy_single_100_true_is_binary_100():
    """A legacy [cmd, 100] task that passes grades to exactly 100/100."""
    cmds = [("check", 100)]
    exec_fn = _stub_exec({"check": "True"})
    score, max_score, log = grade_checkpoints(cmds, exec_fn)
    assert score == 100.0
    assert max_score == 100.0
    assert normalize_score(score) == 100  # int, not float
    assert isinstance(normalize_score(score), int)
    assert log[0]["credit"] == 100.0


def test_legacy_single_100_false_is_binary_0():
    cmds = [("check", 100)]
    exec_fn = _stub_exec({"check": "False"})
    score, max_score, log = grade_checkpoints(cmds, exec_fn)
    assert score == 0.0
    assert max_score == 100.0
    assert normalize_score(score) == 0


def test_legacy_garbage_output_is_0():
    """Non-true, non-float stdout earns nothing (old code keyed on 'true')."""
    cmds = [("check", 100)]
    exec_fn = _stub_exec({"check": "command not found"})
    score, _, _ = grade_checkpoints(cmds, exec_fn)
    assert score == 0.0


def test_multi_checkpoint_sum():
    """All entries evaluated (not just value==100); credits sum."""
    cmds = [("a", 40), ("b", 35), ("c", 25)]
    exec_fn = _stub_exec({"a": "true", "b": "false", "c": "TRUE"})
    score, max_score, log = grade_checkpoints(cmds, exec_fn)
    assert max_score == 100.0
    assert score == 65.0  # 40 + 0 + 25
    assert [e["credit"] for e in log] == [40.0, 0.0, 25.0]


def test_max_score_below_100():
    cmds = [("a", 30), ("b", 20)]
    exec_fn = _stub_exec({"a": "true", "b": "true"})
    score, max_score, _ = grade_checkpoints(cmds, exec_fn)
    assert score == 50.0
    assert max_score == 50.0


def test_float_partial_credit():
    """A float in [0,1] yields continuous partial credit weight*float."""
    cmds = [("a", 50), ("b", 50)]
    exec_fn = _stub_exec({"a": "0.5", "b": "0.8"})
    score, max_score, log = grade_checkpoints(cmds, exec_fn)
    assert max_score == 100.0
    assert score == 65.0  # 50*0.5 + 50*0.8
    assert log[0]["credit"] == 25.0
    assert log[1]["credit"] == 40.0


def test_float_boundaries():
    cmds = [("zero", 100), ("one", 100)]
    exec_fn = _stub_exec({"zero": "0", "one": "1.0"})
    score, _, _ = grade_checkpoints(cmds, exec_fn)
    assert score == 100.0  # 0 + 100


def test_float_out_of_range_is_zero():
    """Floats outside [0,1] (e.g. an errant exit-code echo) earn nothing."""
    cmds = [("a", 100)]
    exec_fn = _stub_exec({"a": "1.5"})
    score, _, _ = grade_checkpoints(cmds, exec_fn)
    assert score == 0.0


def test_timeout_rc124_is_zero_and_logged():
    cmds = [("a", 100)]
    exec_fn = _stub_exec({"a": "true"}, rcs={"a": 124})
    score, max_score, log = grade_checkpoints(cmds, exec_fn)
    assert score == 0.0
    assert max_score == 100.0
    assert log[0]["error"] == "exec timeout"


def test_exec_exception_is_caught():
    def _boom(cmd: str):
        raise RuntimeError("ssh died")

    cmds = [("a", 100)]
    score, max_score, log = grade_checkpoints(cmds, _boom)
    assert score == 0.0
    assert max_score == 100.0
    assert "ssh died" in log[0]["error"]


def test_log_records_breakdown():
    cmds = [("a", 60)]
    exec_fn = _stub_exec({"a": "0.25"})
    _, _, log = grade_checkpoints(cmds, exec_fn)
    entry = log[0]
    assert entry["cmd"] == "a"
    assert entry["weight"] == 60
    assert entry["raw_stdout"] == "0.25"
    assert entry["credit"] == 15.0


# --- normalize_score --------------------------------------------------------


def test_normalize_whole_to_int():
    assert normalize_score(100.0) == 100
    assert isinstance(normalize_score(100.0), int)


def test_normalize_keeps_fraction():
    assert normalize_score(65.5) == 65.5
    assert isinstance(normalize_score(65.5), float)


# --- Feature 2: host-side grading-script folding ----------------------------


def test_fold_none_is_noop():
    base = (50.0, 100.0, [{"cmd": "a", "credit": 50.0}])
    score, max_score, log = fold_script_result(*base, None)
    assert (score, max_score) == (50.0, 100.0)
    assert len(log) == 1


def test_fold_adds_script_contribution():
    base_score, base_max, base_log = 40.0, 60.0, [{"cmd": "a", "credit": 40.0}]
    script = {
        "score": 30.0,
        "max_score": 40.0,
        "checkpoints": [{"name": "fcstd_parses", "weight": 40, "credit": 30}],
        "log": "parsed FreeCAD doc; 3/4 features present",
    }
    score, max_score, log = fold_script_result(base_score, base_max, base_log, script)
    assert score == 70.0  # 40 + 30
    assert max_score == 100.0  # 60 + 40
    # Original checkpoint plus the script checkpoint plus the script log line.
    script_entries = [e for e in log if e.get("source") == "grading_script"]
    assert any(e.get("name") == "fcstd_parses" and e.get("credit") == 30 for e in script_entries)
    assert any("log" in e for e in script_entries)


def test_fold_script_only_task():
    """A task with no grading_command (base 0/0) but a grading_script."""
    script = {"score": 12.5, "max_score": 25.0, "checkpoints": [], "log": ""}
    score, max_score, log = fold_script_result(0.0, 0.0, [], script)
    assert score == 12.5
    assert max_score == 25.0
    assert normalize_score(score) == 12.5


def test_fold_missing_keys_default_zero():
    score, max_score, _ = fold_script_result(10.0, 20.0, [], {"log": "nothing scored"})
    assert score == 10.0
    assert max_score == 20.0


def test_end_to_end_checkpoints_plus_script():
    """In-guest checkpoints + a host script combine into one additive score."""
    cmds = [("file_exists", 20), ("name_ok", 20)]
    exec_fn = _stub_exec({"file_exists": "true", "name_ok": "0.5"})
    score, max_score, log = grade_checkpoints(cmds, exec_fn)  # 20 + 10 = 30 / 40
    script = {"score": 45.0, "max_score": 60.0, "checkpoints": [{"name": "geometry", "weight": 60, "credit": 45}]}
    score, max_score, log = fold_script_result(score, max_score, log, script)
    assert score == 75.0  # 30 + 45
    assert max_score == 100.0  # 40 + 60
    assert normalize_score(score) == 75
