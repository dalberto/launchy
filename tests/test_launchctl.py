from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from launchy import launchctl
from launchy.exceptions import LaunchctlError, TeardownTimeoutError


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


# ---- bootout_and_wait (the slow-quit race fix) -----------------------------


def test_bootout_and_wait_skips_poll_when_pid_is_none() -> None:
    """No running child → bootout only, no os.kill polling."""
    with (
        patch("launchy.launchctl.subprocess.run", return_value=_completed(0)) as run,
        patch("launchy.launchctl.os.kill") as kill,
    ):
        launchctl.bootout_and_wait("gui/501/com.x", pid=None)
    assert run.call_args.args[0] == ["launchctl", "bootout", "gui/501/com.x"]
    kill.assert_not_called()


def test_bootout_and_wait_returns_when_child_already_dead() -> None:
    """Child gone on first poll → return immediately."""
    with (
        patch("launchy.launchctl.subprocess.run", return_value=_completed(0)),
        patch("launchy.launchctl.os.kill", side_effect=ProcessLookupError) as kill,
    ):
        launchctl.bootout_and_wait("gui/501/com.x", pid=999)
    assert kill.call_count == 1


def test_bootout_and_wait_polls_until_child_exits() -> None:
    """Three poll iterations: alive, alive, gone."""
    kill_calls = [None, None, ProcessLookupError()]
    with (
        patch("launchy.launchctl.subprocess.run", return_value=_completed(0)),
        patch("launchy.launchctl.os.kill", side_effect=kill_calls) as kill,
        patch("launchy.launchctl.time.sleep") as sleep,
    ):
        launchctl.bootout_and_wait("gui/501/com.x", pid=999)
    assert kill.call_count == 3
    assert sleep.call_count == 2  # slept between attempts 1→2 and 2→3


def test_bootout_and_wait_raises_teardown_timeout_when_child_outlives() -> None:
    """If the child never exits, raise TeardownTimeoutError after deadline."""
    # time.monotonic returns increasing values so we hit the deadline fast.
    times = iter([0.0, 0.05, 0.1, 0.2, 0.5, 1.5, 2.0])

    def fake_monotonic() -> float:
        return next(times)

    with (
        patch("launchy.launchctl.subprocess.run", return_value=_completed(0)),
        patch("launchy.launchctl.os.kill"),
        patch("launchy.launchctl.time.sleep"),
        patch("launchy.launchctl.time.monotonic", side_effect=fake_monotonic),
        pytest.raises(TeardownTimeoutError, match="PID 999"),
    ):
        launchctl.bootout_and_wait("gui/501/com.x", pid=999, timeout=1.0)


def test_bootout_and_wait_treats_permission_error_as_alive() -> None:
    """PID owned by another user → keep polling until it disappears."""
    # First two polls raise PermissionError (still alive), third raises
    # ProcessLookupError (gone). Should not raise.
    kill_calls = [PermissionError(), PermissionError(), ProcessLookupError()]
    with (
        patch("launchy.launchctl.subprocess.run", return_value=_completed(0)),
        patch("launchy.launchctl.os.kill", side_effect=kill_calls) as kill,
        patch("launchy.launchctl.time.sleep"),
    ):
        launchctl.bootout_and_wait("gui/501/com.x", pid=999)
    assert kill.call_count == 3
