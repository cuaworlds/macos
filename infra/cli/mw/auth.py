"""Local credential storage and resolution for the hosted backend.

Resolution order (first hit wins): the ``CUA_API_KEY`` / ``CUA_API_URL`` env
vars, then ``~/.mw/credentials.json``, then the built-in default URL.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from benchmark.backend import BackendClient
from benchmark.config import DEFAULT_CUA_API_URL

CREDENTIALS_PATH = Path.home() / ".mw" / "credentials.json"


def load_file() -> dict:
    try:
        return json.loads(CREDENTIALS_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_credentials(api_url: str, api_key: str, user: dict | None = None) -> None:
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_PATH.write_text(
        json.dumps({"api_url": api_url, "api_key": api_key, "user": user or {}}, indent=2)
    )
    CREDENTIALS_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)


def clear_credentials() -> bool:
    try:
        CREDENTIALS_PATH.unlink()
        return True
    except FileNotFoundError:
        return False


def resolve() -> tuple[str, str | None]:
    """Return (api_url, api_key) — api_key is None when unauthenticated."""
    saved = load_file()
    api_url = os.getenv("CUA_API_URL") or saved.get("api_url") or DEFAULT_CUA_API_URL
    api_key = os.getenv("CUA_API_KEY") or saved.get("api_key")
    return api_url, api_key


def make_client(*, require: bool = True) -> BackendClient | None:
    """Build a client from resolved credentials, or None when unauthenticated.

    With ``require=True`` (the default), raises when no API key is available.
    """
    api_url, api_key = resolve()
    if not api_key:
        if require:
            raise PermissionError(
                "Not authenticated. Run `mw auth login` or set CUA_API_KEY."
            )
        return None
    return BackendClient(api_url, api_key)
