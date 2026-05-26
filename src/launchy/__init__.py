"""launchy: pythonic wrapper for macOS launchd."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from .diagnostics import Diagnostic, Severity
from .exceptions import (
    JobNotFound,
    LaunchctlError,
    LaunchyError,
    NotInstalled,
    PermissionDeniedError,
    TeardownTimeoutError,
)
from .job import Job
from .paths import Scope
from .plist import CalendarSpec, KeepAliveConditions
from .status import JobStatus

try:
    __version__ = version("launchy")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"

__all__ = [
    "CalendarSpec",
    "Diagnostic",
    "Job",
    "JobNotFound",
    "JobStatus",
    "KeepAliveConditions",
    "LaunchctlError",
    "LaunchyError",
    "NotInstalled",
    "PermissionDeniedError",
    "Scope",
    "Severity",
    "TeardownTimeoutError",
    "__version__",
]
