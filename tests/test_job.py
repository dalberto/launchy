from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from launchy import Job, JobStatus, Scope
from launchy import plist as plist_mod
from launchy.exceptions import JobNotFound, NotInstalled, PermissionDeniedError


def _completed(returncode: int, stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point Path.home() and the resolved plist dir at tmp_path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from launchy import paths

    monkeypatch.setattr(paths, "_USER_DIR", tmp_path / "Library" / "LaunchAgents")
    yield tmp_path


def test_job_requires_label_and_program() -> None:
    with pytest.raises(ValueError, match="label"):
        Job(label="", program=["/bin/true"])
    with pytest.raises(ValueError, match="program"):
        Job(label="x", program=[])


def test_render_includes_log_paths_when_log_dir_set(fake_home: Path) -> None:
    job = Job(
        label="com.launchy.x",
        program=["/bin/true"],
        log_dir=fake_home / "logs",
    )
    rendered = plist_mod.loads(job.render().encode("utf-8"))
    assert rendered["StandardOutPath"].endswith("com.launchy.x.out.log")
    assert rendered["StandardErrorPath"].endswith("com.launchy.x.err.log")


def test_install_writes_plist_and_bootstraps(fake_home: Path) -> None:
    job = Job(label="com.launchy.install", program=["/bin/echo", "hi"], run_at_load=True)

    # print_service returns non-zero (not loaded) → no bootout call; then bootstrap succeeds
    side_effects = [_completed(1), _completed(0)]
    with patch("launchy.launchctl.subprocess.run", side_effect=side_effects) as run:
        path = job.install()

    assert path == job.plist_path
    assert path.exists()
    parsed = plist_mod.loads(path.read_bytes())
    assert parsed["Label"] == "com.launchy.install"
    assert parsed["RunAtLoad"] is True

    argvs = [call.args[0] for call in run.call_args_list]
    assert argvs[0][:2] == ["launchctl", "print"]
    assert argvs[1] == ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)]


def test_install_is_idempotent_when_already_loaded(fake_home: Path) -> None:
    job = Job(label="com.launchy.again", program=["/bin/true"])
    # print → loaded (0), bootout → 0, bootstrap → 0
    with patch(
        "launchy.launchctl.subprocess.run",
        side_effect=[_completed(0, "state = running\n"), _completed(0), _completed(0)],
    ) as run:
        job.install()
    verbs = [call.args[0][1] for call in run.call_args_list]
    assert verbs == ["print", "bootout", "bootstrap"]


def test_uninstall_removes_plist_and_calls_bootout(fake_home: Path) -> None:
    job = Job(label="com.launchy.rm", program=["/bin/true"])
    job.plist_path.parent.mkdir(parents=True, exist_ok=True)
    job.plist_path.write_text("<plist/>", encoding="utf-8")

    with patch("launchy.launchctl.subprocess.run", return_value=_completed(0)) as run:
        job.uninstall()

    assert not job.plist_path.exists()
    assert run.call_args.args[0] == [
        "launchctl",
        "bootout",
        f"gui/{os.getuid()}/com.launchy.rm",
    ]


def test_uninstall_tolerates_missing_plist(fake_home: Path) -> None:
    job = Job(label="com.launchy.gone", program=["/bin/true"])
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(1)):
        job.uninstall()  # must not raise


def test_start_requires_installed_plist(fake_home: Path) -> None:
    job = Job(label="com.launchy.start", program=["/bin/true"])
    with pytest.raises(NotInstalled):
        job.start()


def test_status_returns_parsed_jobstatus(fake_home: Path) -> None:
    job = Job(label="com.launchy.s", program=["/bin/true"])
    output = "state = running\npid = 1234\nlast exit code = (never exited)\n"
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(0, output)):
        st = job.status()
    assert st == JobStatus(label="com.launchy.s", loaded=True, pid=1234, last_exit_code=None)


