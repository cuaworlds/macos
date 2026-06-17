"""Yutori Navigator n1.5 agent — minimal fork of ClaudeAgent.

Drives an `Env` (use.computer managed sandbox or KVM/dockurr guest, both
implementing `benchmark/env/base.py:Env`) via Yutori's OpenAI-compatible
chat.completions API (https://api.yutori.com/v1). Uses the
`browser_tools_core-20260403` tool set with browser-only tools disabled
(no browser context in the macOS sandbox).

Protocol differences vs. Anthropic (per Yutori SDK example):

- The client (us) is responsible for injecting a fresh screenshot into the
  conversation before every predict. We append it as an `image_url` block to
  the LAST user/tool message in the history.
- Tool results are text-only `{role: "tool", tool_call_id, content: [text]}` —
  no image inside the result.
- Termination signal: assistant response with **no tool_calls** means n1.5 is
  done. We mark the run `done` and let the verifier judge.

Public surface matches ClaudeAgent — step(), total_input_tokens,
total_output_tokens, save_logs() — so runner.py can treat the two
interchangeably.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from openai import OpenAI

from benchmark.config import (
    MAX_TOKENS,
    MODEL_CALL_TIMEOUT_S,
    MODEL_CONFIG,
    ONLY_N_MOST_RECENT_IMAGES,
)
from benchmark.dispatch_yutori import dispatch_yutori
from benchmark.env.base import Env


YUTORI_BASE_URL = os.environ.get("YUTORI_BASE_URL", "https://api.yutori.com/v1")
TOOL_SET = "browser_tools_core-20260403"
# Browser-only tools — irrelevant in the macOS sandbox. Disable so the model
# doesn't waste turns trying to navigate URLs.
DISABLE_TOOLS = ["goto_url", "refresh", "go_back", "go_forward"]

# Yutori's docs are explicit that a custom *system* prompt DEGRADES n1.5; extra
# instructions belong in the FIRST USER MESSAGE. n1.5 is also browser-trained, so
# it tends to look for a URL bar / DOM / xdotool. This short preamble (appended to
# the task instruction on step 1) re-grounds it in a macOS desktop without fighting
# the model's training. Keep it terse — long instructions also hurt n1.5.
DESKTOP_PREAMBLE = (
    "Note: you are controlling a full macOS desktop, not a browser. There is no "
    "URL bar and no DOM; navigation tools are unavailable. Use macOS conventions: "
    "cmd-based keyboard shortcuts (cmd+s to save, cmd+w to close), the menu bar at "
    "the top of the screen, and Spotlight (cmd+space) to open applications. Press "
    "Escape or cmd+w if a dialog blocks you. When the task is complete, reply with "
    "plain text and no action."
)


def _first_user_text(instruction: str) -> str:
    """Compose the step-1 user message text: task instruction + desktop preamble.

    Pure helper so the composition is testable without a network/client.
    """
    return f"{instruction}\n\n{DESKTOP_PREAMBLE}"


@dataclass
class StepRecord:
    step: int
    input_tokens: int
    output_tokens: int
    latency_s: float
    actions: list[dict] = field(default_factory=list)
    text_chunks: list[str] = field(default_factory=list)
    status: str = "unfinished"


def _png_to_data_url(png: bytes) -> str:
    b64 = base64.standard_b64encode(png).decode()
    return f"data:image/png;base64,{b64}"


class NavigatorAgent:
    """One-shot Yutori Navigator n1.5 agent driving an Env."""

    def __init__(self, model_id: str, env: Env, save_dir: Path):
        self.model_id = model_id
        self.cfg = MODEL_CONFIG[model_id]
        self.env = env
        self.save_dir = Path(save_dir)
        (self.save_dir / "context").mkdir(parents=True, exist_ok=True)

        api_key = os.environ.get("YUTORI_API_KEY")
        if not api_key:
            raise RuntimeError("YUTORI_API_KEY env var not set")
        self.client = OpenAI(
            base_url=YUTORI_BASE_URL, api_key=api_key, timeout=MODEL_CALL_TIMEOUT_S
        )

        # No system prompt: Yutori docs say a custom system prompt degrades n1.5.
        # The instruction + DESKTOP_PREAMBLE is seeded into the first user message
        # in step() instead (see _first_user_text).
        self.messages: list[dict] = []
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.steps: list[StepRecord] = []

    # ---- message helpers ----

    def _strip_old_screenshots(self, keep: int) -> None:
        """Walk back from end, keep the most recent `keep` image_url blocks; drop the rest."""
        seen = 0
        for i in range(len(self.messages) - 1, -1, -1):
            msg = self.messages[i]
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for j in range(len(content) - 1, -1, -1):
                blk = content[j]
                if isinstance(blk, dict) and blk.get("type") == "image_url":
                    if seen < keep:
                        seen += 1
                    else:
                        del content[j]

    def _append_screenshot_to_last(self, png: bytes) -> None:
        """Append a fresh screenshot to the last user/tool message's content list.

        n1.5 expects the screenshot to live on a user/tool message; assistant
        messages can't carry image content. If the last message is assistant
        (e.g. it emitted text but no tool_calls), we insert a placeholder user
        message to carry the image.
        """
        if not self.messages or self.messages[-1].get("role") == "assistant":
            self.messages.append({"role": "user", "content": []})
        last = self.messages[-1]
        if not isinstance(last.get("content"), list):
            last["content"] = [{"type": "text", "text": last.get("content", "")}]
        last["content"].append({"type": "text", "text": "\n\n"})
        last["content"].append(
            {"type": "image_url", "image_url": {"url": _png_to_data_url(png), "detail": "high"}}
        )

    def _call_model(self) -> tuple[object, float]:
        t0 = time.time()
        response = self.client.chat.completions.create(
            model=self.cfg.model_id,
            messages=self.messages,
            max_completion_tokens=MAX_TOKENS,
            timeout=MODEL_CALL_TIMEOUT_S,
            extra_body={"tool_set": TOOL_SET, "disable_tools": DISABLE_TOOLS},
        )
        return response, time.time() - t0

    def step(self, step_index: int, max_steps: int, instruction: str) -> StepRecord:
        # On the first step, seed the conversation with the instruction + desktop
        # preamble. The screenshot is appended right after, like every later turn.
        if step_index == 1:
            self.messages.append(
                {"role": "user", "content": [{"type": "text", "text": _first_user_text(instruction)}]}
            )

        # Inject a fresh screenshot into the last user/tool message before predict.
        shot = self.env.screenshot()
        if step_index == 1:
            shot.image.save(self.save_dir / "context" / "step_000.png")
        self._append_screenshot_to_last(shot.png)

        if ONLY_N_MOST_RECENT_IMAGES > 0:
            self._strip_old_screenshots(ONLY_N_MOST_RECENT_IMAGES)

        response, latency = self._call_model()
        if not response.choices:
            detail = getattr(response, "detail", None) or response.model_dump().get("detail")
            raise RuntimeError(f"Yutori API error: {detail}")

        usage = response.usage
        in_tok = getattr(usage, "prompt_tokens", 0) or 0
        out_tok = getattr(usage, "completion_tokens", 0) or 0
        self.total_input_tokens += in_tok
        self.total_output_tokens += out_tok

        choice = response.choices[0]
        msg = choice.message
        text = msg.content or ""

        assistant_entry: dict = {"role": "assistant", "content": text or ""}
        if msg.tool_calls:
            assistant_entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        self.messages.append(assistant_entry)

        rec = StepRecord(step=step_index, input_tokens=in_tok, output_tokens=out_tok, latency_s=latency)
        if text:
            rec.text_chunks.append(text)

        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
                ok, dmsg = dispatch_yutori(self.env, tc.function.name, args)
            except json.JSONDecodeError as e:
                args = {}
                ok, dmsg = False, f"bad tool arguments: {e}"

            post_shot = self.env.screenshot()
            png_path = self.save_dir / "context" / f"step_{step_index:03d}.png"
            post_shot.image.save(png_path)
            rec.actions.append(
                {
                    "action": tc.function.name,
                    "input": args,
                    "ok": ok,
                    "msg": dmsg,
                    "screenshot": str(png_path.name),
                }
            )
            # Tool result: text-only per Yutori protocol; screenshot is appended
            # to this message on the NEXT predict via _append_screenshot_to_last.
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": [{"type": "text", "text": dmsg}],
                }
            )

        # n1.5 signals completion by emitting no tool_calls. Let the grader judge.
        if not msg.tool_calls:
            rec.status = "done"

        if rec.status == "unfinished" and step_index >= max_steps:
            rec.status = "max_steps"

        self.steps.append(rec)
        return rec

    def save_logs(self) -> None:
        chat_path = self.save_dir / "context" / "chat_log.json"
        # Strip all images before dumping so the file stays small.
        self._strip_old_screenshots(0)
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
