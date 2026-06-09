from __future__ import annotations

import os
from dataclasses import dataclass

DISPLAY_WIDTH = 1024
DISPLAY_HEIGHT = 768

BASE_URL = os.getenv("USE_COMPUTER_BASE_URL", "https://api.dev.use.computer")

MAX_STEPS = int(os.getenv("MACOSWORLD_MAX_STEPS", "150"))
TASK_STEP_TIMEOUT_S = 120
ONLY_N_MOST_RECENT_IMAGES = 3
MAX_TOKENS = 4096


@dataclass
class ModelCfg:
    model_id: str
    tool_version: str
    beta_header: str
    thinking_budget_tokens: int | None
    input_per_mtok: float
    output_per_mtok: float


# Per Anthropic docs (May 2026):
#   computer-use-2025-11-24 + computer_20251124 → Opus 4.7, Opus 4.6, Sonnet 4.6, Opus 4.5
#   computer-use-2025-01-24 + computer_20250124 → Sonnet 4.5, Haiku 4.5, Opus 4.1, Sonnet 4, Opus 4
MODEL_CONFIG: dict[str, ModelCfg] = {
    "claude-opus-4-7": ModelCfg(
        model_id="claude-opus-4-7",
        tool_version="computer_20251124",
        beta_header="computer-use-2025-11-24",
        thinking_budget_tokens=4000,
        input_per_mtok=15.0,
        output_per_mtok=75.0,
    ),
    "claude-sonnet-4-6": ModelCfg(
        model_id="claude-sonnet-4-6",
        tool_version="computer_20251124",
        beta_header="computer-use-2025-11-24",
        thinking_budget_tokens=2000,
        input_per_mtok=3.0,
        output_per_mtok=15.0,
    ),
    "claude-haiku-4-5": ModelCfg(
        model_id="claude-haiku-4-5",
        tool_version="computer_20250124",
        beta_header="computer-use-2025-01-24",
        thinking_budget_tokens=None,
        input_per_mtok=1.0,
        output_per_mtok=5.0,
    ),
}

SYSTEM_PROMPT = """\
You are operating a macOS computer via a screenshot + click/type/scroll interface.

Notes:
* The display is 1024x768. Click coordinates are pixels on that display.
* Mac modifier keys: cmd (not ctrl) for copy/paste/save/quit. Use `key` with values like `cmd+s`, `cmd+space`, `return`, `escape`.
* When you think the task cannot be done, output exactly ```FAIL``` (with the triple backticks). Only as a last resort.
* When you have completed the task, output exactly ```DONE``` (with the triple backticks).
* After each action (except the last), take a screenshot. In the next turn, explicitly evaluate whether the previous step worked ("I have evaluated step X..."). Only move on once you confirm the previous step succeeded.
* Login is already done. No password is needed.
"""
