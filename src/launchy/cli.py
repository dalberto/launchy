"""Typer CLI. The ONLY module that imports typer/rich.

Commands construct a Job (or Job.load), invoke a lifecycle method, catch
LaunchyError subclasses, and map them to exit codes + rich output. No
business logic here — it all lives in `job.py` and friends.
"""

from __future__ import annotations

import os
import sys
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import Annotated

import click
import typer
import typer.core
from rich.console import Console
from rich.table import Table

from . import __version__, paths
from .diagnostics import Diagnostic, Severity, diagnose
from .exceptions import JobNotFound, LaunchctlError, LaunchyError
from .job import Job
from .paths import Scope
from .plist import CalendarSpec
from .status import JobStatus


class _AliasGroup(typer.core.TyperGroup):
    """Resolve aliases at command-lookup time so --help stays uncluttered."""

    _aliases = {"ls": "list", "rm": "uninstall"}

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        return super().get_command(ctx, self._aliases.get(cmd_name, cmd_name))


app = typer.Typer(
    cls=_AliasGroup,
    no_args_is_help=True,
    add_completion=True,  # provides --install-completion / --show-completion
    context_settings={"help_option_names": ["-h", "--help"]},
)
console = Console()
err_console = Console(stderr=True)

EXIT_OK = 0
EXIT_USER_ERROR = 1
EXIT_LAUNCHCTL_ERROR = 2


# ---- typer Option aliases (module-level so pyright can resolve them) --------


def _fuzzy_score(query: str, candidate: str) -> float | None:
    """fzf-style subsequence match. Returns None if `query` isn't a
    subsequence of `candidate` (case-insensitive). Higher score = better.

    Scoring favours: early position, contiguous runs, word boundaries
    (after `.`/`_`/`-` or at index 0), and shorter candidates on ties.
    """
    if not query:
        return 0.0
    q = query.lower()
    c = candidate.lower()
    positions: list[int] = []
    cursor = 0
    for qc in q:
        idx = c.find(qc, cursor)
        if idx == -1:
            return None
        positions.append(idx)
        cursor = idx + 1

    score = -positions[0] * 2.0  # earlier first match wins
    for i, p in enumerate(positions):
        if i > 0 and p == positions[i - 1] + 1:
            score += 15  # consecutive match
        if p == 0 or candidate[p - 1] in "._-":
            score += 25  # word boundary (heavily favoured — fzf-style)
    score -= len(candidate) * 0.1  # shorter is better on ties
    return score


def _complete_label(incomplete: str) -> list[str]:
    """Tab-complete labels with fzf-style fuzzy matching, ranked.

    Globs filenames — doesn't parse plists — so it stays under the few-ms
    budget shell completion expects. Dedupes labels appearing in multiple
    scopes; returns highest-scoring candidates first.
    """
    candidates: list[tuple[float, str]] = []
    seen: set[str] = set()
    for scope in Scope:
        try:
            directory = paths.plist_dir(scope)
            if not directory.is_dir():
                continue
            for p in directory.glob("*.plist"):
                if p.stem in seen:
                    continue
                seen.add(p.stem)
                score = _fuzzy_score(incomplete, p.stem)
                if score is not None:
                    candidates.append((score, p.stem))
        except OSError:
            continue
    candidates.sort(key=lambda sc: (-sc[0], sc[1]))
    return [label for _s, label in candidates]


LabelArg = Annotated[str, typer.Argument(help="launchd job label (reverse-DNS).")]
InstalledLabelArg = Annotated[
    str,
    typer.Argument(
        help="Label of an installed launchd job.", autocompletion=_complete_label
    ),
]
OptionalInstalledLabelArg = Annotated[
    str | None,
    typer.Argument(
        help="Label of an installed launchd job. Omit if using --prefix/--grep.",
        autocompletion=_complete_label,
    ),
]
ScopeOpt = Annotated[
    Scope,
    typer.Option(
        "--scope",
        case_sensitive=False,
        help="user → ~/Library/LaunchAgents, all-users → /Library (root), system → daemon (root).",
    ),
]
# For `list`, omitting --scope means "all scopes" rather than "user".
OptionalScopeOpt = Annotated[
    Scope | None,
    typer.Option(
        "--scope",
        case_sensitive=False,
        help="Filter to one scope. Omit to show all scopes.",
    ),
]
JsonOpt = Annotated[bool, typer.Option("--json", help="Emit JSON instead of human output.")]
ForceOpt = Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation prompts.")]
FollowOpt = Annotated[bool, typer.Option("--follow", "-f", help="Tail and follow the log.")]
ErrOpt = Annotated[bool, typer.Option("--err", help="Tail stderr log instead of stdout.")]
PrefixOpt = Annotated[
    str, typer.Option("--prefix", help="Only labels starting with this prefix.")
]

