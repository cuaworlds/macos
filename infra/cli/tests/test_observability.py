"""The root-cause regression: progress output must stream, not block-buffer.

The false 'deadlocks' came from `print()` to a redirected (non-TTY) stdout being
block-buffered, so a slow run produced no file output for minutes. We guard the two
defenses: setup_logging flips stdout to line-buffering, and the `mw` logger flushes
per record. Verified in a real child process whose stdout is a pipe (the exact
condition that triggered the bug), so it can't pass spuriously under pytest capture.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap


def _run(code: str) -> str:
    """Run `code` in a child whose stdout is a PIPE (non-TTY), return stdout."""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout


def test_setup_logging_line_buffers_piped_stdout():
    out = _run(
        """
        import sys
        from benchmark.log import setup_logging
        setup_logging()
        print("line_buffering=", sys.stdout.line_buffering)
        """
    )
    assert "line_buffering= True" in out


def test_logger_emits_immediately_and_is_flushed():
    # If the logger buffered, the child would still flush on exit — so we assert the
    # bytes are present AND that the handler stream is sys.stdout (StreamHandler
    # flushes per emit, the property that makes progress appear in real time).
    out = _run(
        """
        import sys
        from benchmark.log import setup_logging, get_logger
        setup_logging()
        lg = get_logger()
        h = lg.handlers[0]
        assert h.stream is sys.stdout, "logger must write to stdout"
        lg.info("[mw1 abcd1234] step 01: streamed")
        """
    )
    assert "[mw1 abcd1234] step 01: streamed" in out
