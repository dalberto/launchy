from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from launchy.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _completed(returncode: int, stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _install_mocks() -> list[subprocess.CompletedProcess[str]]:
    """Mock sequence for an install: idempotent-check, bootstrap, post-install status."""
    return [_completed(1), _completed(0), _completed(1)]


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("HOME", str(tmp_path))
    from launchy import paths

    monkeypatch.setattr(paths, "_USER_DIR", tmp_path / "Library" / "LaunchAgents")
    yield tmp_path


def test_install_writes_plist_and_bootstraps(runner: CliRunner, fake_home: Path) -> None:
    with patch("launchy.launchctl.subprocess.run", side_effect=_install_mocks()):
        result = runner.invoke(
            app,
            [
                "install",
                "com.launchy.cli",
                "--program",
                "/bin/echo",
                "--program",
                "hi",
                "--run-at-load",
            ],
        )
    assert result.exit_code == 0, result.output
    assert "installed" in result.output
    expected = fake_home / "Library" / "LaunchAgents" / "com.launchy.cli.plist"
    assert expected.exists()


def test_install_with_calendar_flag(runner: CliRunner, fake_home: Path) -> None:
    with patch("launchy.launchctl.subprocess.run", side_effect=_install_mocks()):
        result = runner.invoke(
            app,
            [
                "install",
                "com.launchy.cal",
                "--program",
                "/bin/true",
                "--calendar",
                "hour=2,minute=0",
            ],
        )
    assert result.exit_code == 0, result.output


def test_install_with_interval_flag(runner: CliRunner, fake_home: Path) -> None:
    with patch("launchy.launchctl.subprocess.run", side_effect=_install_mocks()):
        result = runner.invoke(
            app,
            ["install", "com.launchy.iv", "--program", "/bin/true", "--interval", "300"],
        )
    assert result.exit_code == 0, result.output


def test_install_with_env_flag(runner: CliRunner, fake_home: Path) -> None:
    with patch("launchy.launchctl.subprocess.run", side_effect=_install_mocks()):
        result = runner.invoke(
            app,
            [
                "install",
                "com.launchy.env",
                "--program",
                "/bin/true",
                "--env",
                "FOO=bar",
                "--env",
                "BAZ=qux",
            ],
        )
    assert result.exit_code == 0, result.output


def test_install_rejects_bad_calendar(runner: CliRunner, fake_home: Path) -> None:
    result = runner.invoke(
        app,
        [
            "install",
            "com.launchy.bad",
            "--program",
            "/bin/true",
            "--calendar",
            "hour=notanumber",
        ],
    )
    assert result.exit_code != 0


def test_install_rejects_bad_env(runner: CliRunner, fake_home: Path) -> None:
    result = runner.invoke(
        app,
        ["install", "com.launchy.bad", "--program", "/bin/true", "--env", "NO_EQUALS"],
    )
    assert result.exit_code != 0


def test_uninstall_with_force_skips_prompt(runner: CliRunner, fake_home: Path) -> None:
    with patch("launchy.launchctl.subprocess.run", side_effect=_install_mocks()):
        runner.invoke(
            app,
            ["install", "com.launchy.u", "--program", "/bin/true"],
        )
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(0)):
        result = runner.invoke(app, ["uninstall", "com.launchy.u", "--force"])
    assert result.exit_code == 0, result.output
    assert "uninstalled" in result.output


def test_uninstall_unknown_label_with_force_is_noop(runner: CliRunner, fake_home: Path) -> None:
    result = runner.invoke(app, ["uninstall", "com.launchy.never", "--force"])
    assert result.exit_code == 0


def test_uninstall_unknown_label_without_force_errors(runner: CliRunner, fake_home: Path) -> None:
    result = runner.invoke(app, ["uninstall", "com.launchy.never"])
    assert result.exit_code != 0