ProgramOpt = Annotated[
    list[str],
    typer.Option("--program", "-p", help="Program argv. Repeat the flag for each arg."),
]
RunAtLoadOpt = Annotated[bool, typer.Option("--run-at-load", help="Fire once when loaded.")]
StartOnMountOpt = Annotated[
    bool, typer.Option("--start-on-mount", help="Fire when a filesystem mounts.")
]
IntervalOpt = Annotated[
    int | None,
    typer.Option("--interval", help="Repeat every N seconds (StartInterval). Single value."),
]
EveryOpt = Annotated[
    str | None,
    typer.Option(
        "--every",
        help="Shorthand for --interval. Single value. Units: s/m/h/d. e.g. 30s, 5m, 1h, 2d.",
    ),
]
CalendarOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--calendar",
        help="cron-ish: 'hour=2,minute=0'. Repeat for multiple fire times.",
    ),
]
AtOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--at",
        help="Shorthand for --calendar. e.g. '02:00', 'Mon 09:00'. Repeatable.",
    ),
]
WatchOpt = Annotated[
    list[Path] | None,
    typer.Option("--watch", help="Fire when this path changes. Repeatable."),
]
QueueDirOpt = Annotated[
    list[Path] | None,
    typer.Option("--queue-dir", help="Fire when this dir is non-empty. Repeatable."),
]
KeepAliveOpt = Annotated[
    bool, typer.Option("--keep-alive", help="Always restart on exit.")
]
EnvOpt = Annotated[
    list[str] | None, typer.Option("--env", "-e", help="KEY=VAL. Repeatable.")
]
LogDirOpt = Annotated[Path | None, typer.Option("--log-dir")]
WorkingDirOpt = Annotated[Path | None, typer.Option("--working-dir")]


# ---- helpers ----------------------------------------------------------------


def _parse_calendar(spec: str) -> CalendarSpec:
    """Parse 'hour=2,minute=0' into a CalendarSpec."""
    out: CalendarSpec = {}
    for raw in spec.split(","):
        piece = raw.strip()
        if not piece:
            continue
        if "=" not in piece:
            raise typer.BadParameter(f"calendar piece must be key=value, got {piece!r}")
        key, val = piece.split("=", 1)
        key = key.strip()
        try:
            num = int(val.strip())
        except ValueError as e:
            raise typer.BadParameter(f"calendar value must be int: {piece!r}") from e
        if key not in ("minute", "hour", "day", "weekday", "month"):
            raise typer.BadParameter(f"unknown calendar key: {key!r}")
        out[key] = num  # type: ignore[literal-required]
    if not out:
        raise typer.BadParameter("calendar must have at least one key=value")
    return out


def _parse_env(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise typer.BadParameter(f"--env must be KEY=VAL, got {item!r}")
        k, v = item.split("=", 1)
        out[k] = v
    return out


_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_every(spec: str) -> int:
    """Parse '5m'/'1h'/'30s'/'2d' into seconds for StartInterval."""
    s = spec.strip().lower()
    if not s:
        raise typer.BadParameter("--every must not be empty")
    unit = s[-1]
    if unit not in _DURATION_UNITS:
        raise typer.BadParameter(
            f"--every must end with s/m/h/d, got {spec!r}"
        )
    try:
        n = int(s[:-1])
    except ValueError as e:
        raise typer.BadParameter(f"--every value must be an integer: {spec!r}") from e
    if n <= 0:
        raise typer.BadParameter(f"--every must be positive: {spec!r}")
    return n * _DURATION_UNITS[unit]


_WEEKDAYS = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}


