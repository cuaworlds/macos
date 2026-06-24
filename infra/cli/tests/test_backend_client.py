"""BackendClient HTTP behavior: auth header, error parsing, retries (MockTransport)."""

from __future__ import annotations

import httpx
import pytest

from benchmark import backend
from benchmark.backend import BackendClient, BackendError


def _client_with(handler) -> BackendClient:
    client = BackendClient("https://api/v1", "cua_test")
    client._http = httpx.Client(
        base_url="https://api/v1",
        headers={"Authorization": "Bearer cua_test"},
        transport=httpx.MockTransport(handler),
    )
    return client


def test_sends_bearer_and_parses_json():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"id": 7})

    assert _client_with(handler).whoami() == {"id": 7}
    assert seen["auth"] == "Bearer cua_test"


def test_4xx_raises_with_detail():
    def handler(req):
        return httpx.Response(404, json={"detail": "nope"})

    with pytest.raises(BackendError, match="nope"):
        _client_with(handler).whoami()


def test_retries_5xx_then_succeeds(monkeypatch):
    monkeypatch.setattr(backend, "BACKOFF_S", 0)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, json={"detail": "busy"})
        return httpx.Response(200, json={"ok": True})

    assert _client_with(handler)._request("GET", "/x") == {"ok": True}
    assert calls["n"] == 3


def test_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr(backend, "BACKOFF_S", 0)

    def handler(req):
        return httpx.Response(503, json={"detail": "busy"})

    with pytest.raises(BackendError, match="busy"):
        _client_with(handler)._request("GET", "/x")
