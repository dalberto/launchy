from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from launchy import plist
from launchy.plist import build_payload, loads, render


def _roundtrip(**kwargs: Any) -> dict[str, Any]:
    payload = build_payload(label="com.launchy.t", program=["/bin/echo", "hi"], **kwargs)
    return loads(render(payload).encode("utf-8"))


def test_minimal_payload() -> None:
    parsed = _roundtrip()
    assert parsed == {"Label": "com.launchy.t", "ProgramArguments": ["/bin/echo", "hi"]}


def test_run_at_load_and_start_on_mount() -> None:
    parsed = _roundtrip(run_at_load=True, start_on_mount=True)
    assert parsed["RunAtLoad"] is True
    assert parsed["StartOnMount"] is True


def test_interval_serialized_as_seconds() -> None:
    parsed = _roundtrip(interval=timedelta(minutes=5))
    assert parsed["StartInterval"] == 300


def test_interval_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        build_payload(
            label="x", program=["/bin/true"], interval=timedelta(0)
        )


def test_calendar_single_dict_maps_to_pascal_case() -> None:
    parsed = _roundtrip(calendar={"hour": 2, "minute": 30, "weekday": 1})
    assert parsed["StartCalendarInterval"] == {"Hour": 2, "Minute": 30, "Weekday": 1}


def test_calendar_list_of_specs() -> None:
    parsed = _roundtrip(calendar=[{"hour": 9}, {"hour": 17}])
    assert parsed["StartCalendarInterval"] == [{"Hour": 9}, {"Hour": 17}]


def test_calendar_unknown_key_rejected() -> None:
    with pytest.raises(ValueError, match="unknown CalendarSpec key"):
        build_payload(
            label="x", program=["/bin/true"], calendar={"bogus": 1}  # type: ignore[typeddict-unknown-key]
        )


def test_watch_paths_serialized_as_strings() -> None:
    parsed = _roundtrip(watch_paths=[Path("/tmp/a"), Path("/tmp/b")])
    assert parsed["WatchPaths"] == ["/tmp/a", "/tmp/b"]


def test_queue_directories_serialized_as_strings() -> None:
    parsed = _roundtrip(queue_directories=[Path("/tmp/q")])
    assert parsed["QueueDirectories"] == ["/tmp/q"]


def test_keep_alive_bool_true() -> None:
    parsed = _roundtrip(keep_alive=True)
    assert parsed["KeepAlive"] is True


def test_keep_alive_conditions_mapped_to_pascal_case() -> None:
    parsed = _roundtrip(keep_alive={"successful_exit": False, "crashed": True})
    assert parsed["KeepAlive"] == {"SuccessfulExit": False, "Crashed": True}


def test_keep_alive_path_state_preserves_nested_keys() -> None:
    parsed = _roundtrip(keep_alive={"path_state": {"/tmp/heartbeat": True}})
    assert parsed["KeepAlive"] == {"PathState": {"/tmp/heartbeat": True}}


def test_env_and_working_dir_and_logs() -> None:
    parsed = _roundtrip(
        env={"FOO": "bar"},
        working_dir=Path("/var/tmp"),
        stdout_path=Path("/tmp/o.log"),
        stderr_path=Path("/tmp/e.log"),
    )
    assert parsed["EnvironmentVariables"] == {"FOO": "bar"}
    assert parsed["WorkingDirectory"] == "/var/tmp"
    assert parsed["StandardOutPath"] == "/tmp/o.log"
    assert parsed["StandardErrorPath"] == "/tmp/e.log"


def test_loads_rejects_non_dict_root() -> None:
    raw = b'<?xml version="1.0"?><plist version="1.0"><array/></plist>'
    with pytest.raises(ValueError, match="root must be a dict"):
        loads(raw)


def test_from_plist_calendar_inverse_of_to_plist() -> None:
    spec: dict[str, int] = {"hour": 2, "minute": 0, "weekday": 1}
    serialized = plist.calendar_to_plist(spec)  # type: ignore[arg-type]
    assert plist.calendar_from_plist(serialized) == spec


def test_from_plist_keep_alive_handles_bool_and_dict() -> None:
    assert plist.keep_alive_from_plist(True) is True
    assert plist.keep_alive_from_plist({"SuccessfulExit": False}) == {"successful_exit": False}


def test_from_plist_keep_alive_rejects_unknown_key() -> None:
    with pytest.raises(ValueError, match="unknown KeepAlive plist key"):
        plist.keep_alive_from_plist({"BogusKey": True})


def test_render_produces_valid_xml_header() -> None:
    out = render(build_payload(label="x", program=["/bin/true"]))
    assert out.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert "<plist version=\"1.0\">" in out