def _parse_at(spec: str) -> CalendarSpec:
    """Parse '02:00' / 'Mon 09:00' / 'sun 23:30' into a CalendarSpec.

    Day prefix is optional. Missing day = fire every day at that time.
    """
    parts = spec.strip().split()
    if len(parts) == 1:
        day_str: str | None = None
        time_str = parts[0]
    elif len(parts) == 2:
        day_str, time_str = parts
    else:
        raise typer.BadParameter(f"--at must be 'HH:MM' or 'DAY HH:MM', got {spec!r}")
    if ":" not in time_str:
        raise typer.BadParameter(f"--at time must be HH:MM, got {time_str!r}")
    h_str, m_str = time_str.split(":", 1)
    try:
        hour = int(h_str)
        minute = int(m_str)
    except ValueError as e:
        raise typer.BadParameter(f"--at HH:MM must be integers: {time_str!r}") from e
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise typer.BadParameter(f"--at out of range: {time_str!r}")
    out: CalendarSpec = {"hour": hour, "minute": minute}
    if day_str is not None:
        weekday = _WEEKDAYS.get(day_str.lower()[:3])
        if weekday is None:
            raise typer.BadParameter(
                f"--at day must be Sun/Mon/Tue/Wed/Thu/Fri/Sat, got {day_str!r}"
            )
        out["weekday"] = weekday
    return out


def _handle(exc: LaunchyError) -> typer.Exit:
    """Print the error and return a typer.Exit with an appropriate code."""
    err_console.print(f"[red]{exc}[/red]")
    code = EXIT_LAUNCHCTL_ERROR if isinstance(exc, LaunchctlError) else EXIT_USER_ERROR
    return typer.Exit(code)


def _find(label: str, scope: Scope | None) -> Job:
    """Wrap `Job.find` so the CLI passes a scope-list when scope is set."""
    return Job.find(label, scopes=[scope] if scope is not None else None)


def _format_duration(td: timedelta) -> str:
    seconds = int(td.total_seconds())
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _format_calendar(cal: CalendarSpec | list[CalendarSpec]) -> str:
    if isinstance(cal, list):
        return f"{len(cal)} fire times"
    return ",".join(f"{k}={v}" for k, v in cal.items())


def _path_marker(p: Path, *, executable: bool = False) -> str:
    """Coloured ✓/✗ indicator for a path's existence + (optional) executable bit."""
    if not p.exists():
        return "[red]✗ missing[/red]"
    if executable and not os.access(p, os.X_OK):
        return "[yellow]⚠ not executable[/yellow]"
    return "[green]✓[/green]"


def _human_size(n: int) -> str:
    """Format byte count as B / KB / MB / GB."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n //= 1024
    return f"{n} GB"


def _file_size_marker(p: Path | None) -> str:
    if p is None:
        return "[dim]-[/dim]"
    try:
        return _human_size(p.stat().st_size)
    except OSError:
        return "[red]missing[/red]"


_SEVERITY_MARKERS: dict[Severity, str] = {
    "ok": "[green]✓[/green]",
    "warn": "[yellow]⚠[/yellow]",
    "fail": "[red]✗[/red]",
}


def _format_schedule(job: Job) -> str:
    """Compact one-line summary of a Job's triggers."""
    parts: list[str] = []
    if job.run_at_load:
        parts.append("at load")
    if job.start_on_mount:
        parts.append("on mount")
    if job.interval is not None:
        parts.append(f"every {_format_duration(job.interval)}")
    if job.calendar is not None:
        parts.append(f"@ {_format_calendar(job.calendar)}")
    if job.watch_paths:
        n = len(job.watch_paths)
        parts.append(f"watch {n} path{'s' if n != 1 else ''}")
    if job.queue_directories:
        n = len(job.queue_directories)
        parts.append(f"queue {n} dir{'s' if n != 1 else ''}")
    if job.keep_alive is True:
        parts.append("keep alive")
    elif isinstance(job.keep_alive, dict):
        conds = ",".join(job.keep_alive.keys())
        parts.append(f"keep alive ({conds})")
    return ", ".join(parts) if parts else "-"


# ---- commands ---------------------------------------------------------------