def test_status_human_output(runner: CliRunner, fake_home: Path) -> None:
    with patch("launchy.launchctl.subprocess.run", side_effect=_install_mocks()):
        runner.invoke(app, ["install", "com.launchy.s", "--program", "/bin/true"])
    output = "pid = 999\nlast exit code = 0\n"
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(0, output)):
        result = runner.invoke(app, ["status", "com.launchy.s"])
    assert result.exit_code == 0, result.output
    assert "loaded" in result.output


def test_status_json_output(runner: CliRunner, fake_home: Path) -> None:
    with patch("launchy.launchctl.subprocess.run", side_effect=_install_mocks()):
        runner.invoke(app, ["install", "com.launchy.sj", "--program", "/bin/true"])
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(1)):
        result = runner.invoke(app, ["status", "com.launchy.sj", "--json"])
    assert result.exit_code == 0, result.output
    assert '"loaded": false' in result.output


def test_list_shows_installed_jobs(runner: CliRunner, fake_home: Path) -> None:
    with patch("launchy.launchctl.subprocess.run", side_effect=_install_mocks()):
        runner.invoke(app, ["install", "com.launchy.list1", "--program", "/bin/true"])
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(1)):
        result = runner.invoke(app, ["list", "--prefix", "com.launchy."])
    assert result.exit_code == 0, result.output
    assert "com.launchy.list1" in result.output


def test_show_prints_plist_xml(runner: CliRunner, fake_home: Path) -> None:
    with patch("launchy.launchctl.subprocess.run", side_effect=_install_mocks()):
        runner.invoke(app, ["install", "com.launchy.show", "--program", "/bin/true"])
    result = runner.invoke(app, ["show", "com.launchy.show"])
    assert result.exit_code == 0, result.output
    assert "<plist" in result.output
    assert "com.launchy.show" in result.output


def test_version_flag(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0


# ---- shorthand schedule flags ----------------------------------------------


def test_install_with_every_shorthand(runner: CliRunner, fake_home: Path) -> None:
    with patch("launchy.launchctl.subprocess.run", side_effect=_install_mocks()):
        result = runner.invoke(
            app, ["install", "com.launchy.iv2", "--program", "/bin/true", "--every", "5m"]
        )
    assert result.exit_code == 0, result.output
    plist_path = fake_home / "Library" / "LaunchAgents" / "com.launchy.iv2.plist"
    body = plist_path.read_text()
    assert "<key>StartInterval</key>" in body
    assert "<integer>300</integer>" in body


def test_install_with_at_shorthand(runner: CliRunner, fake_home: Path) -> None:
    with patch("launchy.launchctl.subprocess.run", side_effect=_install_mocks()):
        result = runner.invoke(
            app,
            ["install", "com.launchy.at2", "--program", "/bin/true", "--at", "Mon 09:00"],
        )
    assert result.exit_code == 0, result.output
    body = (fake_home / "Library" / "LaunchAgents" / "com.launchy.at2.plist").read_text()
    assert "<key>StartCalendarInterval</key>" in body
    assert "<key>Weekday</key>" in body
    assert "<integer>1</integer>" in body


def test_install_rejects_every_with_interval(runner: CliRunner, fake_home: Path) -> None:
    result = runner.invoke(
        app,
        [
            "install",
            "com.launchy.x",
            "--program",
            "/bin/true",
            "--interval",
            "60",
            "--every",
            "1m",
        ],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_install_rejects_at_with_interval(runner: CliRunner, fake_home: Path) -> None:
    result = runner.invoke(
        app,
        [
            "install",
            "com.launchy.x",
            "--program",
            "/bin/true",
            "--every",
            "1m",
            "--at",
            "02:00",
        ],
    )
    assert result.exit_code != 0


def test_install_rejects_bad_every(runner: CliRunner, fake_home: Path) -> None:
    result = runner.invoke(
        app,
        ["install", "com.launchy.x", "--program", "/bin/true", "--every", "5q"],
    )
    assert result.exit_code != 0


def test_install_rejects_bad_at(runner: CliRunner, fake_home: Path) -> None:
    result = runner.invoke(
        app, ["install", "com.launchy.x", "--program", "/bin/true", "--at", "99:99"]
    )
    assert result.exit_code != 0


# ---- list filters / -1 ------------------------------------------------------


def _install_pair(runner: CliRunner, label: str) -> None:
    """Install a no-op job for tests. /usr/bin/true is universal across macOS."""
    with patch("launchy.launchctl.subprocess.run", side_effect=_install_mocks()):
        runner.invoke(app, ["install", label, "--program", "/usr/bin/true"])


def test_list_one_line(runner: CliRunner, fake_home: Path) -> None:
    _install_pair(runner, "com.launchy.one1")
    _install_pair(runner, "com.launchy.one2")
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(1)):
        result = runner.invoke(app, ["list", "-1", "--prefix", "com.launchy."])
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert "com.launchy.one1" in lines
    assert "com.launchy.one2" in lines


def test_list_grep_substring(runner: CliRunner, fake_home: Path) -> None:
    _install_pair(runner, "com.launchy.alpha")
    _install_pair(runner, "com.launchy.beta")
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(1)):
        result = runner.invoke(app, ["list", "--grep", "alph"])
    assert result.exit_code == 0
    assert "com.launchy.alpha" in result.output
    assert "com.launchy.beta" not in result.output


