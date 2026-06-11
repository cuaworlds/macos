from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import anthropic
from anthropic.types.beta import (
    BetaContentBlockParam,
    BetaMessage,
    BetaTextBlock,
    BetaTextBlockParam,
    BetaToolUseBlockParam,
)

from benchmark.config import (
    MAX_TOKENS,
    MODEL_CALL_TIMEOUT_S,
    MODEL_CONFIG,
    ONLY_N_MOST_RECENT_IMAGES,
    SYSTEM_PROMPT,
)
from benchmark.env import MacOSWorldEnv, Screenshot
from benchmark.tools import tools_for


@dataclass
class StepRecord:
    step: int
    input_tokens: int
    output_tokens: int
    latency_s: float
    actions: list[dict] = field(default_factory=list)  # [{action, input, ok, msg, screenshot_path}]
    text_chunks: list[str] = field(default_factory=list)
    status: str = "unfinished"


class ClaudeAgent:
    """One-shot agent that drives a MacOSWorldEnv until DONE/FAIL or max_steps."""

    def __init__(self, model_id: str, env: MacOSWorldEnv, save_dir: Path):
        self.model_id = model_id
        self.cfg = MODEL_CONFIG[model_id]
        self.env = env
        self.save_dir = Path(save_dir)
        (self.save_dir / "context").mkdir(parents=True, exist_ok=True)

        self.client = anthropic.Anthropic(timeout=MODEL_CALL_TIMEOUT_S)
        self.tools = tools_for(model_id)
        self.messages: list[dict] = []
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.steps: list[StepRecord] = []

    # ---- message helpers ----

    def _push_user_task(self, instruction: str) -> None:
        self.messages.append({"role": "user", "content": instruction})

    def _filter_n_most_recent_images(self, n: int) -> None:
        """In-place: keep at most n base64 images across tool_result blocks. Older ones dropped."""
        for i in range(len(self.messages) - 1, -1, -1):
            msg = self.messages[i]
            if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
                continue
            for j in range(len(msg["content"]) - 1, -1, -1):
                blk = msg["content"][j]
                if blk.get("type") != "tool_result":
                    continue
                content = blk.get("content") or []
                if not isinstance(content, list):
                    continue
                for k in range(len(content) - 1, -1, -1):
                    if content[k].get("type") == "image":
                        if n > 0:
                            n -= 1
                        else:
                            del content[k]

    def _response_to_params(self, response: BetaMessage) -> list[BetaContentBlockParam]:
        out: list[BetaContentBlockParam] = []
        for block in response.content:
            if isinstance(block, BetaTextBlock):
                if block.text:
                    out.append(BetaTextBlockParam(type="text", text=block.text))
            elif getattr(block, "type", None) == "thinking":
                tb: dict = {"type": "thinking", "thinking": getattr(block, "thinking", "")}
                if getattr(block, "signature", None):
                    tb["signature"] = block.signature
                out.append(tb)  # type: ignore[arg-type]
            else:
                out.append(BetaToolUseBlockParam(**block.model_dump()))
        return out

    @staticmethod
    def _tool_result(tool_use_id: str, ok: bool, text: str, image_png: bytes | None) -> dict:
        content: list[dict] = [{"type": "text", "text": text}]
        if image_png is not None:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.standard_b64encode(image_png).decode(),
                    },
                }
            )
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": content,
                    "is_error": not ok,
                }
            ],
        }

    # ---- main loop hooks ----

    def _call_model(self) -> tuple[BetaMessage, float]:
        t0 = time.time()
        kwargs = dict(
            model=self.cfg.model_id,
            max_tokens=MAX_TOKENS,
            tools=self.tools,
            messages=self.messages,
            system=SYSTEM_PROMPT,
            betas=[self.cfg.beta_header],
        )
        if self.cfg.thinking_budget_tokens:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.cfg.thinking_budget_tokens,
            }
        response = self.client.beta.messages.create(**kwargs)
        return response, time.time() - t0

    def step(self, step_index: int, max_steps: int, instruction: str) -> StepRecord:
        # Seed instruction on first call.
        if step_index == 1:
            self._push_user_task(instruction)
            # Seed an initial screenshot so the model sees the starting state.
            shot = self.env.screenshot()
            self.messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": base64.standard_b64encode(shot.png).decode(),
                            },
                        }
                    ],
                }
            )
            shot.image.save(self.save_dir / "context" / "step_000.png")

        if ONLY_N_MOST_RECENT_IMAGES > 0:
            self._filter_n_most_recent_images(ONLY_N_MOST_RECENT_IMAGES)

        response, latency = self._call_model()
        in_tok = response.usage.input_tokens
        out_tok = response.usage.output_tokens
        self.total_input_tokens += in_tok
        self.total_output_tokens += out_tok

        # Persist assistant message verbatim so the next turn's context includes it.
        self.messages.append({"role": "assistant", "content": self._response_to_params(response)})

        rec = StepRecord(step=step_index, input_tokens=in_tok, output_tokens=out_tok, latency_s=latency)

        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                txt = block.text or ""
                rec.text_chunks.append(txt)
                if "```DONE```" in txt:
                    rec.status = "done"
                elif "```FAIL```" in txt:
                    rec.status = "fail"
            elif btype == "tool_use":
                tool_input = block.input or {}
                ok, msg = self.env.dispatch(tool_input)
                shot = self.env.screenshot()
                png_path = self.save_dir / "context" / f"step_{step_index:03d}.png"
                shot.image.save(png_path)
                rec.actions.append(
                    {
                        "action": tool_input.get("action"),
                        "input": tool_input,
                        "ok": ok,
                        "msg": msg,
                        "screenshot": str(png_path.name),
                    }
                )
                self.messages.append(self._tool_result(block.id, ok, msg, shot.png))

        if rec.status == "unfinished" and step_index >= max_steps:
            rec.status = "max_steps"

        self.steps.append(rec)
        return rec

    def save_logs(self) -> None:
        chat_path = self.save_dir / "context" / "chat_log.json"
        # Strip images before dumping to keep file small.
        self._filter_n_most_recent_images(0)
        chat_path.write_text(json.dumps(self.messages, indent=2, default=str))

        traj_path = self.save_dir / "trajectory.jsonl"
        with traj_path.open("w") as f:
            for s in self.steps:
                f.write(
                    json.dumps(
                        {
                            "step": s.step,
                            "input_tokens": s.input_tokens,
                            "output_tokens": s.output_tokens,
                            "latency_s": round(s.latency_s, 3),
                            "actions": s.actions,
                            "text": "".join(s.text_chunks)[:2000],
                            "status": s.status,
                        }
                    )
                    + "\n"
                )