@app.command()
def install(
    label: LabelArg,
    program: ProgramOpt,
    run_at_load: RunAtLoadOpt = False,
    start_on_mount: StartOnMountOpt = False,
    interval: IntervalOpt = None,
    every: EveryOpt = None,
    calendar: CalendarOpt = None,
    at: AtOpt = None,
    watch: WatchOpt = None,
    queue_dir: QueueDirOpt = None,
    keep_alive: KeepAliveOpt = False,
    env: EnvOpt = None,
    log_dir: LogDirOpt = None,
    working_dir: WorkingDirOpt = None,
    scope: ScopeOpt = Scope.USER,
) -> None:
    """Render a plist, write it, and bootstrap into launchd."""
    # Resolve interval (--interval and --every are mutually exclusive)
    if interval is not None and every is not None:
        raise typer.BadParameter("--interval and --every are mutually exclusive")
    resolved_interval = interval if interval is not None else (
        _parse_every(every) if every is not None else None
    )

    # Resolve calendar (--calendar and --at are mutually exclusive; --every conflicts too)
    if calendar and at:
        raise typer.BadParameter("--calendar and --at are mutually exclusive")
    if (calendar or at) and resolved_interval is not None:
        raise typer.BadParameter("interval and calendar triggers are mutually exclusive")
    calendars = (
        [_parse_calendar(c) for c in calendar]
        if calendar
        else [_parse_at(a) for a in at]
        if at
        else None
    )
    cal: CalendarSpec | list[CalendarSpec] | None
    if calendars is None:
        cal = None
    elif len(calendars) == 1:
        cal = calendars[0]
    else:
        cal = calendars

    job = Job(
        label=label,
        program=program,
        scope=scope,
        run_at_load=run_at_load,
        start_on_mount=start_on_mount,
        interval=timedelta(seconds=resolved_interval) if resolved_interval else None,
        calendar=cal,
        watch_paths=list(watch) if watch else [],
        queue_directories=list(queue_dir) if queue_dir else [],
        keep_alive=keep_alive if keep_alive else None,
        env=_parse_env(env or []),
        log_dir=log_dir,
        working_dir=working_dir,
    )
    try:
        path = job.install()
    except LaunchyError as exc:
        raise _handle(exc) from exc

    js = job.status()
    dot = "[green]●[/green]" if js.loaded else "[red]●[/red]"
    state = "loaded" if js.loaded else "not loaded"
    console.print(f"[green]installed[/green] {label} ({job.scope.value}) → {dot} {state}")
    console.print(f"  Plist: {path}")
    if job.resolved_stdout_path:
        console.print(f"  Logs:  {job.resolved_stdout_path}")
    # Inline doctor warnings — surface footguns at install time, not later.
    # Reuse the just-fetched status; don't re-shell to launchctl.
    issues = [d for d in diagnose(job, js) if d.severity != "ok"]
    if issues:
        console.print()
        for d in issues:
            console.print(
                f"  {_SEVERITY_MARKERS[d.severity]} {d.check}: {d.detail}"
            )


def _select_targets(
    label: str | None, *, prefix: str, grep: str, scope: Scope | None
) -> list[Job]:
    """Resolve a single label or a --prefix/--grep filter to a list of Jobs."""
    if label is not None:
        if prefix or grep:
            raise typer.BadParameter("pass either a label or --prefix/--grep, not both")
        return [_find(label, scope)]
    if not prefix and not grep:
        raise typer.BadParameter("specify a label, --prefix, or --grep")
    pairs = Job.list_with_status(prefix=prefix, scope=scope)
    needle = grep.lower()
    return [j for j, _s in pairs if not needle or needle in j.label.lower()]


def _confirm(targets: list[Job], verb: str, force: bool) -> None:
    if force or not targets:
        return
    if len(targets) == 1:
        t = targets[0]
        typer.confirm(f"{verb} {t.label} ({t.scope.value})?", abort=True)
        return
    sample = ", ".join(t.label for t in targets[:3])
    extra = f" + {len(targets) - 3} more" if len(targets) > 3 else ""
    typer.confirm(f"{verb} {len(targets)} jobs: {sample}{extra}?", abort=True)


_PAST_TENSE = {
    "uninstall": "uninstalled",
    "start": "started",
    "stop": "stopped",
    "reload": "reloaded",
    "disable": "disabled",
    "enable": "enabled",
}