def test_list_failed_filter(runner: CliRunner, fake_home: Path) -> None:
    _install_pair(runner, "com.launchy.failtest")
    # status returns loaded with non-zero last exit
    output = "pid = 0\nlast exit code = 2\n"
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(0, output)):
        result = runner.invoke(app, ["list", "--failed"])
    assert result.exit_code == 0
    assert "com.launchy.failtest" in result.output


def test_list_running_filter_excludes_stopped(runner: CliRunner, fake_home: Path) -> None:
    _install_pair(runner, "com.launchy.runonly")
    # status: loaded but no pid → not running
    output = "last exit code = 0\n"
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(0, output)):
        result = runner.invoke(app, ["list", "--running"])
    assert result.exit_code == 0
    assert "com.launchy.runonly" not in result.output


# ---- info ------------------------------------------------------------------


def test_info_prints_summary(runner: CliRunner, fake_home: Path) -> None:
    _install_pair(runner, "com.launchy.infox")
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(1)):
        result = runner.invoke(app, ["info", "com.launchy.infox"])
    assert result.exit_code == 0, result.output
    assert "com.launchy.infox" in result.output
    assert "Scope:" in result.output
    assert "Program:" in result.output
    assert "Schedule:" in result.output
    assert "Status:" in result.output


def test_info_unknown_label_errors(runner: CliRunner, fake_home: Path) -> None:
    result = runner.invoke(app, ["info", "com.launchy.nope"])
    assert result.exit_code != 0


# ---- doctor ----------------------------------------------------------------


def test_doctor_clean_job(runner: CliRunner, fake_home: Path) -> None:
    # Need a trigger to avoid the "no trigger set" warn.
    with patch("launchy.launchctl.subprocess.run", side_effect=_install_mocks()):
        runner.invoke(
            app,
            [
                "install",
                "com.launchy.clean",
                "--program",
                "/usr/bin/true",
                "--run-at-load",
            ],
        )
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(0, "pid = 99\n")):
        result = runner.invoke(app, ["doctor", "com.launchy.clean"])
    assert result.exit_code == 0, result.output
    assert "all clear" in result.output


def test_doctor_flags_missing_program(runner: CliRunner, fake_home: Path) -> None:
    with patch("launchy.launchctl.subprocess.run", side_effect=_install_mocks()):
        runner.invoke(
            app, ["install", "com.launchy.bad1", "--program", "/nonexistent/binary"]
        )
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(0, "pid = 1\n")):
        result = runner.invoke(app, ["doctor", "com.launchy.bad1"])
    assert result.exit_code == 1, result.output
    assert "missing" in result.output
    assert "/nonexistent/binary" in result.output


