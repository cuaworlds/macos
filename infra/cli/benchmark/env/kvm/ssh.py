"""SSH exec into a KVM guest, over the host-mapped SSH port.

Connects straight to <host>:<ssh_port> (Tailscale-routed in our setup), using the
id_kvm key baked into the base volume. Used for run_pre_command + grade — the same
role exec_ssh plays for the managed backend.
"""
from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass


@dataclass
class SshResult:
    rc: int
    stdout: str
    stderr: str


class GuestSsh:
    def __init__(self, host: str, port: int, user: str, key_path: str):
        self.host = host
        self.port = port
        self.user = user
        self.key_path = key_path

    def _base_argv(self, *, connect_timeout: int) -> list[str]:
        return [
            "ssh",
            "-i", self.key_path,
            "-p", str(self.port),
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "BatchMode=yes",
            "-o", "PreferredAuthentications=publickey",
            "-o", f"ConnectTimeout={connect_timeout}",
            f"{self.user}@{self.host}",
        ]

    def exec(self, command: str, *, timeout: int = 60) -> SshResult:
        """Run a command in the guest and capture output. Synchronous."""
        argv = self._base_argv(connect_timeout=10) + [command]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            return SshResult(rc=124, stdout=e.stdout or "", stderr=f"ssh timeout after {timeout}s")
        return SshResult(rc=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    def exec_detached(self, command: str, *, timeout: int = 60) -> SshResult:
        """Run a setup command whose backgrounded children must outlive the ssh session.

        Tasks like the safety dialog spawn `( sleep 6 && osascript ... ) &`. Two problems
        with a plain ssh exec: ssh hangs until the backgrounded child closes the pipe, and
        the child can be killed on disconnect. Redirecting all stdio detaches backgrounded
        jobs from the ssh pipe (so ssh returns as soon as the synchronous part finishes)
        and, with no controlling tty, they aren't HUP'd. macOS has no `setsid`, so this
        redirection trick is what we use instead.
        """
        wrapped = f"bash -lc {shlex.quote(command)} </dev/null >/dev/null 2>&1"
        return self.exec(wrapped, timeout=timeout)

    def wait_until_ready(self, *, timeout_s: int, poll_s: float = 5.0) -> bool:
        """Poll until the guest answers SSH (key auth) or timeout elapses."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            argv = self._base_argv(connect_timeout=3) + ["true"]
            try:
                proc = subprocess.run(argv, capture_output=True, text=True, timeout=10)
                if proc.returncode == 0:
                    return True
            except subprocess.TimeoutExpired:
                pass
            time.sleep(poll_s)
        return False
