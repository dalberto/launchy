from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from launchy import launchctl
from launchy.exceptions import LaunchctlError


def _completed(
    returncode: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_bootstrap_invokes_correct_argv() -> None:
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(0)) as run:
        launchctl.bootstrap("gui/501", Path("/tmp/x.plist"))
    run.assert_called_once()
    argv = run.call_args.args[0]
    assert argv == ["launchctl", "bootstrap", "gui/501", "/tmp/x.plist"]
    assert run.call_args.kwargs["capture_output"] is True


def test_bootstrap_raises_launchctl_error_on_failure() -> None:
    with (
        patch(
            "launchy.launchctl.subprocess.run",
            return_value=_completed(78, stderr="Bootstrap failed"),
        ),
        pytest.raises(LaunchctlError) as exc_info,
    ):
        launchctl.bootstrap("gui/501", Path("/tmp/x.plist"))
    assert exc_info.value.returncode == 78
    assert "Bootstrap failed" in str(exc_info.value)
    assert exc_info.value.argv[:2] == ["launchctl", "bootstrap"]


def test_bootout_does_not_raise_on_failure() -> None:
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(1, stderr="not loaded")):
        result = launchctl.bootout("gui/501/com.x")
    assert result.returncode == 1


def test_kickstart_appends_kill_flag_by_default() -> None:
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(0)) as run:
        launchctl.kickstart("gui/501/com.x")
    assert run.call_args.args[0] == ["launchctl", "kickstart", "-k", "gui/501/com.x"]


def test_kickstart_without_kill_existing() -> None:
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(0)) as run:
        launchctl.kickstart("gui/501/com.x", kill_existing=False)
    assert run.call_args.args[0] == ["launchctl", "kickstart", "gui/501/com.x"]


def test_kill_passes_signal_name() -> None:
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(0)) as run:
        launchctl.kill("gui/501/com.x", "SIGTERM")
    assert run.call_args.args[0] == ["launchctl", "kill", "SIGTERM", "gui/501/com.x"]


def test_print_service_returns_completed_process() -> None:
    with patch(
        "launchy.launchctl.subprocess.run",
        return_value=_completed(0, stdout="state = running\n"),
    ):
        result = launchctl.print_service("gui/501/com.x")
    assert result.returncode == 0
    assert "running" in result.stdout