def test_status_when_not_loaded(fake_home: Path) -> None:
    job = Job(label="com.launchy.s", program=["/bin/true"])
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(1)):
        st = job.status()
    assert st.loaded is False
    assert st.pid is None


def test_load_round_trips_a_job(fake_home: Path) -> None:
    original = Job(
        label="com.launchy.rt",
        program=["/usr/bin/python3", "-m", "foo"],
        interval=timedelta(seconds=600),
        keep_alive={"successful_exit": False},
        env={"FOO": "bar"},
        log_dir=fake_home / "logs",
    )
    with patch("launchy.launchctl.subprocess.run", side_effect=[_completed(1), _completed(0)]):
        original.install()

    loaded = Job.load("com.launchy.rt")
    assert loaded.label == "com.launchy.rt"
    assert loaded.program == ["/usr/bin/python3", "-m", "foo"]
    assert loaded.interval == timedelta(seconds=600)
    assert loaded.keep_alive == {"successful_exit": False}
    assert loaded.env == {"FOO": "bar"}
    assert loaded.log_dir == fake_home / "logs"


def test_load_raises_when_plist_missing(fake_home: Path) -> None:
    with pytest.raises(JobNotFound):
        Job.load("com.launchy.nope")


def test_list_filters_by_prefix(fake_home: Path) -> None:
    for label in ("com.launchy.a", "com.launchy.b", "com.other.c"):
        Job(label=label, program=["/bin/true"]).plist_path.parent.mkdir(parents=True, exist_ok=True)
        path = Job(label=label, program=["/bin/true"]).plist_path
        path.write_text(Job(label=label, program=["/bin/true"]).render(), encoding="utf-8")

    all_jobs = Job.list(scope=Scope.USER)
    assert {j.label for j in all_jobs} == {"com.launchy.a", "com.launchy.b", "com.other.c"}

    filtered = Job.list(prefix="com.launchy.", scope=Scope.USER)
    assert {j.label for j in filtered} == {"com.launchy.a", "com.launchy.b"}


def test_list_returns_empty_when_dir_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from launchy import paths

    monkeypatch.setattr(paths, "_USER_DIR", tmp_path / "does-not-exist")
    assert Job.list(scope=Scope.USER) == []


def test_list_across_all_scopes_when_scope_is_none(
    fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`scope=None` (the default) iterates user, all-users, and system."""
    from launchy import paths

    all_users_dir = tmp_path / "alt-Library" / "LaunchAgents"
    system_dir = tmp_path / "alt-Library" / "LaunchDaemons"
    all_users_dir.mkdir(parents=True, exist_ok=True)
    system_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paths, "_ALL_USERS_DIR", all_users_dir)
    monkeypatch.setattr(paths, "_SYSTEM_DIR", system_dir)

    user_job = Job(label="com.launchy.u", program=["/bin/true"], scope=Scope.USER)
    user_job.plist_path.parent.mkdir(parents=True, exist_ok=True)
    user_job.plist_path.write_text(user_job.render(), encoding="utf-8")

    au_job = Job(label="com.launchy.au", program=["/bin/true"], scope=Scope.ALL_USERS)
    au_job.plist_path.write_text(au_job.render(), encoding="utf-8")

    sys_job = Job(label="com.launchy.s", program=["/bin/true"], scope=Scope.SYSTEM)
    sys_job.plist_path.write_text(sys_job.render(), encoding="utf-8")

    jobs = Job.list()  # no scope = all
    by_label = {j.label: j.scope for j in jobs}
    assert by_label == {
        "com.launchy.u": Scope.USER,
        "com.launchy.au": Scope.ALL_USERS,
        "com.launchy.s": Scope.SYSTEM,
    }


def test_list_skips_binary_and_malformed_plists(
    fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real /Library/LaunchDaemons has binary plists; list should skip junk, not crash."""
    import plistlib

    binary_dir = tmp_path / "alt-Library" / "LaunchDaemons"
    binary_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(__import__("launchy").paths, "_SYSTEM_DIR", binary_dir)

    # Valid binary plist with all required fields.
    (binary_dir / "com.foreign.daemon.plist").write_bytes(
        plistlib.dumps(
            {"Label": "com.foreign.daemon", "ProgramArguments": ["/bin/true"]},
            fmt=plistlib.FMT_BINARY,
        )
    )
    # Garbage that should be silently skipped.
    (binary_dir / "com.malformed.plist").write_bytes(b"not a plist at all")
    # Valid plist but missing required keys (third-party that doesn't match our schema).
    (binary_dir / "com.schema-mismatch.plist").write_bytes(
        plistlib.dumps({"Label": "com.schema-mismatch"}, fmt=plistlib.FMT_BINARY)
    )

    jobs = Job.list(scope=Scope.SYSTEM)
    assert {j.label for j in jobs} == {"com.foreign.daemon"}


def test_daemon_scope_requires_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 501)
    job = Job(label="com.launchy.d", program=["/bin/true"], scope=Scope.SYSTEM)
    with pytest.raises(PermissionDeniedError, match="sudo"):
        job.install()


