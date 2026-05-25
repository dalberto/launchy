from __future__ import annotations

import os
from pathlib import Path

from launchy.paths import (
    Scope,
    domain_target,
    plist_dir,
    plist_path,
    requires_root,
    service_target,
)


def test_user_scope_uses_home_launchagents() -> None:
    assert plist_dir(Scope.USER) == Path.home() / "Library" / "LaunchAgents"


def test_all_users_scope_uses_root_launchagents() -> None:
    assert plist_dir(Scope.ALL_USERS) == Path("/Library/LaunchAgents")


def test_system_scope_uses_launchdaemons() -> None:
    assert plist_dir(Scope.SYSTEM) == Path("/Library/LaunchDaemons")


def test_plist_path_appends_label() -> None:
    assert plist_path(Scope.USER, "com.launchy.x") == (
        Path.home() / "Library" / "LaunchAgents" / "com.launchy.x.plist"
    )


def test_domain_target_user_uses_gui_uid() -> None:
    assert domain_target(Scope.USER) == f"gui/{os.getuid()}"


def test_domain_target_all_users_uses_gui_uid() -> None:
    assert domain_target(Scope.ALL_USERS) == f"gui/{os.getuid()}"


def test_domain_target_system_uses_system() -> None:
    assert domain_target(Scope.SYSTEM) == "system"


def test_service_target_concatenates_domain_and_label() -> None:
    assert service_target(Scope.USER, "com.launchy.x") == f"gui/{os.getuid()}/com.launchy.x"
    assert service_target(Scope.SYSTEM, "com.launchy.x") == "system/com.launchy.x"


def test_requires_root_matches_privileged_scopes() -> None:
    assert requires_root(Scope.USER) is False
    assert requires_root(Scope.ALL_USERS) is True
    assert requires_root(Scope.SYSTEM) is True
