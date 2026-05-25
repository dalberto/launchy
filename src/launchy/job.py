"""Job dataclass: the primary library API.

Construct a Job, then call lifecycle methods. install()/uninstall()/start()/
stop()/reload()/status() each map to a launchctl invocation; the heavy
lifting lives in plist.py + launchctl.py + status.py.
"""

from __future__ import annotations

import os
import plistlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Self
from xml.parsers.expat import ExpatError

from . import diagnostics, launchctl, paths, plist, status
from .diagnostics import Diagnostic
from .exceptions import JobNotFound, LaunchctlError, NotInstalled, PermissionDeniedError
from .paths import Scope
from .plist import CalendarSpec, KeepAliveConditions
from .status import JobStatus


@dataclass
class Job:
    label: str
    program: list[str]
    scope: Scope = Scope.USER

    # Triggers — all optional, native types throughout.
    run_at_load: bool = False
    start_on_mount: bool = False
    interval: timedelta | None = None
    calendar: CalendarSpec | list[CalendarSpec] | None = None
    watch_paths: list[Path] = field(default_factory=list[Path])
    queue_directories: list[Path] = field(default_factory=list[Path])
    keep_alive: bool | KeepAliveConditions | None = None

    # Runtime config
    env: dict[str, str] = field(default_factory=dict[str, str])
    working_dir: Path | None = None
    # log_dir auto-derives `<log_dir>/<label>.out.log` and `.err.log`. Pass
    # explicit stdout_path/stderr_path instead to use arbitrary file paths.
    log_dir: Path | None = None
    stdout_path: Path | None = None  # overrides log_dir derivation
    stderr_path: Path | None = None  # overrides log_dir derivation

    def __post_init__(self) -> None:
        if not self.label:
            raise ValueError("label is required")
        if not self.program:
            raise ValueError("program (argv) is required")

    # ---- paths ---------------------------------------------------------------

    @property
    def plist_path(self) -> Path:
        return paths.plist_path(self.scope, self.label)

    @property
    def _service_target(self) -> str:
        return paths.service_target(self.scope, self.label)

    @property
    def _domain(self) -> str:
        return paths.domain_target(self.scope)

    @property
    def resolved_stdout_path(self) -> Path | None:
        """StandardOutPath: explicit override, or derived from log_dir."""
        if self.stdout_path is not None:
            return self.stdout_path
        return (self.log_dir / f"{self.label}.out.log") if self.log_dir else None

    @property
    def resolved_stderr_path(self) -> Path | None:
        """StandardErrorPath: explicit override, or derived from log_dir."""
        if self.stderr_path is not None:
            return self.stderr_path
        return (self.log_dir / f"{self.label}.err.log") if self.log_dir else None

    # ---- rendering -----------------------------------------------------------

    def render(self) -> str:
        """Serialize this Job to plist XML without writing anything."""
        payload = plist.build_payload(
            label=self.label,
            program=self.program,
            run_at_load=self.run_at_load,
            start_on_mount=self.start_on_mount,
            interval=self.interval,
            calendar=self.calendar,
            watch_paths=self.watch_paths or None,
            queue_directories=self.queue_directories or None,
            keep_alive=self.keep_alive,
            env=self.env or None,
            working_dir=self.working_dir,
            stdout_path=self.resolved_stdout_path,
            stderr_path=self.resolved_stderr_path,
        )
        return plist.render(payload)

    # ---- lifecycle -----------------------------------------------------------

    def install(self) -> Path:
        """Write the plist and bootstrap into launchd.

        Idempotent: if the service is already loaded, bootout first so launchd
        picks up any changes.
        """
        self._check_root()
        if self.log_dir is not None:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        self.plist_path.parent.mkdir(parents=True, exist_ok=True)
        self.plist_path.write_text(self.render(), encoding="utf-8")

        # Idempotent bootstrap: if loaded, unload first.
        if launchctl.print_service(self._service_target).returncode == 0:
            launchctl.bootout(self._service_target)
        launchctl.bootstrap(self._domain, self.plist_path)
        return self.plist_path

    def uninstall(self) -> None:
        """Best-effort bootout + remove the plist."""
        self._check_root()
        launchctl.bootout(self._service_target)
        self.plist_path.unlink(missing_ok=True)

    def start(self) -> None:
        """Force the job to run now (kickstart -k)."""
        self._require_installed()
        launchctl.kickstart(self._service_target, kill_existing=True)

    def stop(self) -> None:
        """Send SIGTERM to the running job. No-op if already stopped."""
        self._require_installed()
        if self.status().pid is None:
            return
        try:
            launchctl.kill(self._service_target, "SIGTERM")
        except LaunchctlError as exc:
            # Race: status() saw a pid but the process exited before we sent
            # the signal. launchctl returns "No process to signal" (exit 3).
            if "No process to signal" in exc.stderr:
                return
            raise

    def reload(self) -> None:
        """Bootout + bootstrap. Use after editing the plist on disk."""
        self._check_root()
        if not self.plist_path.exists():
            raise NotInstalled(f"plist not on disk: {self.plist_path}")
        launchctl.bootout(self._service_target)
        launchctl.bootstrap(self._domain, self.plist_path)

    def disable(self) -> None:
        """Mark disabled in launchd's persistent store, then unload.

        Survives reboots — the job won't reappear on next bootstrap. Use
        `enable()` to undo. Distinct from `stop()`/`uninstall()`: the plist
        stays on disk; only the registration is removed and disabled.
        """
        self._check_root()
        self._require_installed()
        launchctl.disable(self._service_target)
        launchctl.bootout(self._service_target)

    def enable(self) -> None:
        """Remove the disabled flag and bootstrap the job. Idempotent.

        Mirrors `install()`'s pattern: if the job is already loaded, bootout
        first so a second `enable()` call doesn't trip `bootstrap`'s
        "already-loaded" failure.
        """
        self._check_root()
        self._require_installed()
        launchctl.enable(self._service_target)
        if launchctl.print_service(self._service_target).returncode == 0:
            launchctl.bootout(self._service_target)
        launchctl.bootstrap(self._domain, self.plist_path)

    def status(self) -> JobStatus:
        """Query loaded state, pid, and last exit code."""
        result = launchctl.print_service(self._service_target)
        return status.parse(self.label, result.returncode, result.stdout)

    def diagnose(self) -> list[Diagnostic]:
        """Run health checks. Wraps `diagnostics.diagnose(self, self.status())`."""
        return diagnostics.diagnose(self, self.status())

    # ---- helpers -------------------------------------------------------------

    def _check_root(self) -> None:
        if paths.requires_root(self.scope) and os.geteuid() != 0:
            raise PermissionDeniedError(
                f"scope={self.scope.value} requires root; re-run with sudo"
            )

    def _require_installed(self) -> None:
        if not self.plist_path.exists():
            raise NotInstalled(f"plist not on disk: {self.plist_path}")

    # ---- factories -----------------------------------------------------------

    @classmethod
    def load(cls, label: str, scope: Scope = Scope.USER) -> Self:
        """Rehydrate a Job from a plist in the given scope. Raises JobNotFound."""
        path = paths.plist_path(scope, label)
        if not path.exists():
            hint = (
                " (try --scope all-users or --scope system)"
                if scope is Scope.USER
                else ""
            )
            raise JobNotFound(f"no job '{label}' in scope {scope.value}{hint}")
        parsed = plist.loads(path.read_bytes())
        return cls._from_plist(parsed, scope)

    @classmethod
    def find(cls, label: str, scopes: list[Scope] | None = None) -> Self:
        """Find a job by label across scopes; return the first match.

        Searches `scopes` in order (defaults to USER, ALL_USERS, SYSTEM).
        Use `Job.load(label, scope=...)` instead when you know the scope.
        """
        candidates = scopes if scopes is not None else list(Scope)
        last_err: JobNotFound | None = None
        for s in candidates:
            try:
                return cls.load(label, scope=s)
            except JobNotFound as exc:
                last_err = exc
        names = [s.value for s in candidates]
        raise JobNotFound(f"no job '{label}' in scopes {names}") from last_err

    @classmethod
    def list(cls, prefix: str = "", scope: Scope | None = None) -> list[Self]:
        """Glob the plist directory and return Jobs whose label starts with prefix.

        When scope is None, iterates all three scopes and concatenates. Pass
        an explicit Scope to filter. Plists from third parties (Homebrew,
        Adobe, etc.) won't necessarily have the keys we expect — skip
        anything we can't parse rather than crashing.
        """
        scopes = [scope] if scope is not None else list(Scope)
        jobs: list[Self] = []
        for s in scopes:
            directory = paths.plist_dir(s)
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.plist")):
                label = path.stem
                if prefix and not label.startswith(prefix):
                    continue
                try:
                    parsed = plist.loads(path.read_bytes())
                    jobs.append(cls._from_plist(parsed, s))
                except (ValueError, OSError, plistlib.InvalidFileException, ExpatError):
                    continue
        return jobs

    @classmethod
    def list_with_status(
        cls, prefix: str = "", scope: Scope | None = None
    ) -> list[tuple[Self, JobStatus]]:
        """Like list(), but pairs each Job with its current JobStatus.

        Status calls run in a thread pool — `launchctl print` is blocking I/O,
        so threads parallelize fine without async.
        """
        jobs = cls.list(prefix=prefix, scope=scope)
        if not jobs:
            return []

        def _status_of(j: Self) -> JobStatus:
            return j.status()

        with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as ex:
            statuses: list[JobStatus] = list(ex.map(_status_of, jobs))
        return list(zip(jobs, statuses, strict=True))

    @classmethod
    def _from_plist(cls, payload: dict[str, Any], scope: Scope) -> Self:
        label = payload.get("Label")
        program_raw = payload.get("ProgramArguments")
        if not isinstance(label, str):
            raise ValueError("plist missing Label")
        if not isinstance(program_raw, list):
            raise ValueError("plist missing ProgramArguments")
        program: list[Any] = program_raw  # type: ignore[assignment]

        kwargs: dict[str, Any] = {
            "label": label,
            "program": [str(a) for a in program],
            "scope": scope,
            "run_at_load": bool(payload.get("RunAtLoad", False)),
            "start_on_mount": bool(payload.get("StartOnMount", False)),
        }
        sec = payload.get("StartInterval")
        if isinstance(sec, int):
            kwargs["interval"] = timedelta(seconds=sec)
        cal = payload.get("StartCalendarInterval")
        if isinstance(cal, list):
            cal_list: list[Any] = cal  # type: ignore[assignment]
            kwargs["calendar"] = [plist.calendar_from_plist(c) for c in cal_list]
        elif isinstance(cal, dict):
            cal_dict: dict[str, Any] = cal  # type: ignore[assignment]
            kwargs["calendar"] = plist.calendar_from_plist(cal_dict)
        wp = payload.get("WatchPaths")
        if isinstance(wp, list):
            wp_list: list[Any] = wp  # type: ignore[assignment]
            kwargs["watch_paths"] = [Path(str(p)) for p in wp_list]
        qd = payload.get("QueueDirectories")
        if isinstance(qd, list):
            qd_list: list[Any] = qd  # type: ignore[assignment]
            kwargs["queue_directories"] = [Path(str(p)) for p in qd_list]
        ka = payload.get("KeepAlive")
        if ka is not None:
            kwargs["keep_alive"] = plist.keep_alive_from_plist(ka)
        env_raw = payload.get("EnvironmentVariables")
        if isinstance(env_raw, dict):
            env_dict: dict[Any, Any] = env_raw  # type: ignore[assignment]
            kwargs["env"] = {str(k): str(v) for k, v in env_dict.items()}
        wd = payload.get("WorkingDirectory")
        if isinstance(wd, str):
            kwargs["working_dir"] = Path(wd)

        # Preserve literal log paths so `launchy logs` works on plists that
        # don't follow our `<log_dir>/<label>.out.log` convention.
        stdout = payload.get("StandardOutPath")
        stderr = payload.get("StandardErrorPath")
        if isinstance(stdout, str):
            kwargs["stdout_path"] = Path(stdout)
        if isinstance(stderr, str):
            kwargs["stderr_path"] = Path(stderr)
        # Recover log_dir only when both paths follow our convention exactly.
        if isinstance(stdout, str) and isinstance(stderr, str):
            so_path = Path(stdout)
            se_path = Path(stderr)
            convention = (
                so_path.parent == se_path.parent
                and so_path.name == f"{label}.out.log"
                and se_path.name == f"{label}.err.log"
            )
            if convention:
                kwargs["log_dir"] = so_path.parent
                kwargs.pop("stdout_path", None)
                kwargs.pop("stderr_path", None)

        return cls(**kwargs)
