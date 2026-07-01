"""Worlds app provisioning: per-rollout hosted simulator instances.

An alternative to the local `mypc-apps` Docker sidecar. Each rollout creates
isolated, auto-login, persona-seeded instances on the MyPCBench orchestrator
(`mypc.worlds.vibrantlabs.com`), gets a unique URL per app, and deletes them on
teardown. Tasks keep their `localhost:<port>` text; the session rewrites those to
the live instance URLs at runtime, so task JSON is untouched. Graded over each
instance's REST API (see grade_worlds.py) since the orchestrator has no get_state.
"""

from __future__ import annotations

import os
import re

import httpx

from benchmark.log import get_logger

log = get_logger()

ORCHESTRATOR = os.getenv("WORLDS_ORCHESTRATOR", "http://mypc.worlds.vibrantlabs.com")
_TIMEOUT = httpx.Timeout(60.0, connect=15.0)

# docker apps-container host port -> orchestrator app key (only deployed apps)
PORT_TO_APP = {3001: "gringotts", 3011: "dinoco"}

_PORT_RE = re.compile(r"localhost:(\d{4})")


def create_instance(app: str, base: str = ORCHESTRATOR) -> tuple[str, str]:
    r = httpx.post(f"{base}/envs/{app}/create", json={}, timeout=_TIMEOUT)
    r.raise_for_status()
    d = r.json()
    return d["instance_id"], d["url"]


def delete_instance(instance_id: str, base: str = ORCHESTRATOR) -> None:
    try:
        httpx.delete(f"{base}/envs/{instance_id}", timeout=_TIMEOUT)
    except httpx.HTTPError as e:
        log.warning(f"[worlds] delete {instance_id} failed: {e}")


def ports_in(*texts: str) -> list[int]:
    found: set[int] = set()
    for t in texts:
        found.update(int(p) for p in _PORT_RE.findall(t or ""))
    return sorted(found)


class WorldsSession:
    """Live instances for the `localhost:<port>` apps a task references."""

    def __init__(self, ports: list[int], base: str = ORCHESTRATOR):
        unknown = [p for p in ports if p not in PORT_TO_APP]
        if unknown:
            raise ValueError(f"no worlds app for port(s) {unknown}; available: {sorted(PORT_TO_APP)}")
        self.base = base
        self._port_app = {p: PORT_TO_APP[p] for p in ports}
        self.ids: dict[str, str] = {}
        self.url_by_port: dict[int, str] = {}
        self.url_by_app: dict[str, str] = {}

    def open(self) -> "WorldsSession":
        for port, app in self._port_app.items():
            iid, url = create_instance(app, self.base)
            self.ids[app] = iid
            self.url_by_port[port] = url
            self.url_by_app[app] = url
            log.info(f"[worlds] {app} -> {url} ({iid})")
        return self

    def close(self) -> None:
        for iid in self.ids.values():
            delete_instance(iid, self.base)

    def render(self, text: str, autologin: bool = False) -> str:
        for port, url in self.url_by_port.items():
            # `?_autologin=1` pre-warms Michael's session; worlds instances aren't logged in by default.
            if autologin:
                text = text.replace(f"http://localhost:{port}/", f"{url}/?_autologin=1")
            text = text.replace(f"http://localhost:{port}", url).replace(f"localhost:{port}", url)
        return text