def _bulk(
    label: str | None,
    scope: Scope | None,
    prefix: str,
    grep: str,
    force: bool,
    verb: str,
    action: str,
) -> None:
    """Resolve targets, confirm, run `action` (a method name) on each."""
    try:
        targets = _select_targets(label, prefix=prefix, grep=grep, scope=scope)
    except LaunchyError as exc:
        raise _handle(exc) from exc
    if not targets:
        console.print("[yellow]no jobs matched[/yellow]")
        return
    _confirm(targets, verb, force)
    past = _PAST_TENSE.get(action, action)
    for job in targets:
        try:
            getattr(job, action)()
        except LaunchyError as exc:
            raise _handle(exc) from exc
        console.print(f"[green]{past}[/green] {job.label} ({job.scope.value})")


@app.command()
def uninstall(
    label: OptionalInstalledLabelArg = None,
    prefix: PrefixOpt = "",
    grep: GrepOpt = "",
    scope: OptionalScopeOpt = None,
    force: ForceOpt = False,
) -> None:
    """Bootout + remove plists. Single label or bulk via --prefix/--grep."""
    # Preserve the "uninstall <unknown> --force is a noop" UX for single-label calls
    if label is not None and force and not prefix and not grep:
        try:
            job = _find(label, scope)
        except JobNotFound:
            console.print(f"[yellow]no plist for {label}[/yellow]")
            return
        try:
            job.uninstall()
        except LaunchyError as exc:
            raise _handle(exc) from exc
        console.print(f"[green]uninstalled[/green] {job.label} ({job.scope.value})")
        return
    _bulk(label, scope, prefix, grep, force, "Uninstall", "uninstall")


@app.command()
def start(
    label: OptionalInstalledLabelArg = None,
    prefix: PrefixOpt = "",
    grep: GrepOpt = "",
    scope: OptionalScopeOpt = None,
    force: ForceOpt = False,
) -> None:
    """Force jobs to run now."""
    _bulk(label, scope, prefix, grep, force, "Start", "start")


@app.command()
def stop(
    label: OptionalInstalledLabelArg = None,
    prefix: PrefixOpt = "",
    grep: GrepOpt = "",
    scope: OptionalScopeOpt = None,
    force: ForceOpt = False,
) -> None:
    """Send SIGTERM to running jobs."""
    _bulk(label, scope, prefix, grep, force, "Stop", "stop")


@app.command()
def reload(
    label: OptionalInstalledLabelArg = None,
    prefix: PrefixOpt = "",
    grep: GrepOpt = "",
    scope: OptionalScopeOpt = None,
    force: ForceOpt = False,
) -> None:
    """Bootout + bootstrap. Use after editing the plist on disk."""
    _bulk(label, scope, prefix, grep, force, "Reload", "reload")


@app.command()
def disable(
    label: OptionalInstalledLabelArg = None,
    prefix: PrefixOpt = "",
    grep: GrepOpt = "",
    scope: OptionalScopeOpt = None,
    force: ForceOpt = False,
) -> None:
    """Mark disabled in launchd's persistent store and unload. Survives reboots."""
    _bulk(label, scope, prefix, grep, force, "Disable", "disable")


@app.command()
def enable(
    label: OptionalInstalledLabelArg = None,
    prefix: PrefixOpt = "",
    grep: GrepOpt = "",
    scope: OptionalScopeOpt = None,
    force: ForceOpt = False,
) -> None:
    """Remove the disabled flag and bootstrap the job."""
    _bulk(label, scope, prefix, grep, force, "Enable", "enable")


