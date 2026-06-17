"""Substrate-blind grading helpers shared by every Env backend.

This module is the abstract grading seam: it never imports anything
substrate-specific (no KVM, no use.computer SDK). Backends call into it with a
plain `exec(cmd) -> (rc, stdout, stderr)` callable, so the same logic runs
identically on KVM today and any future Apple/VZ backend tomorrow.

Checkpoint contract
-------------------
A task's `grading_command` is a list of ``[shell_cmd, weight]`` checkpoints.
Each checkpoint is evaluated independently and is worth ``weight`` points:

  * The command's stdout is stripped and lower-cased.
  * If it is exactly ``"true"`` the checkpoint passes fully -> credit = weight.
  * If it parses as a float in ``[0, 1]`` the checkpoint earns continuous
    partial credit -> credit = weight * float.
  * Otherwise (``"false"``, garbage, timeout, error) -> credit = 0.

The task score is the SUM of every checkpoint's credit; ``max_score`` is the
sum of the weights.

Backward compatibility
-----------------------
Legacy tasks carry a single ``[cmd, 100]`` checkpoint. Under these rules they
still yield exactly 0 or 100 (``"true"`` -> 100, anything else -> 0) and
``max_score`` stays 100 — so existing binary-scored tasks are unchanged.

Host-side grading scripts
-------------------------
``fold_script_result`` merges a host-run verifier's JSON payload (see
``run_grading_script`` in the runner) into the same score/max_score/log shape,
so a task can use ``grading_command`` and/or ``grading_script`` and the
contributions simply add up.
"""
from __future__ import annotations

from typing import Callable

# An exec callable: takes a shell command, returns (rc, stdout, stderr).
# Backends adapt their own SSH/exec surface to this signature.
ExecFn = Callable[[str], "tuple[int, str, str]"]


def _checkpoint_credit(weight: float, stdout: str) -> tuple[float, str]:
    """Map a checkpoint's raw stdout to (credit, normalized_output).

    Returns the points earned for this checkpoint (0..weight) and the
    stripped/lower-cased stdout used to decide it.
    """
    output = (stdout or "").strip().lower()
    if output == "true":
        return weight * 1.0, output
    try:
        frac = float(output)
    except ValueError:
        return 0.0, output
    if 0.0 <= frac <= 1.0:
        return weight * frac, output
    return 0.0, output


def grade_checkpoints(
    grading_command: list[tuple[str, int]],
    exec_fn: ExecFn,
    *,
    timeout: int = 60,
) -> tuple[float, float, list[dict]]:
    """Evaluate every checkpoint and return (score, max_score, log).

    - score: sum of per-checkpoint credit.
    - max_score: sum of weights.
    - log: one dict per checkpoint with cmd, weight, raw_stdout, credit
      (and an `error` key on timeout/exec failure).

    For a single legacy ``[cmd, 100]`` checkpoint this returns (100.0, 100.0)
    on a "true" stdout and (0.0, 100.0) otherwise — i.e. unchanged binary
    behaviour. The runner is responsible for casting a legacy result back to an
    int if it wants to preserve the old `int` score type.
    """
    score = 0.0
    max_score = 0.0
    log: list[dict] = []
    for cmd, weight in grading_command:
        max_score += weight
        try:
            rc, stdout, stderr = exec_fn(cmd)
        except Exception as e:  # noqa: BLE001 — surface as a failed checkpoint
            log.append(
                {
                    "cmd": cmd[:200],
                    "weight": weight,
                    "raw_stdout": "",
                    "credit": 0.0,
                    "error": f"{type(e).__name__}: {e}",
                }
            )
            continue
        if rc == 124:
            log.append(
                {
                    "cmd": cmd[:200],
                    "weight": weight,
                    "raw_stdout": "",
                    "credit": 0.0,
                    "error": "exec timeout",
                }
            )
            continue
        credit, output = _checkpoint_credit(float(weight), stdout)
        score += credit
        log.append(
            {
                "cmd": cmd[:200],
                "weight": weight,
                "raw_stdout": output[:200],
                "credit": credit,
            }
        )
    return score, max_score, log


def normalize_score(score: float) -> float | int:
    """Collapse a whole-number float score to int (keeps legacy result.json tidy).

    A legacy single-100 task grades to 100.0 -> 100 (int), matching the old
    behaviour exactly; fractional weighted scores stay floats.
    """
    rounded = round(score, 6)
    if rounded == int(rounded):
        return int(rounded)
    return rounded


def fold_script_result(
    base_score: float,
    base_max: float,
    base_log: list[dict],
    script_result: dict | None,
) -> tuple[float, float, list[dict]]:
    """Fold a host-side grading-script JSON payload into a grade triple.

    ``script_result`` is the parsed JSON line the script printed, expected to
    look like::

        {"score": <float>, "max_score": <float>,
         "checkpoints": [{"name", "weight", "credit"}, ...],
         "log": "..."}

    Missing/None script_result is a no-op (returns the base triple unchanged),
    so a task using only ``grading_command`` is unaffected. The script's
    checkpoints are appended to the log under a `source: "grading_script"` tag.
    """
    if not script_result:
        return base_score, base_max, base_log
    score = base_score + float(script_result.get("score", 0) or 0)
    max_score = base_max + float(script_result.get("max_score", 0) or 0)
    log = list(base_log)
    for ckpt in script_result.get("checkpoints", []) or []:
        log.append(
            {
                "source": "grading_script",
                "name": ckpt.get("name", ""),
                "weight": ckpt.get("weight"),
                "credit": ckpt.get("credit"),
            }
        )
    if script_result.get("log"):
        log.append({"source": "grading_script", "log": str(script_result["log"])[:1000]})
    return score, max_score, log
