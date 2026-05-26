"""Exception hierarchy for launchy. Library code raises these; CLI catches them."""

from __future__ import annotations


class LaunchyError(Exception):
    """Base exception. Catch this to handle any launchy failure."""


class JobNotFound(LaunchyError):
    """`Job.load()` or similar lookup was given a label with no installed plist."""


class NotInstalled(LaunchyError):
    """Lifecycle operation invoked on a Job whose plist isn't on disk."""


class PermissionDeniedError(LaunchyError):
    """Daemon-scope operation invoked without root."""


class LaunchctlError(LaunchyError):
    """`launchctl` exited non-zero. Carries the argv, returncode, and stderr."""

    def __init__(self, argv: list[str], returncode: int, stderr: str) -> None:
        self.argv = argv
        self.returncode = returncode
        self.stderr = stderr
        joined = " ".join(argv)
        msg = f"launchctl failed (exit {returncode}): {joined}"
        if stderr.strip():
            msg = f"{msg}\n{stderr.strip()}"
        super().__init__(msg)


class TeardownTimeoutError(LaunchyError):
    """`bootout_and_wait` couldn't confirm the child PID exited in time.

    Distinct from `LaunchctlError`: launchctl itself succeeded, but the
    child process is still running past the deadline. Subsequent
    `bootstrap` would race with launchd's stale registration. Caller can
    retry (often the child just needs more time) or surface as a stuck
    teardown that needs human investigation.
    """
