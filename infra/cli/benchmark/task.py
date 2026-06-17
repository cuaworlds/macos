from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

TASKS_ROOT = Path(__file__).resolve().parent.parent / "tasks"
SMOKE_FILE = Path(__file__).resolve().parent.parent / "smoke_tasks.txt"


@dataclass
class Task:
    id: str
    category: str
    instruction: str
    pre_command: str
    before_action_delay: int
    before_grading_delay: int
    grading_command: list[tuple[str, int]]
    raw: dict
    # Directory the task JSON lives in — used to resolve a relative grading_script.
    task_dir: Path | None = None
    # Optional host-side verifier. Path is relative to the task's dir (then to
    # TASKS_ROOT); absent on every legacy task so they're unaffected.
    grading_script: str | None = None

    @classmethod
    def from_json(cls, path: Path) -> "Task":
        data = json.loads(path.read_text())
        pre = data.get("pre_command", "")
        if isinstance(pre, dict):
            pre = pre.get("en", "")
        return cls(
            id=data["id"],
            category=path.parent.name,
            instruction=data["task"]["en"],
            pre_command=(pre or "").strip(),
            before_action_delay=int(data.get("before_action_delay_seconds", 0)),
            before_grading_delay=int(data.get("before_grading_delay_seconds", 0)),
            grading_command=[(g[0], int(g[1])) for g in data.get("grading_command", [])],
            raw=data,
            task_dir=path.parent,
            grading_script=(data.get("grading_script") or None),
        )

    def resolve_grading_script(self) -> Path | None:
        """Absolute path to the grading_script, or None if unset.

        Resolved relative to the task's own dir first, then TASKS_ROOT.
        """
        if not self.grading_script:
            return None
        candidates = []
        if self.task_dir is not None:
            candidates.append(self.task_dir / self.grading_script)
        candidates.append(TASKS_ROOT / self.grading_script)
        for cand in candidates:
            if cand.exists():
                return cand.resolve()
        # Return the best-guess path even if missing so the runner can report it.
        return candidates[0].resolve()


def _index_all_tasks() -> dict[str, Path]:
    return {p.stem: p for p in TASKS_ROOT.glob("*/*.json")}


def load_tasks(ids: list[str] | None = None, smoke: bool = False) -> list[Task]:
    index = _index_all_tasks()
    if smoke:
        ids = [line.strip() for line in SMOKE_FILE.read_text().splitlines() if line.strip()]
    if ids:
        missing = [i for i in ids if i not in index]
        if missing:
            raise FileNotFoundError(f"Tasks not vendored: {missing}")
        paths = [index[i] for i in ids]
    else:
        paths = sorted(index.values())
    return [Task.from_json(p) for p in paths]
