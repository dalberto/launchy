"""Thin subprocess wrappers around `launchctl` verbs.

These are pure I/O — they don't know about Job, plist rendering, or scopes
beyond the domain target string they're handed. Callers map errors to
domain-meaningful exceptions (PermissionDeniedError, NotInstalled, ...).
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from .exceptions import LaunchctlError, TeardownTimeoutError


def _run(argv: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise LaunchctlError(argv=argv, returncode=result.returncode, stderr=result.stderr)
    return result


def bootstrap(domain: str, plist: Path) -> None:
    """Load a plist into a launchd domain. Modern replacement for `launchctl load`."""
    _run(["launchctl", "bootstrap", domain, str(plist)], check=True)


def bootout(service_target: str) -> subprocess.CompletedProcess[str]:
    """Unload a service. Best-effort: returns the CompletedProcess so callers can ignore
    'service not loaded' errors without raising."""
    return _run(["launchctl", "bootout", service_target], check=False)


def kickstart(service_target: str, *, kill_existing: bool = True) -> None:
    """Force the service to run now. -k kills any existing instance first."""
    argv = ["launchctl", "kickstart"]
    if kill_existing:
        argv.append("-k")
    argv.append(service_target)
    _run(argv, check=True)


def kill(service_target: str, signal: str = "SIGTERM") -> None:
    """Send a signal to the running service."""
    _run(["launchctl", "kill", signal, service_target], check=True)


def print_service(service_target: str) -> subprocess.CompletedProcess[str]:
    """Query a service. Non-zero exit means the service isn't loaded."""
    return _run(["launchctl", "print", service_target], check=False)


def disable(service_target: str) -> None:
    """Mark a service as disabled in launchd's persistent store.

    Disabled services are skipped on future bootstraps and survive reboots.
    Pair with `bootout` to also stop the currently-running instance.
    """
    _run(["launchctl", "disable", service_target], check=True)


def enable(service_target: str) -> None:
    """Remove the disabled flag for a service. Doesn't bootstrap on its own."""
    _run(["launchctl", "enable", service_target], check=True)


def bootout_and_wait(
    service_target: str, pid: int | None, timeout: float = 30.0
) -> None:
    """Bootout, then block until the previously-loaded child PID is gone.

    `launchctl bootout` dispatches SIGTERM and returns immediately. The
    follow-up `bootstrap` will collide with launchd's still-live
    registration if the child takes time to exit (any service with a
    SIGTERM trap and slow drain). `launchctl print` lies — it reports the
    service as gone before launchd actually finishes the teardown. Polling
    the child PID directly via `os.kill(pid, 0)` is the only reliable
    teardown-complete signal.

    pid=None skips the poll (service was loaded but had no running child).
    Raises `TeardownTimeoutError` if the child outlives `timeout` seconds.
    """
    bootout(service_target)
    if pid is None:
        return
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return  # child is gone — teardown actually complete
        except PermissionError:
            pass  # owned by another user; treat as alive
        if time.monotonic() > deadline:
            raise TeardownTimeoutError(
                f"child PID {pid} did not exit within {timeout}s after bootout"
            )
        time.sleep(0.05)