def test_calendar_list_round_trips(fake_home: Path) -> None:
    job = Job(
        label="com.launchy.cal",
        program=["/bin/true"],
        calendar=[{"hour": 9}, {"hour": 17}],
    )
    with patch("launchy.launchctl.subprocess.run", side_effect=[_completed(1), _completed(0)]):
        job.install()
    loaded = Job.load("com.launchy.cal")
    assert loaded.calendar == [{"hour": 9}, {"hour": 17}]


# ---- Job.find -----------------------------------------------------------------


def test_find_falls_back_through_scopes(
    fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from launchy import paths

    system_dir = tmp_path / "alt-system"
    system_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paths, "_SYSTEM_DIR", system_dir)

    sys_job = Job(label="com.launchy.daemon", program=["/bin/true"], scope=Scope.SYSTEM)
    sys_job.plist_path.write_text(sys_job.render(), encoding="utf-8")

    found = Job.find("com.launchy.daemon")
    assert found.scope is Scope.SYSTEM


def test_find_respects_explicit_scope_list(fake_home: Path) -> None:
    user_job = Job(label="com.launchy.only", program=["/bin/true"], scope=Scope.USER)
    user_job.plist_path.parent.mkdir(parents=True, exist_ok=True)
    user_job.plist_path.write_text(user_job.render(), encoding="utf-8")

    with pytest.raises(JobNotFound):
        Job.find("com.launchy.only", scopes=[Scope.SYSTEM])


def test_find_raises_when_no_scope_matches(fake_home: Path) -> None:
    with pytest.raises(JobNotFound, match="no job 'com.launchy.ghost'"):
        Job.find("com.launchy.ghost")


# ---- Job.disable / Job.enable -------------------------------------------------


def test_disable_runs_launchctl_disable_then_bootout(fake_home: Path) -> None:
    job = Job(label="com.launchy.dis", program=["/bin/true"])
    job.plist_path.parent.mkdir(parents=True, exist_ok=True)
    job.plist_path.write_text(job.render(), encoding="utf-8")

    with patch(
        "launchy.launchctl.subprocess.run", side_effect=[_completed(0), _completed(0)]
    ) as run:
        job.disable()
    verbs = [c.args[0][1] for c in run.call_args_list]
    assert verbs == ["disable", "bootout"]


def test_enable_runs_launchctl_enable_then_bootstrap(fake_home: Path) -> None:
    """First-time enable on a not-loaded job: enable → check load → bootstrap."""
    job = Job(label="com.launchy.en", program=["/bin/true"])
    job.plist_path.parent.mkdir(parents=True, exist_ok=True)
    job.plist_path.write_text(job.render(), encoding="utf-8")

    # enable → 0, print → 1 (not loaded), bootstrap → 0
    with patch(
        "launchy.launchctl.subprocess.run",
        side_effect=[_completed(0), _completed(1), _completed(0)],
    ) as run:
        job.enable()
    verbs = [c.args[0][1] for c in run.call_args_list]
    assert verbs == ["enable", "print", "bootstrap"]


