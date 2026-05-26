"""Real-launchd integration tests.

These tests actually invoke `launchctl` against the user's launchd domain
and write plists into `~/Library/LaunchAgents`. They're gated on macOS,
use a dedicated `dev.launchy.it-*` label namespace to avoid collisions
with anything real, and clean up after themselves on any exit.

Run only when iterating on launchd-touching code or before a release.
"""

from __future__ import annotations

import contextlib
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from launchy import Job, KeepAliveConditions

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="launchd is macOS-only")


SLOW_QUIT_SCRIPT = """#!/bin/bash
# Trap SIGTERM and drain for ~3s before exiting. This is the shape that
# triggers the bootout/bootstrap race in `Job.install()` on re-install.
trap 'echo "received SIGTERM, draining..."; sleep 3; echo "exiting"; exit 1' TERM
echo "started PID=$$"
while true; do sleep 1; done
"""


@pytest.fixture
def slow_quit_job(tmp_path: Path) -> Iterator[Job]:
    """A LaunchAgent whose program traps SIGTERM and takes ~3s to exit.

    Yields the constructed Job; guarantees teardown via `uninstall` on
    test exit even when the test itself fails.
    """
    script = tmp_path / "slow_quit.sh"
    script.write_text(SLOW_QUIT_SCRIPT)
    script.chmod(0o755)
    job = Job(
        label="dev.launchy.it-slow-quit",
        program=[str(script)],
        run_at_load=True,
        keep_alive=KeepAliveConditions(successful_exit=False),
        stdout_path=tmp_path / "out.log",
        stderr_path=tmp_path / "err.log",
    )
    try:
        yield job
    finally:
        with contextlib.suppress(Exception):
            job.uninstall()


def test_install_idempotent_against_slow_quit_child(slow_quit_job: Job) -> None:
    """The regression: re-installing while a slow-quit child is loaded
    must not race-fail with Bootstrap EIO 5."""
    slow_quit_job.install()
    time.sleep(0.3)  # let launchd settle so the second install sees it loaded
    slow_quit_job.install()  # must not raise


def test_reload_idempotent_against_slow_quit_child(slow_quit_job: Job) -> None:
    """Same race lives in reload(); same fix should cover it."""
    slow_quit_job.install()
    time.sleep(0.3)
    slow_quit_job.reload()  # must not raise
