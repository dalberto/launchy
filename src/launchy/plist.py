"""Render Job fields into a launchd plist.

Public surface: `CalendarSpec` and `KeepAliveConditions` TypedDicts (snake_case
at the user surface), plus `render()` and `loads()` round-trip helpers.

launchd plist keys are PascalCase; the user-facing TypedDicts are snake_case.
The mapping happens here so the rest of the codebase stays Pythonic.
"""

from __future__ import annotations

import plistlib
from datetime import timedelta
from pathlib import Path
from typing import Any, TypedDict, cast


class CalendarSpec(TypedDict, total=False):
    """One firing time for `StartCalendarInterval`. Omitted keys are wildcards.

    Example: `{"hour": 2, "minute": 0}` fires at 02:00 every day.
    """

    minute: int     # 0-59
    hour: int       # 0-23
    day: int        # 1-31 (day of month)
    weekday: int    # 0-7 (0 and 7 both Sunday)
    month: int      # 1-12


class KeepAliveConditions(TypedDict, total=False):
    """Conditional KeepAlive. Each key gates restart on a different signal.

    The ferry pattern (`{"successful_exit": False}`) means: restart on crash,
    but stay down after a clean SIGINT/SIGTERM (so `uninstall` actually stops
    the service).
    """

    successful_exit: bool
    network_state: bool
    crashed: bool
    path_state: dict[str, bool]
    other_job_enabled: dict[str, bool]


_CALENDAR_KEYS = {
    "minute": "Minute",
    "hour": "Hour",
    "day": "Day",
    "weekday": "Weekday",
    "month": "Month",
}

_KEEP_ALIVE_KEYS = {
    "successful_exit": "SuccessfulExit",
    "network_state": "NetworkState",
    "crashed": "Crashed",
    "path_state": "PathState",
    "other_job_enabled": "OtherJobEnabled",
}


def calendar_to_plist(spec: CalendarSpec) -> dict[str, int]:
    out: dict[str, int] = {}
    for raw_k, raw_v in spec.items():
        k = str(raw_k)
        pascal = _CALENDAR_KEYS.get(k)
        if pascal is None:
            raise ValueError(f"unknown CalendarSpec key: {k!r}")
        out[pascal] = int(raw_v)  # type: ignore[arg-type]
    return out


def keep_alive_to_plist(value: bool | KeepAliveConditions) -> bool | dict[str, Any]:
    if isinstance(value, bool):
        return value
    out: dict[str, Any] = {}
    for raw_k, raw_v in value.items():
        k = str(raw_k)
        pascal = _KEEP_ALIVE_KEYS.get(k)
        if pascal is None:
            raise ValueError(f"unknown KeepAliveConditions key: {k!r}")
        out[pascal] = raw_v
    return out


def calendar_from_plist(d: dict[str, Any]) -> CalendarSpec:
    inverse = {v: k for k, v in _CALENDAR_KEYS.items()}
    out: CalendarSpec = {}
    for k, v in d.items():
        snake = inverse.get(str(k))
        if snake is None:
            raise ValueError(f"unknown calendar plist key: {k!r}")
        out[snake] = int(v)  # type: ignore[literal-required]
    return out


def keep_alive_from_plist(value: Any) -> bool | KeepAliveConditions:
    if isinstance(value, bool):
        return value
    if not isinstance(value, dict):
        raise ValueError(f"KeepAlive must be bool or dict, got {type(value).__name__}")
    inverse = {v: k for k, v in _KEEP_ALIVE_KEYS.items()}
    out: KeepAliveConditions = {}
    items = cast("dict[str, Any]", value).items()
    for raw_k, raw_v in items:
        snake = inverse.get(str(raw_k))
        if snake is None:
            raise ValueError(f"unknown KeepAlive plist key: {raw_k!r}")
        out[snake] = raw_v  # type: ignore[literal-required]
    return out


def build_payload(
    *,
    label: str,
    program: list[str],
    run_at_load: bool = False,
    start_on_mount: bool = False,
    interval: timedelta | None = None,
    calendar: CalendarSpec | list[CalendarSpec] | None = None,
    watch_paths: list[Path] | None = None,
    queue_directories: list[Path] | None = None,
    keep_alive: bool | KeepAliveConditions | None = None,
    env: dict[str, str] | None = None,
    working_dir: Path | None = None,
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
) -> dict[str, Any]:
    """Assemble the plist dict from Job-shaped kwargs. No I/O."""
    payload: dict[str, Any] = {
        "Label": label,
        "ProgramArguments": list(program),
    }
    if run_at_load:
        payload["RunAtLoad"] = True
    if start_on_mount:
        payload["StartOnMount"] = True
    if interval is not None:
        seconds = int(interval.total_seconds())
        if seconds <= 0:
            raise ValueError("interval must be positive")
        payload["StartInterval"] = seconds
    if calendar is not None:
        if isinstance(calendar, list):
            payload["StartCalendarInterval"] = [calendar_to_plist(c) for c in calendar]
        else:
            payload["StartCalendarInterval"] = calendar_to_plist(calendar)
    if watch_paths:
        payload["WatchPaths"] = [str(p) for p in watch_paths]
    if queue_directories:
        payload["QueueDirectories"] = [str(p) for p in queue_directories]
    if keep_alive is not None:
        payload["KeepAlive"] = keep_alive_to_plist(keep_alive)
    if env:
        payload["EnvironmentVariables"] = dict(env)
    if working_dir is not None:
        payload["WorkingDirectory"] = str(working_dir)
    if stdout_path is not None:
        payload["StandardOutPath"] = str(stdout_path)
    if stderr_path is not None:
        payload["StandardErrorPath"] = str(stderr_path)
    return payload


def render(payload: dict[str, Any]) -> str:
    """Serialize a plist payload to UTF-8 XML."""
    return plistlib.dumps(payload, sort_keys=True).decode("utf-8")


def loads(data: bytes) -> dict[str, Any]:
    """Parse a plist into a dict. Auto-detects XML vs binary format.

    Pass raw bytes (not str) — binary plists aren't UTF-8 decodable.
    """
    parsed: Any = plistlib.loads(data)
    if not isinstance(parsed, dict):
        raise ValueError(f"plist root must be a dict, got {type(parsed).__name__}")
    return cast("dict[str, Any]", parsed)
