from __future__ import annotations

import json
import time
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path

from typing import TYPE_CHECKING

from benchmark.agent import ClaudeAgent
from benchmark.config import MAX_STEPS, MODEL_CONFIG
from benchmark.env import Env, make_env
from benchmark.task import Task

if TYPE_CHECKING:
    from benchmark.env.kvm import KvmFleet


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

    print(f"\n=== {model_id} | {task.category}/{task.id} ===")
    print(f"    instruction: {task.instruction[:100]}")

    t0 = time.time()
    env: Env | None = None
    slot = None
    status = "error"
    error_str = ""
    score = 0
    grade_log: list[dict] = []
    agent: ClaudeAgent | None = None
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
        print(f"    sandbox: {sandbox_id} ({env._real_w}x{env._real_h} -> 1024x768, scale={env.scale_x:.2f}x{env.scale_y:.2f})")

        if task.pre_command:
            print(f"    pre_command: {task.pre_command[:80]}")
            env.run_pre_command(task)
        if task.before_action_delay:
            time.sleep(task.before_action_delay)

        agent = ClaudeAgent(model_id, env, task_dir)
        for step in range(1, MAX_STEPS + 1):
            rec = agent.step(step, MAX_STEPS, task.instruction)
            print(f"    step {step:02d}: {len(rec.actions)} actions, {rec.input_tokens}in/{rec.output_tokens}out, {rec.latency_s:.1f}s, status={rec.status}")
            if rec.status != "unfinished":
                status = rec.status
                break
        else:
            status = "max_steps"

        if task.before_grading_delay:
            time.sleep(task.before_grading_delay)
        score, grade_log = env.grade(task)
        print(f"    score: {score}")
    except Exception as e:
        traceback.print_exc()
        error_str = f"{type(e).__name__}: {e}"
    finally:
        if agent is not None:
            try:
                agent.save_logs()
            except Exception as e:
                print(f"    [warn] save_logs failed: {e}")
        if env is not None:
            try:
                env.close()
            except Exception as e:
                print(f"    [warn] env.close failed: {e}")
        elif slot is not None:
            # make_env failed after we acquired a slot — return it so the pool
            # doesn't leak a guest. (When env exists, env.close() releases it.)
            try:
                slot.release()
            except Exception as e:
                print(f"    [warn] slot.release failed: {e}")

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
