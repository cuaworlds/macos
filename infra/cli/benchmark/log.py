"""Logging setup for the benchmark harness.

Two problems this solves, both of which made debugging concurrent KVM runs hard:

1. **Block-buffered stdout.** Plain `print()` to a redirected (non-TTY) stdout is
   block-buffered (~8 KB), so a slow run produces no file output for minutes and
   *looks* deadlocked. `logging.StreamHandler` flushes after every record, and we
   also flip stdout to line-buffering, so progress streams in real time.
2. **Unattributable concurrent output.** Under an N-way fleet, bare `step 12:`
   lines from N tasks interleave with no way to tell them apart. Every line now
   carries a timestamp, and per-task lines carry a `[<guest> <task>]` tag.

Use `setup_logging()` once at process entry, then `get_logger()` everywhere.
"""

from __future__ import annotations

import logging
import sys

_LOGGER_NAME = "mw"


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure the `mw` logger to stream timestamped lines to stdout.

    Idempotent: safe to call more than once (e.g. from nested Click groups).
    """
    # Flip stdout to line-buffering so even stray `print()`s stream when stdout is
    # a pipe/file rather than a TTY. Guard: not all streams support reconfigure.
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)  # StreamHandler flushes per-record
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(level)
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)
