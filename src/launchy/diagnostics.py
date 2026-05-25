"""Health checks for Jobs — the silent-failure killer.

`diagnose(job, status)` runs a battery of checks and returns a list of
`Diagnostic` entries. Callers decide how to render them. CLI uses this for
`launchy doctor`; library users can call `Job.diagnose()` for the same data.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .job import Job
    from .status import JobStatus

Severity = Literal["ok", "warn", "fail"]


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One check result. `severity` is the load-bearing field; `check` and
    `detail` are for display/logging."""

    severity: Severity
    check: str
    detail: str


_LABEL_RE = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$")


def _check_program(job: Job) -> Diagnostic:
    p = Path(job.program[0])
    if not p.is_absolute():
        return Diagnostic("fail", "program", f"path is relative: {p} (launchd needs absolute)")
    if not p.exists():
        return Diagnostic("fail", "program", f"missing: {p}")
    if not p.is_file():
        return Diagnostic("fail", "program", f"not a file: {p}")
    if not os.access(p, os.X_OK):
        return Diagnostic("fail", "program", f"not executable: {p}")
    return Diagnostic("ok", "program", str(p))


def _check_working_dir(job: Job) -> Diagnostic | None:
    if job.working_dir is None:
        return None
    if not job.working_dir.exists():
        return Diagnostic("fail", "working_dir", f"missing: {job.working_dir}")
    if not job.working_dir.is_dir():
        return Diagnostic("fail", "working_dir", f"not a directory: {job.working_dir}")
    return Diagnostic("ok", "working_dir", str(job.working_dir))


def _check_log_paths(job: Job) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for kind, p in (("stdout", job.resolved_stdout_path), ("stderr", job.resolved_stderr_path)):
        if p is None:
            continue
        parent = p.parent
        if not parent.exists():
            out.append(Diagnostic("fail", f"log_{kind}", f"parent dir missing: {parent}"))
        elif not os.access(parent, os.W_OK):
            out.append(Diagnostic("fail", f"log_{kind}", f"parent dir not writable: {parent}"))
        else:
            out.append(Diagnostic("ok", f"log_{kind}", str(p)))
    return out


def _check_label_format(job: Job) -> Diagnostic:
    if not _LABEL_RE.match(job.label):
        return Diagnostic(
            "warn", "label", f"non-standard chars: {job.label!r} (expected reverse-DNS)"
        )
    return Diagnostic("ok", "label", job.label)


def _check_schedule_sanity(job: Job) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    if job.interval is not None and job.interval.total_seconds() < 10:
        out.append(
            Diagnostic(
                "warn",
                "schedule",
                f"interval < 10s ({int(job.interval.total_seconds())}s) — CPU thrash risk",
            )
        )
    if isinstance(job.calendar, dict) and not job.calendar:
        out.append(Diagnostic("warn", "schedule", "empty calendar (fires every minute)"))
    has_trigger = bool(
        job.run_at_load
        or job.start_on_mount
        or job.interval
        or job.calendar
        or job.watch_paths
        or job.queue_directories
        or job.keep_alive
    )
    if not has_trigger:
        out.append(Diagnostic("warn", "schedule", "no trigger set — this job will never run"))
    return out


def _check_env_sanity(job: Job) -> Diagnostic | None:
    p = job.program[0]
    if p.endswith((".sh", ".py", ".rb", ".pl")) and "PATH" not in job.env:
        return Diagnostic(
            "warn",
            "env",
            f"{Path(p).suffix} script with no PATH in env — subprocess calls may fail",
        )
    return None


def _check_trigger_conflicts(job: Job) -> Diagnostic | None:
    schedule_triggers = sum(
        1 for present in (job.interval is not None, job.calendar is not None) if present
    )
    if schedule_triggers > 1:
        return Diagnostic(
            "warn", "triggers", "both interval and calendar set — fires from each independently"
        )
    return None


def _check_state(status: JobStatus) -> Diagnostic | None:
    if not status.loaded:
        return Diagnostic(
            "warn", "state", "plist on disk but not loaded — disabled, or bootstrap failed"
        )
    if status.last_exit_code is not None and status.last_exit_code != 0:
        return Diagnostic("warn", "state", f"last exit code = {status.last_exit_code}")
    return None


def diagnose(job: Job, status: JobStatus | None = None) -> list[Diagnostic]:
    """Run all health checks. Pass `status` to include the loaded-state check.

    Returns one Diagnostic per check (ok/warn/fail). Caller filters by
    severity for display. Used by `Job.diagnose()` and `launchy doctor`.
    """
    diagnostics: list[Diagnostic] = [
        _check_program(job),
        _check_label_format(job),
    ]
    if (wd := _check_working_dir(job)) is not None:
        diagnostics.append(wd)
    diagnostics.extend(_check_log_paths(job))
    diagnostics.extend(_check_schedule_sanity(job))
    if (env_diag := _check_env_sanity(job)) is not None:
        diagnostics.append(env_diag)
    if (conflict := _check_trigger_conflicts(job)) is not None:
        diagnostics.append(conflict)
    if status is not None and (state := _check_state(status)) is not None:
        diagnostics.append(state)
    return diagnostics
