from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import traceback
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol

from benchmark.agent import ClaudeAgent
from benchmark.agent_yutori import NavigatorAgent
from benchmark.config import MAX_STEPS, MODEL_CONFIG
from benchmark.env import Env, make_env
from benchmark.grading import fold_script_result, normalize_score
from benchmark.log import get_logger
from benchmark.task import TASKS_ROOT, Task
from benchmark import worlds

log = get_logger()

# Chars of model "thinking"/text to log per step (collapsed to one line). 0 disables.
LOG_THINK_CHARS = int(os.getenv("MACOSWORLD_LOG_THINK_CHARS", "240"))


def _collapse(text: str, limit: int) -> str:
    """Whitespace-collapsed, truncated single line — keeps multi-line model text greppable."""
    s = " ".join(text.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def summarize_action(a: dict) -> str:
    """One-line summary of a dispatched action: `name {input} -> ok | FAIL: msg`."""
    name = a.get("action") or "?"
    detail = {k: v for k, v in (a.get("input") or {}).items() if k != "action"}
    blob = json.dumps(detail, separators=(",", ":"), default=str)
    if len(blob) > 160:
        blob = blob[:159] + "…"
    outcome = "ok" if a.get("ok", True) else f"FAIL: {str(a.get('msg', '')).strip()[:100]}"
    return f"{name} {blob} -> {outcome}"


# Anthropic and Yutori agent classes share the public surface the runner uses
# (step / total_input_tokens / total_output_tokens / save_logs / steps).
Agent = ClaudeAgent | NavigatorAgent

if TYPE_CHECKING:
    from benchmark.env.kvm import KvmFleet


class _SteppableAgent(Protocol):
    def step(self, step_index: int, max_steps: int, instruction: str): ...


def drive_agent(
    agent: _SteppableAgent,
    instruction: str,
    max_steps: int,
    *,
    on_step: Callable[[int, object], None] | None = None,
) -> str:
    """Run the agent step loop until terminal status or max_steps.

    Pure control flow over `agent.step()` — no env/fleet/network coupling — so it
    can be unit-tested with a fake agent. `on_step(step_index, rec)` is invoked
    after each step (used by run_task for per-step logging). Returns the terminal
    status string ("done" | "fail" | "max_steps" | ...).
    """
    for step in range(1, max_steps + 1):
        rec = agent.step(step, max_steps, instruction)
        if on_step is not None:
            on_step(step, rec)
        if rec.status != "unfinished":
            return rec.status
    return "max_steps"


@dataclass
class TaskResult:
    task_id: str
    category: str
    model: str
    score: float  # int for legacy binary tasks; float for weighted checkpoints
    max_score: float
    n_steps: int
    status: str  # "done" | "fail" | "max_steps" | "error"
    duration_s: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    sandbox_id: str
    error: str = ""
    # Multi-trial (pass@k) fields. Additive with defaults so single-trial runs and
    # the dashboard's flat summary.json rows are unchanged. base_task_id is always
    # task.id; trial is the 0-based trial index; passed is filled by the CLI layer
    # (which owns the pass threshold), left None here.
    base_task_id: str = ""
    trial: int = 0
    passed: bool | None = None


def _cost_usd(model_id: str, in_tok: int, out_tok: int) -> float:
    cfg = MODEL_CONFIG[model_id]
    return round((in_tok / 1_000_000) * cfg.input_per_mtok + (out_tok / 1_000_000) * cfg.output_per_mtok, 6)


def build_work_items(tasks: list[Task], trials: int) -> list[tuple[Task, int | None]]:
    """Cross product of tasks×trials as (task, trial) work items, TRIAL-MAJOR.

    Order is trial 0 of every task, then trial 1 of every task, ... so a partial
    run still covers every task once before doubling up. When ``trials == 1`` the
    trial component is ``None`` so callers reproduce today's single-trial output
    layout byte-for-byte; otherwise it's the 0-based trial index.
    """
    if trials <= 1:
        return [(t, None) for t in tasks]
    return [(t, k) for k in range(trials) for t in tasks]


def aggregate_trials(results: list[TaskResult], n_trials: int, pass_threshold: float) -> dict:
    """Aggregate per-trial TaskResults into a pass@k report grouped by base task.

    A trial passes when ``max_score > 0`` and ``score / max_score >= pass_threshold``;
    a max_score of 0 (e.g. an errored rollout that never graded) can never pass.
    Trials are grouped by ``base_task_id`` and ordered by trial index.
    """
    by_task: dict[str, list[TaskResult]] = {}
    for r in results:
        by_task.setdefault(r.base_task_id, []).append(r)

    tasks: dict[str, dict] = {}
    model = results[0].model if results else ""
    for base_id, rs in by_task.items():
        rs = sorted(rs, key=lambda r: r.trial)
        trials = []
        passes = 0
        for r in rs:
            passed = r.max_score > 0 and (r.score / r.max_score) >= pass_threshold
            if passed:
                passes += 1
            trials.append(
                {
                    "trial": r.trial,
                    "dir": r.task_id,
                    "score": r.score,
                    "max_score": r.max_score,
                    "passed": passed,
                    "status": r.status,
                    "n_steps": r.n_steps,
                    "duration_s": r.duration_s,
                    "cost_usd": r.cost_usd,
                }
            )
        tasks[base_id] = {
            "n_trials": len(rs),
            "passes": passes,
            "pass_rate": (passes / len(rs)) if rs else 0.0,
            "trials": trials,
        }

    return {
        "n_trials": n_trials,
        "pass_threshold": pass_threshold,
        "model": model,
        "tasks": tasks,
    }


def run_grading_script(task: Task, env: "Env", task_dir: Path) -> dict | None:
    """Run a task's host-side grading_script and return its parsed JSON payload.

    The script runs on the LINUX HOST via `uv run python <script>`, with a JSON
    context on stdin giving it the guest SSH coordinates (so it can scp/ssh to
    pull artifacts), the task id, and a fresh scratch temp dir. It must print one
    JSON line: {"score","max_score","checkpoints":[...],"log"}.

    Returns None when the task has no grading_script (the common case), so legacy
    tasks are unaffected.
    """
    script = task.resolve_grading_script()
    if script is None:
        return None
    if not script.exists():
        log.warning(f"grading_script not found: {script}")
        return {"score": 0, "max_score": 0, "checkpoints": [], "log": f"script not found: {script}"}

    conn = env.guest_conn() if hasattr(env, "guest_conn") else None
    with tempfile.TemporaryDirectory(prefix=f"grade-{task.id}-") as scratch:
        ctx = {
            "task_id": task.id,
            "category": task.category,
            "guest_conn": conn,
            "scratch_dir": scratch,
            "task_dir": str(task.task_dir) if task.task_dir else None,
        }
        log.info(f"grading_script: {script.name}")
        try:
            proc = subprocess.run(
                ["uv", "run", "python", str(script)],
                input=json.dumps(ctx),
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            log.warning("grading_script timed out")
            return {"score": 0, "max_score": 0, "checkpoints": [], "log": "grading_script timeout"}
        # The verifier's JSON is the LAST non-empty stdout line (so it can also log freely).
        out_lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
        for line in reversed(out_lines):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        log.warning(f"grading_script produced no JSON (rc={proc.returncode}): {proc.stderr.strip()[:200]}")
        return {"score": 0, "max_score": 0, "checkpoints": [], "log": f"no JSON output (rc={proc.returncode})"}


def run_worlds_grading(task: Task, urls: dict[str, str]) -> dict | None:
    """Grade a worlds rollout via the REST grader, passing the live instance URLs."""
    script = TASKS_ROOT / "mypcbench" / "grade_worlds.py"
    if not script.exists():
        return {"score": 0, "max_score": 0, "checkpoints": [], "log": f"grader missing: {script}"}
    ctx = {"task_id": task.id, "category": task.category, "worlds_instances": urls}
    log.info(f"grading_script: grade_worlds.py ({', '.join(urls)})")
    try:
        proc = subprocess.run(
            ["uv", "run", "python", str(script)],
            input=json.dumps(ctx), capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return {"score": 0, "max_score": 0, "checkpoints": [], "log": "worlds grader timeout"}
    for line in reversed([ln for ln in (proc.stdout or "").splitlines() if ln.strip()]):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return {"score": 0, "max_score": 0, "checkpoints": [], "log": f"no JSON (rc={proc.returncode})"}


def run_task(
    model_id: str,
    task: Task,
    run_dir: Path,
    *,
    backend: str = "use-computer",
    fleet: "KvmFleet | None" = None,
    trial: int | None = None,
    apps: str = "docker",
) -> TaskResult:
    # When running multiple trials, each rollout gets a FLAT sibling dir/task_id
    # `<task.id>__t<NN>` under run_dir (nested dirs break the dashboard's routing).
    # trial=None reproduces the byte-identical single-trial layout of today.
    out_id = task.id if trial is None else f"{task.id}__t{trial:02d}"
    task_dir = run_dir / out_id
    (task_dir / "context").mkdir(parents=True, exist_ok=True)

    # Tag every line for this task so concurrent fleet output stays greppable.
    # Upgraded to include the guest/sandbox id once the env exists.
    tag = f"{task.category}/{out_id[:8]}"
    log.info(f"[{tag}] START {model_id} :: {task.instruction[:90]}")

    t0 = time.time()
    env: Env | None = None
    slot = None
    wsession: "worlds.WorldsSession | None" = None
    status = "error"
    error_str = ""
    score: float = 0
    # Fallback max if grading never runs (error before grade): the task's declared
    # weights, defaulting to the legacy 100 so existing tasks/dashboards are stable.
    max_score: float = sum(w for _, w in task.grading_command) or 100
    grade_log: list[dict] = []
    agent: Agent | None = None
    sandbox_id = ""

    try:
        if backend == "kvm":
            if fleet is None:
                raise ValueError("kvm backend requires a fleet")
            slot = fleet.acquire()
            env = make_env("kvm", slot=slot)
        else:
            env = make_env(backend)
        sandbox_id = env.sandbox_id
        tag = f"{sandbox_id} {task.id[:8]}"
        log.info(
            f"[{tag}] sandbox {env._real_w}x{env._real_h} -> 1024x768 "
            f"(scale {env.scale_x:.2f}x{env.scale_y:.2f})"
        )

        # Worlds mode: spin up per-rollout hosted instances and rewrite the task's
        # localhost:<port> URLs to the live instance URLs (task JSON stays untouched).
        rtask = task
        if apps == "worlds":
            ports = worlds.ports_in(task.pre_command, task.instruction)
            if not ports:
                raise ValueError(f"{task.id}: --apps worlds but no localhost:<port> in task")
            wsession = worlds.WorldsSession(ports).open()
            rtask = replace(
                task,
                instruction=wsession.render(task.instruction),
                pre_command=wsession.render(task.pre_command, autologin=True),
            )

        if rtask.pre_command:
            env.run_pre_command(rtask)
        if task.before_action_delay:
            time.sleep(task.before_action_delay)

        if MODEL_CONFIG[model_id].provider == "yutori":
            agent = NavigatorAgent(model_id, env, task_dir)
        else:
            agent = ClaudeAgent(model_id, env, task_dir)

        def _on_step(step: int, rec) -> None:
            log.info(
                f"[{tag}] step {step:02d}: {len(rec.actions)} actions, "
                f"{rec.input_tokens}in/{rec.output_tokens}out, {rec.latency_s:.1f}s, "
                f"status={rec.status}"
            )
            # Agent thinking/text for this step (what the model reasoned before acting).
            if LOG_THINK_CHARS > 0:
                think = "".join(getattr(rec, "text_chunks", []) or []).strip()
                if think:
                    log.info(f"[{tag}]   think: {_collapse(think, LOG_THINK_CHARS)}")
            # The concrete action(s) it took + outcome (failures surface here).
            for a in rec.actions:
                log.info(f"[{tag}]   action: {summarize_action(a)}")

        status = drive_agent(agent, rtask.instruction, MAX_STEPS, on_step=_on_step)

        if task.before_grading_delay:
            time.sleep(task.before_grading_delay)
        score, max_score, grade_log = env.grade(rtask)
        # Host-side verifier folds additively into the in-guest checkpoints. Worlds
        # mode reads the live instance REST APIs; docker mode runs the task's script.
        if apps == "worlds" and wsession is not None:
            script_result = run_worlds_grading(task, wsession.url_by_app)
        else:
            script_result = run_grading_script(task, env, task_dir)
        score, max_score, grade_log = fold_script_result(score, max_score, grade_log, script_result)
        score = normalize_score(score)
        max_score = normalize_score(max_score)
        log.info(f"[{tag}] score: {score}/{max_score}")
    except Exception as e:
        log.error(f"[{tag}] ERROR {type(e).__name__}: {e}\n{traceback.format_exc()}")
        error_str = f"{type(e).__name__}: {e}"
    finally:
        if wsession is not None:
            try:
                wsession.close()
            except Exception as e:
                log.warning(f"[{tag}] worlds teardown failed: {e}")
        if agent is not None:
            try:
                agent.save_logs()
            except Exception as e:
                log.warning(f"[{tag}] save_logs failed: {e}")
        if env is not None:
            try:
                env.close()
            except Exception as e:
                log.warning(f"[{tag}] env.close failed: {e}")
        elif slot is not None:
            # make_env failed after we acquired a slot — return it so the pool
            # doesn't leak a guest. (When env exists, env.close() releases it.)
            try:
                slot.release()
            except Exception as e:
                log.warning(f"[{tag}] slot.release failed: {e}")

    in_tok = agent.total_input_tokens if agent else 0
    out_tok = agent.total_output_tokens if agent else 0
    result = TaskResult(
        task_id=out_id,
        category=task.category,
        model=model_id,
        score=score,
        max_score=max_score,
        n_steps=len(agent.steps) if agent else 0,
        status=status,
        duration_s=round(time.time() - t0, 2),
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=_cost_usd(model_id, in_tok, out_tok),
        sandbox_id=sandbox_id,
        error=error_str,
        base_task_id=task.id,
        trial=trial or 0,
    )
    (task_dir / "result.json").write_text(json.dumps(asdict(result) | {"grade_log": grade_log}, indent=2))
    return result
