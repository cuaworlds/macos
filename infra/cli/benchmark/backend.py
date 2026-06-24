"""HTTP client for the hosted CuaWorld backend.

Thin wrapper over the REST API (Bearer auth, retries on transient failures).
Domain mapping (TaskResult -> rollout payloads) lives in mw.push; this module
only speaks HTTP.
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx

RETRY_STATUSES = {500, 502, 503, 504}
MAX_ATTEMPTS = 3
BACKOFF_S = 0.5


class BackendError(Exception):
    """A backend request failed (4xx, or transient error after retries)."""


def login(api_url: str, username: str, password: str, *, timeout: float = 30.0) -> dict:
    """Exchange username/password for a token pair. No auth header needed."""
    try:
        resp = httpx.post(
            f"{api_url}/auth/login",
            json={"username": username, "password": password},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise BackendError(f"login failed: {exc}") from exc
    if resp.status_code >= 400:
        raise BackendError(_detail(resp, "login"))
    return resp.json()


class BackendClient:
    def __init__(self, api_url: str, token: str, *, timeout: float = 30.0):
        self.api_url = api_url.rstrip("/")
        self._http = httpx.Client(
            base_url=self.api_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> BackendClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- core --------------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> dict:
        last_exc: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = self._http.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                last_exc = exc
            else:
                if resp.status_code in RETRY_STATUSES and attempt < MAX_ATTEMPTS:
                    time.sleep(BACKOFF_S * attempt)
                    continue
                if resp.status_code >= 400:
                    raise BackendError(_detail(resp, f"{method} {path}"))
                return resp.json() if resp.content else {}
            time.sleep(BACKOFF_S * attempt)
        raise BackendError(f"{method} {path} failed after {MAX_ATTEMPTS} attempts: {last_exc}")

    # -- auth --------------------------------------------------------------

    def whoami(self) -> dict:
        return self._request("GET", "/auth/me")

    def mint_key(self) -> str:
        return self._request("POST", "/auth/key")["api_key"]

    # -- tasks -------------------------------------------------------------

    def create_task(self, payload: dict) -> dict:
        return self._request("POST", "/tasks", json=payload)

    # -- runs --------------------------------------------------------------

    def create_run(self, payload: dict) -> dict:
        return self._request("POST", "/runs", json=payload)

    def patch_run(self, run_id: int, payload: dict) -> dict:
        return self._request("PATCH", f"/runs/{run_id}", json=payload)

    # -- rollouts ----------------------------------------------------------

    def create_rollout(self, payload: dict) -> dict:
        return self._request("POST", "/rollouts", json=payload)

    def patch_rollout(self, rollout_id: int, payload: dict) -> dict:
        return self._request("PATCH", f"/rollouts/{rollout_id}", json=payload)

    # -- artifacts ---------------------------------------------------------

    def presign_artifact(self, rollout_id: int, filename: str, content_type: str) -> dict:
        return self._request(
            "POST",
            f"/rollouts/{rollout_id}/artifacts/presigned",
            json={"filename": filename, "content_type": content_type},
        )

    def upload_artifact(self, upload_url: str, path: Path, content_type: str) -> None:
        """PUT a file to a presigned S3 URL (no auth header; Content-Type must match)."""
        try:
            resp = httpx.put(
                upload_url,
                content=path.read_bytes(),
                headers={"Content-Type": content_type},
                timeout=self._http.timeout,
            )
        except httpx.HTTPError as exc:
            raise BackendError(f"artifact upload failed: {exc}") from exc
        if resp.status_code >= 400:
            raise BackendError(f"artifact upload failed: {resp.status_code} {resp.text[:200]}")


def _detail(resp: httpx.Response, ctx: str) -> str:
    try:
        detail = resp.json().get("detail")
    except Exception:
        detail = None
    return f"{ctx}: {resp.status_code} {detail or resp.text[:200]}"