@app.command()
def info(label: InstalledLabelArg, scope: OptionalScopeOpt = None) -> None:
    """Human-readable single-job summary: config + current state."""
    try:
        job = _find(label, scope)
    except LaunchyError as exc:
        raise _handle(exc) from exc
    js = job.status()

    console.print(f"[bold]{job.label}[/bold]")
    console.print(f"  Scope:       {job.scope.value}")

    program_str = " ".join(job.program)
    prog_path = Path(job.program[0])
    console.print(f"  Program:     {program_str} {_path_marker(prog_path, executable=True)}")

    if job.working_dir is not None:
        console.print(f"  Working dir: {job.working_dir} {_path_marker(job.working_dir)}")

    console.print(f"  Schedule:    {_format_schedule(job)}")

    if job.env:
        console.print("  Env:")
        for k, v in job.env.items():
            console.print(f"    {k}={v}")

    if job.resolved_stdout_path or job.resolved_stderr_path:
        console.print("  Logs:")
        if job.resolved_stdout_path:
            console.print(
                f"    stdout: {job.resolved_stdout_path} "
                f"({_file_size_marker(job.resolved_stdout_path)})"
            )
        if job.resolved_stderr_path:
            console.print(
                f"    stderr: {job.resolved_stderr_path} "
                f"({_file_size_marker(job.resolved_stderr_path)})"
            )

    dot = "[green]●[/green]" if js.loaded else "[red]●[/red]"
    state = "loaded" if js.loaded else "not loaded"
    pid_str = str(js.pid) if js.pid is not None else "-"
    exit_str = str(js.last_exit_code) if js.last_exit_code is not None else "-"
    console.print(
        f"  Status:      {dot} {state}, pid={pid_str}, last_exit={exit_str}"
    )

    console.print(f"  Plist:       {job.plist_path}")


# ---- doctor -----------------------------------------------------------------


VerboseOpt = Annotated[
    bool,
    typer.Option("--verbose", "-v", help="Show passing checks too (default: only warn/fail)."),
]
DoctorLabelArg = Annotated[
    str | None,
    typer.Argument(
        help="Label to check. Omit to sweep all jobs.", autocompletion=_complete_label
    ),
]


def _render_doctor(pairs: list[tuple[Job, list[Diagnostic]]], *, verbose: bool) -> int:
    """Render diagnostic results. Returns exit code (0 ok, 1 fail)."""
    any_fail = False
    any_issue = False
    table = Table()
    table.add_column("")
    table.add_column("Label")
    table.add_column("Check")
    table.add_column("Detail")
    for job, results in pairs:
        rows = [d for d in results if verbose or d.severity != "ok"]
        for d in rows:
            any_issue = True
            if d.severity == "fail":
                any_fail = True
            table.add_row(_SEVERITY_MARKERS[d.severity], job.label, d.check, d.detail)
    if not any_issue:
        console.print("[green]all clear[/green]")
    else:
        console.print(table)
    return 1 if any_fail else 0


@app.command()
def doctor(
    label: DoctorLabelArg = None,
    scope: OptionalScopeOpt = None,
    verbose: VerboseOpt = False,
) -> None:
    """Health check: catches the silent-failure footguns launchd swallows."""
    if label is not None:
        try:
            job = Job.find(label, scopes=[scope] if scope else None)
        except LaunchyError as exc:
            raise _handle(exc) from exc
        results: list[tuple[Job, list[Diagnostic]]] = [(job, job.diagnose())]
    else:
        try:
            jobs_and_status = Job.list_with_status(scope=scope)
        except LaunchyError as exc:
            raise _handle(exc) from exc
        results = [(j, diagnose(j, s)) for j, s in jobs_and_status]
    code = _render_doctor(results, verbose=verbose)
    if code != 0:
        raise typer.Exit(code)


@app.command()
def status(
    label: InstalledLabelArg, scope: OptionalScopeOpt = None, as_json: JsonOpt = False
) -> None:
    """Query a job's loaded/pid/exit state."""
    try:
        job = _find(label, scope)
    except LaunchyError as exc:
        raise _handle(exc) from exc
    js = job.status()
    if as_json:
        payload = {"scope": job.scope.value, **asdict(js)}
        console.print_json(data=payload)
        return
    dot = "[green]●[/green]" if js.loaded else "[red]●[/red]"
    state = "loaded" if js.loaded else "not loaded"
    console.print(
        f"{dot} {js.label} ({job.scope.value}): {state}, pid={js.pid}, "
        f"last_exit={js.last_exit_code}"
    )


ScheduleOpt = Annotated[
    bool, typer.Option("--schedule", "-s", help="Add a Schedule column summarizing triggers.")
]
GrepOpt = Annotated[
    str, typer.Option("--grep", "-g", help="Case-insensitive substring match on label.")
]
RunningOpt = Annotated[
    bool, typer.Option("--running", help="Only loaded jobs with a live pid.")
]
StoppedOpt = Annotated[
    bool, typer.Option("--stopped", help="Only loaded jobs without a live pid.")
]
FailedOpt = Annotated[
    bool, typer.Option("--failed", help="Only jobs whose last exit was non-zero.")
]
OneLineOpt = Annotated[
    bool,
    typer.Option(
        "-1",
        "--one-line",
        help="One label per line, no decoration. For pipelines.",
    ),
]


