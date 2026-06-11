from __future__ import annotations

import json
import os
import time
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol

from benchmark.agent import ClaudeAgent
from benchmark.agent_yutori import NavigatorAgent
from benchmark.config import MAX_STEPS, MODEL_CONFIG
from benchmark.env import Env, make_env
from benchmark.log import get_logger
from benchmark.task import Task

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
    score: int
    max_score: int
    n_steps: int
    status: str  # "done" | "fail" | "max_steps" | "error"
    duration_s: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    sandbox_id: str
    error: str = ""


def _cost_usd(model_id: str, in_tok: int, out_tok: int) -> float:
    cfg = MODEL_CONFIG[model_id]
    return round((in_tok / 1_000_000) * cfg.input_per_mtok + (out_tok / 1_000_000) * cfg.output_per_mtok, 6)


def run_task(
    model_id: str,
    task: Task,
    run_dir: Path,
    *,
    backend: str = "use-computer",
    fleet: "KvmFleet | None" = None,
) -> TaskResult:
    task_dir = run_dir / task.id
    (task_dir / "context").mkdir(parents=True, exist_ok=True)

    # Tag every line for this task so concurrent fleet output stays greppable.
    # Upgraded to include the guest/sandbox id once the env exists.
    tag = f"{task.category}/{task.id[:8]}"
    log.info(f"[{tag}] START {model_id} :: {task.instruction[:90]}")

    t0 = time.time()
    env: Env | None = None
    slot = None
    status = "error"
    error_str = ""
    score = 0
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

        if task.pre_command:
            env.run_pre_command(task)
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

        status = drive_agent(agent, task.instruction, MAX_STEPS, on_step=_on_step)

        if task.before_grading_delay:
            time.sleep(task.before_grading_delay)
        score, grade_log = env.grade(task)
        log.info(f"[{tag}] score: {score}")
    except Exception as e:
        log.error(f"[{tag}] ERROR {type(e).__name__}: {e}\n{traceback.format_exc()}")
        error_str = f"{type(e).__name__}: {e}"
    finally:
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
        task_id=task.id,
        category=task.category,
        model=model_id,
        score=score,
        max_score=100,
        n_steps=len(agent.steps) if agent else 0,
        status=status,
        duration_s=round(time.time() - t0, 2),
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=_cost_usd(model_id, in_tok, out_tok),
        sandbox_id=sandbox_id,
        error=error_str,
    )
    (task_dir / "result.json").write_text(json.dumps(asdict(result) | {"grade_log": grade_log}, indent=2))
    return result