def test_doctor_flags_short_interval(runner: CliRunner, fake_home: Path) -> None:
    with patch("launchy.launchctl.subprocess.run", side_effect=_install_mocks()):
        runner.invoke(
            app,
            [
                "install",
                "com.launchy.fast",
                "--program",
                "/usr/bin/true",
                "--interval",
                "3",
            ],
        )
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(0, "pid = 1\n")):
        result = runner.invoke(app, ["doctor", "com.launchy.fast"])
    # warn-only → exit 0
    assert result.exit_code == 0, result.output
    assert "CPU thrash" in result.output


def test_doctor_flags_no_trigger(runner: CliRunner, fake_home: Path) -> None:
    _install_pair(runner, "com.launchy.idle")
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(0, "pid = 1\n")):
        result = runner.invoke(app, ["doctor", "com.launchy.idle"])
    # No trigger set → warn (job will never run)
    assert result.exit_code == 0
    assert "never run" in result.output


def test_doctor_verbose_shows_passing_checks(runner: CliRunner, fake_home: Path) -> None:
    _install_pair(runner, "com.launchy.vv")
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(0, "pid = 1\n")):
        result = runner.invoke(app, ["doctor", "com.launchy.vv", "-v"])
    assert result.exit_code == 0
    # ok rows render the program check
    assert "program" in result.output


# ---- completion: fuzzy matching --------------------------------------------


def test_fuzzy_score_subsequence_required() -> None:
    from launchy.cli import _fuzzy_score

    # No subsequence → None
    assert _fuzzy_score("xyz", "com.launchy.backup") is None
    # Subsequence (in order) → some score
    assert _fuzzy_score("clb", "com.launchy.backup") is not None
    # Out-of-order chars don't match
    assert _fuzzy_score("bal", "com.launchy.backup") is None  # 'l' after 'a'


def test_fuzzy_score_prefix_beats_midword() -> None:
    """Same query, different positions — prefix match wins."""
    from launchy.cli import _fuzzy_score

    prefix = _fuzzy_score("com", "com.launchy.backup")  # 'com' at index 0
    mid = _fuzzy_score("com", "user.dotcom.app")  # 'com' at index 8
    assert prefix is not None and mid is not None
    assert prefix > mid


def test_fuzzy_score_word_boundary_beats_random() -> None:
    from launchy.cli import _fuzzy_score

    boundary = _fuzzy_score("b", "com.launchy.backup")  # matches the 'b' after '.'
    midword = _fuzzy_score("a", "com.launchy.backup")  # matches 'a' in 'launchy'
    assert boundary is not None and midword is not None
    assert boundary > midword


def test_fuzzy_score_shorter_wins_on_tie() -> None:
    from launchy.cli import _fuzzy_score

    short = _fuzzy_score("com", "com.x")
    long = _fuzzy_score("com", "com.something.very.long")
    assert short is not None and long is not None
    assert short > long


def test_fuzzy_score_empty_query_matches_anything() -> None:
    from launchy.cli import _fuzzy_score

    assert _fuzzy_score("", "com.launchy.x") == 0.0


def test_complete_label_ranks_matches(fake_home: Path) -> None:
    """Real glob: install several jobs, query partial label, check ordering."""
    from launchy.cli import _complete_label

    for label in ("com.launchy.backup", "com.launchy.bench", "com.other.thing"):
        _install_pair(runner_fixture := CliRunner(), label)  # noqa: F841

    # "bac" should hit "backup" (substring) but not "bench" or "thing"
    results = _complete_label("bac")
    assert "com.launchy.backup" in results
    assert "com.launchy.bench" not in results
    assert "com.other.thing" not in results


def test_complete_label_empty_query_returns_all(fake_home: Path) -> None:
    from launchy.cli import _complete_label

    for label in ("com.launchy.a", "com.launchy.b"):
        _install_pair(CliRunner(), label)
    results = _complete_label("")
    assert {"com.launchy.a", "com.launchy.b"} <= set(results)


# ---- disable / enable ------------------------------------------------------