def test_disable_requires_installed_plist(fake_home: Path) -> None:
    from launchy.exceptions import NotInstalled

    job = Job(label="com.launchy.nope", program=["/bin/true"])
    with pytest.raises(NotInstalled):
        job.disable()


# ---- Job.diagnose -------------------------------------------------------------


def test_diagnose_returns_diagnostic_list(fake_home: Path) -> None:
    from launchy import Diagnostic

    job = Job(label="com.launchy.diag", program=["/usr/bin/true"], run_at_load=True)
    job.plist_path.parent.mkdir(parents=True, exist_ok=True)
    job.plist_path.write_text(job.render(), encoding="utf-8")
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(0, "pid = 99\n")):
        results = job.diagnose()
    assert all(isinstance(d, Diagnostic) for d in results)
    severities = {d.severity for d in results}
    # /usr/bin/true exists + has trigger → all ok
    assert severities == {"ok"}


def test_diagnose_surfaces_missing_program(fake_home: Path) -> None:
    job = Job(label="com.launchy.gone", program=["/nonexistent"], run_at_load=True)
    job.plist_path.parent.mkdir(parents=True, exist_ok=True)
    job.plist_path.write_text(job.render(), encoding="utf-8")
    with patch("launchy.launchctl.subprocess.run", return_value=_completed(0, "pid = 99\n")):
        results = job.diagnose()
    fails = [d for d in results if d.severity == "fail"]
    assert any(d.check == "program" for d in fails)


# ---- idempotency --------------------------------------------------------------


def test_stop_is_noop_when_already_stopped(fake_home: Path) -> None:
    """status returns no pid → stop should not invoke launchctl kill."""
    job = Job(label="com.launchy.stop1", program=["/bin/true"])
    job.plist_path.parent.mkdir(parents=True, exist_ok=True)
    job.plist_path.write_text(job.render(), encoding="utf-8")
    # print returns "no pid" (loaded but not running)
    with patch(
        "launchy.launchctl.subprocess.run",
        return_value=_completed(0, "state = not running\n"),
    ) as run:
        job.stop()
    verbs = [c.args[0][1] for c in run.call_args_list]
    assert verbs == ["print"]  # no `kill` was attempted


def test_stop_swallows_race_no_process_to_signal(fake_home: Path) -> None:
    """If process exits between status() and kill, treat as already-stopped."""
    job = Job(label="com.launchy.stop2", program=["/bin/true"])
    job.plist_path.parent.mkdir(parents=True, exist_ok=True)
    job.plist_path.write_text(job.render(), encoding="utf-8")
    # First call: print → has pid; second call: kill → exit 3 "No process to signal"
    print_resp = _completed(0, "pid = 999\n")
    kill_fail = subprocess.CompletedProcess(
        args=[], returncode=3, stdout="", stderr="No process to signal."
    )
    with patch("launchy.launchctl.subprocess.run", side_effect=[print_resp, kill_fail]):
        job.stop()  # must not raise


def test_enable_is_idempotent(fake_home: Path) -> None:
    """A second enable() must not fail with bootstrap's already-loaded error."""
    job = Job(label="com.launchy.en2", program=["/bin/true"])
    job.plist_path.parent.mkdir(parents=True, exist_ok=True)
    job.plist_path.write_text(job.render(), encoding="utf-8")
    # enable → 0, print (loaded) → 0, bootout → 0, bootstrap → 0
    with patch(
        "launchy.launchctl.subprocess.run",
        side_effect=[_completed(0), _completed(0, "loaded"), _completed(0), _completed(0)],
    ) as run:
        job.enable()
    verbs = [c.args[0][1] for c in run.call_args_list]
    assert verbs == ["enable", "print", "bootout", "bootstrap"]
