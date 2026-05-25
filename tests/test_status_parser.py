from __future__ import annotations

from launchy.status import JobStatus, parse


def test_non_zero_returncode_is_not_loaded() -> None:
    result = parse("com.launchy.x", returncode=1, output="")
    assert result == JobStatus(label="com.launchy.x", loaded=False, pid=None, last_exit_code=None)


def test_running_service_extracts_pid_and_never_exited() -> None:
    output = """gui/501/com.launchy.x = {
\ttype = LaunchAgent
\tstate = running
\truns = 1
\tpid = 4242
\tlast exit code = (never exited)
}
"""
    result = parse("com.launchy.x", returncode=0, output=output)
    assert result.loaded is True
    assert result.pid == 4242
    assert result.last_exit_code is None


def test_loaded_but_not_running_extracts_exit_code() -> None:
    output = """gui/501/com.launchy.x = {
\ttype = LaunchAgent
\tstate = not running
\truns = 3
\tlast exit code = -9
}
"""
    result = parse("com.launchy.x", returncode=0, output=output)
    assert result.loaded is True
    assert result.pid is None
    assert result.last_exit_code == -9


def test_zero_exit_code_is_extracted() -> None:
    output = "state = not running\nlast exit code = 0\n"
    result = parse("x", returncode=0, output=output)
    assert result.last_exit_code == 0