def test_disable_calls_launchctl_disable_then_bootout(
    runner: CliRunner, fake_home: Path
) -> None:
    _install_pair(runner, "com.launchy.dis")
    with patch(
        "launchy.launchctl.subprocess.run",
        side_effect=[_completed(0), _completed(0)],
    ) as run:
        result = runner.invoke(app, ["disable", "com.launchy.dis", "--force"])
    assert result.exit_code == 0, result.output
    verbs = [c.args[0][1] for c in run.call_args_list]
    assert verbs == ["disable", "bootout"]
    assert "disabled" in result.output


def test_enable_calls_launchctl_enable_then_bootstrap(
    runner: CliRunner, fake_home: Path
) -> None:
    _install_pair(runner, "com.launchy.en")
    # enable → 0, print → 1 (not loaded), bootstrap → 0. (enable is idempotent:
    # if already loaded, it bootouts first; this test covers the not-loaded path.)
    with patch(
        "launchy.launchctl.subprocess.run",
        side_effect=[_completed(0), _completed(1), _completed(0)],
    ) as run:
        result = runner.invoke(app, ["enable", "com.launchy.en", "--force"])
    assert result.exit_code == 0, result.output
    verbs = [c.args[0][1] for c in run.call_args_list]
    assert verbs == ["enable", "print", "bootstrap"]
    assert "enabled" in result.output


# ---- bulk semantics --------------------------------------------------------


def test_bulk_uninstall_by_grep(runner: CliRunner, fake_home: Path) -> None:
    _install_pair(runner, "com.launchy.bulk1")
    _install_pair(runner, "com.launchy.bulk2")
    _install_pair(runner, "com.launchy.keep")
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(0)):
        result = runner.invoke(app, ["rm", "--grep", "bulk", "--force"])
    assert result.exit_code == 0, result.output
    assert "com.launchy.bulk1" in result.output
    assert "com.launchy.bulk2" in result.output
    assert "com.launchy.keep" not in result.output


def test_bulk_uninstall_by_prefix(runner: CliRunner, fake_home: Path) -> None:
    _install_pair(runner, "com.launchy.px1")
    _install_pair(runner, "com.other.thing")
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(0)):
        result = runner.invoke(
            app, ["rm", "--prefix", "com.launchy.", "--force"]
        )
    assert result.exit_code == 0, result.output
    assert "com.launchy.px1" in result.output
    assert "com.other.thing" not in result.output


def test_bulk_rejects_label_with_filter(runner: CliRunner, fake_home: Path) -> None:
    result = runner.invoke(
        app, ["rm", "com.launchy.x", "--grep", "y", "--force"]
    )
    assert result.exit_code != 0


def test_bulk_rejects_no_target(runner: CliRunner, fake_home: Path) -> None:
    result = runner.invoke(app, ["start", "--force"])
    assert result.exit_code != 0


def test_bulk_empty_match_is_noop(runner: CliRunner, fake_home: Path) -> None:
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(1)):
        result = runner.invoke(
            app, ["stop", "--grep", "definitely-no-match", "--force"]
        )
    assert result.exit_code == 0
    assert "no jobs matched" in result.output


# ---- install success message -----------------------------------------------


def test_install_success_shows_status_and_plist(
    runner: CliRunner, fake_home: Path
) -> None:
    with patch(
        "launchy.launchctl.subprocess.run",
        side_effect=[_completed(1), _completed(0), _completed(0, "pid = 1\n")],
    ):
        result = runner.invoke(
            app,
            [
                "install",
                "com.launchy.msg",
                "--program",
                "/usr/bin/true",
                "--run-at-load",
            ],
        )
    assert result.exit_code == 0, result.output
    assert "installed" in result.output
    assert "loaded" in result.output
    assert "Plist:" in result.output


def test_install_surfaces_doctor_warnings(runner: CliRunner, fake_home: Path) -> None:
    with patch(
        "launchy.launchctl.subprocess.run",
        side_effect=_install_mocks(),
    ):
        result = runner.invoke(
            app,
            ["install", "com.launchy.notrig", "--program", "/usr/bin/true"],
        )
    assert result.exit_code == 0, result.output
    assert "never run" in result.output  # no trigger warning surfaced inline