def _loaded_cell(loaded: bool) -> str:
    return "[green]●[/green] yes" if loaded else "[red]●[/red] no"


def _filter_pairs(
    pairs: list[tuple[Job, JobStatus]],
    *,
    grep: str,
    running: bool,
    stopped: bool,
    failed: bool,
) -> list[tuple[Job, JobStatus]]:
    needle = grep.lower()
    out: list[tuple[Job, JobStatus]] = []
    for job, st in pairs:
        if needle and needle not in job.label.lower():
            continue
        if running and not (st.loaded and st.pid is not None):
            continue
        if stopped and not (st.loaded and st.pid is None):
            continue
        if failed and (st.last_exit_code is None or st.last_exit_code == 0):
            continue
        out.append((job, st))
    return out


@app.command(name="list")
def list_(
    prefix: PrefixOpt = "",
    scope: OptionalScopeOpt = None,
    as_json: JsonOpt = False,
    schedule: ScheduleOpt = False,
    grep: GrepOpt = "",
    running: RunningOpt = False,
    stopped: StoppedOpt = False,
    failed: FailedOpt = False,
    one_line: OneLineOpt = False,
) -> None:
    """List installed jobs. Defaults to all scopes; pass --scope to filter."""
    try:
        pairs = Job.list_with_status(prefix=prefix, scope=scope)
    except LaunchyError as exc:
        raise _handle(exc) from exc
    pairs = _filter_pairs(pairs, grep=grep, running=running, stopped=stopped, failed=failed)
    if one_line:
        for job, _st in pairs:
            print(job.label)
        return
    if as_json:
        payload = [
            {
                "label": j.label,
                "scope": j.scope.value,
                "schedule": _format_schedule(j),
                "status": asdict(s),
            }
            for j, s in pairs
        ]
        console.print_json(data=payload)
        return
    if not pairs:
        console.print("[yellow]no jobs[/yellow]")
        return
    table = Table()
    table.add_column("Label")
    table.add_column("Scope")
    table.add_column("Loaded")
    table.add_column("PID")
    table.add_column("Last Exit")
    if schedule:
        table.add_column("Schedule")
    for job, st in pairs:
        row = [
            st.label,
            job.scope.value,
            _loaded_cell(st.loaded),
            str(st.pid) if st.pid is not None else "-",
            str(st.last_exit_code) if st.last_exit_code is not None else "-",
        ]
        if schedule:
            row.append(_format_schedule(job))
        table.add_row(*row)
    console.print(table)


@app.command()
def logs(
    label: InstalledLabelArg,
    scope: OptionalScopeOpt = None,
    follow: FollowOpt = False,
    err: ErrOpt = False,
) -> None:
    """Tail the job's stdout or stderr log file."""
    try:
        job = _find(label, scope)
    except LaunchyError as exc:
        raise _handle(exc) from exc
    target = job.resolved_stderr_path if err else job.resolved_stdout_path
    if target is None:
        which = "StandardErrorPath" if err else "StandardOutPath"
        err_console.print(f"[red]{label}: no {which} set[/red]")
        raise typer.Exit(EXIT_USER_ERROR)
    if not target.exists():
        err_console.print(f"[yellow]log file does not exist yet: {target}[/yellow]")
        raise typer.Exit(EXIT_USER_ERROR)
    argv = ["tail"]
    if follow:
        argv.append("-f")
    argv.append(str(target))
    os.execvp(argv[0], argv)


@app.command()
def show(label: InstalledLabelArg, scope: OptionalScopeOpt = None) -> None:
    """Print the rendered plist XML."""
    try:
        job = _find(label, scope)
    except LaunchyError as exc:
        raise _handle(exc) from exc
    sys.stdout.write(job.render())


def _version_callback(value: bool) -> None:
    if value:
        console.print(__version__)
        raise typer.Exit()


VersionOpt = Annotated[
    bool,
    typer.Option(
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
]


@app.callback()
def main(_version: VersionOpt = False) -> None:
    """launchy: pythonic wrapper for macOS launchd."""
