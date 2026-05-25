"""Parse `launchctl print <target>` output into a JobStatus dataclass.

launchctl's print verb emits a human-readable stanza, not JSON. We pluck pid
and last exit code with regex — same approach as mcp-ferry's launchd.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class JobStatus:
    label: str
    loaded: bool
    pid: int | None
    last_exit_code: int | None


_PID_RE = re.compile(r"^\s*pid\s*=\s*(\d+)\s*$", re.MULTILINE)
_EXIT_RE = re.compile(r"^\s*last exit code\s*=\s*(-?\d+)\s*$", re.MULTILINE)


def parse(label: str, returncode: int, output: str) -> JobStatus:
    """Build a JobStatus from `launchctl print`'s exit code and stdout."""
    if returncode != 0:
        return JobStatus(label=label, loaded=False, pid=None, last_exit_code=None)
    pid_match = _PID_RE.search(output)
    exit_match = _EXIT_RE.search(output)
    return JobStatus(
        label=label,
        loaded=True,
        pid=int(pid_match.group(1)) if pid_match else None,
        last_exit_code=int(exit_match.group(1)) if exit_match else None,
    )
