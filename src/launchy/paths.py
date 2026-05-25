"""Scope enum and path/domain resolution.

A Scope picks the plist directory on disk and the `launchctl` domain target
string. Keep these in one place so `Job`, `launchctl`, and the CLI all agree.
"""

from __future__ import annotations

import enum
import os
from pathlib import Path


class Scope(enum.Enum):
    """Where the plist lives and which launchd domain manages it."""

    USER = "user"
    ALL_USERS = "all-users"
    SYSTEM = "system"


_USER_DIR = Path.home() / "Library" / "LaunchAgents"
_ALL_USERS_DIR = Path("/Library/LaunchAgents")
_SYSTEM_DIR = Path("/Library/LaunchDaemons")


def plist_dir(scope: Scope) -> Path:
    match scope:
        case Scope.USER:
            return _USER_DIR
        case Scope.ALL_USERS:
            return _ALL_USERS_DIR
        case Scope.SYSTEM:
            return _SYSTEM_DIR


def plist_path(scope: Scope, label: str) -> Path:
    return plist_dir(scope) / f"{label}.plist"


def domain_target(scope: Scope) -> str:
    """The `launchctl` domain string for this scope.

    `gui/<uid>` for user-scoped agents (loaded into the current GUI session),
    `system` for daemons. ALL_USERS plists are loaded per-user, so they share
    the GUI domain at runtime even though they live in /Library.
    """
    match scope:
        case Scope.USER | Scope.ALL_USERS:
            return f"gui/{os.getuid()}"
        case Scope.SYSTEM:
            return "system"


def service_target(scope: Scope, label: str) -> str:
    return f"{domain_target(scope)}/{label}"


def requires_root(scope: Scope) -> bool:
    return scope in (Scope.ALL_USERS, Scope.SYSTEM)
