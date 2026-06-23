"""Credential resolution order: env > saved file > default."""

from __future__ import annotations

import json

from mw import auth


def test_env_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("CUA_API_KEY", "cua_env")
    monkeypatch.setenv("CUA_API_URL", "https://env/api")
    monkeypatch.setattr(auth, "CREDENTIALS_PATH", tmp_path / "c.json")
    url, key = auth.resolve()
    assert (url, key) == ("https://env/api", "cua_env")


def test_file_used_when_no_env(monkeypatch, tmp_path):
    monkeypatch.delenv("CUA_API_KEY", raising=False)
    monkeypatch.delenv("CUA_API_URL", raising=False)
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"api_url": "https://file/api", "api_key": "cua_file"}))
    monkeypatch.setattr(auth, "CREDENTIALS_PATH", path)
    url, key = auth.resolve()
    assert (url, key) == ("https://file/api", "cua_file")


def test_default_when_unauthenticated(monkeypatch, tmp_path):
    monkeypatch.delenv("CUA_API_KEY", raising=False)
    monkeypatch.delenv("CUA_API_URL", raising=False)
    monkeypatch.setattr(auth, "CREDENTIALS_PATH", tmp_path / "missing.json")
    url, key = auth.resolve()
    assert key is None
    assert url == auth.DEFAULT_CUA_API_URL


def test_save_and_clear_roundtrip(monkeypatch, tmp_path):
    path = tmp_path / "sub" / "c.json"
    monkeypatch.setattr(auth, "CREDENTIALS_PATH", path)
    auth.save_credentials("https://x/api", "cua_abc", {"username": "a"})
    assert json.loads(path.read_text())["api_key"] == "cua_abc"
    assert auth.clear_credentials() is True
    assert auth.clear_credentials() is False
