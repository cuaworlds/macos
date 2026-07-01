"""Human-friendly default names for benchmark runs."""

from __future__ import annotations

import random
from datetime import UTC, datetime

_ADJECTIVES = (
    "brave", "calm", "clever", "bold", "swift", "bright", "quiet", "eager",
    "gentle", "lucky", "mellow", "nimble", "proud", "sharp", "warm", "zesty",
)
_NOUNS = (
    "otter", "falcon", "maple", "comet", "harbor", "lynx", "willow", "ember",
    "raven", "meadow", "pebble", "cedar", "badger", "lotus", "quartz", "sable",
)


def default_run_name(model: str) -> str:
    """`<model>-<adjective>-<noun>-<utc-timestamp>`, lowercased."""
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{model}-{random.choice(_ADJECTIVES)}-{random.choice(_NOUNS)}-{ts}".lower()
