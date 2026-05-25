# launchy

[![CI](https://github.com/dalberto/launchy/actions/workflows/ci.yml/badge.svg)](https://github.com/dalberto/launchy/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/launchy.svg)](https://pypi.org/project/launchy/)
[![Python](https://img.shields.io/pypi/pyversions/launchy.svg)](https://pypi.org/project/launchy/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Schedule things on macOS without writing plists or memorizing `launchctl` verbs. Python library and CLI for launchd.

```python
from pathlib import Path
from launchy import Job

Job(
    label="com.launchy.backup.daily",
    program=["/usr/bin/python3", str(Path.home() / "backup.py")],
    calendar={"hour": 2, "minute": 0},
    keep_alive={"successful_exit": False},
    log_dir=Path.home() / "Library/Logs/backup",
).install()
```

```sh
launchy install com.launchy.backup.daily \
    --program /usr/bin/python3 --program ~/backup.py \
    --at 02:00 --keep-alive
launchy info com.launchy.backup.daily      # config + state
launchy doctor                              # health-check everything
launchy ls --failed                         # only jobs with non-zero last exit
```

## Install

```sh
# library + CLI on PATH
uv tool install 'launchy[cli]'

# library only
uv add launchy
```

Requires macOS and Python 3.14+.

---

Each feature is documented as **Library** then **CLI**. Skip ahead to [Reference](#reference) for the trigger/scope tables.

## Creating jobs

A `Job` is the unit of work. Pass triggers, env, log paths; call `install()`. The install is idempotent — re-running with the same label bootouts the old version and bootstraps the new one.

**Library**

```python
from datetime import timedelta
from pathlib import Path
from launchy import Job, Scope

job = Job(
    label="com.launchy.sync.photos",
    program=["/usr/local/bin/photo-sync"],
    interval=timedelta(minutes=15),
    env={"PHOTO_DIR": "/Volumes/Photos"},
    log_dir=Path.home() / "Library/Logs/photo-sync",
    scope=Scope.USER,
)
job.install()
```

**CLI**

```sh
launchy install com.launchy.sync.photos \
    --program /usr/local/bin/photo-sync \
    --every 15m \
    --env PHOTO_DIR=/Volumes/Photos \
    --log-dir ~/Library/Logs/photo-sync
```

The install message reports loaded status, plist path, and inlines doctor warnings if the new job looks broken (missing program, unwritable log dir, no trigger set).

## Listing & finding jobs

**Library**

```python
from launchy import Job

# all jobs across user, all-users, and system scopes; pass scope= to filter
all_jobs = Job.list_with_status()                          # list[tuple[Job, JobStatus]]
mine = Job.list_with_status(prefix="com.launchy.")         # filter
one = Job.find("com.launchy.backup.daily")                 # auto-detects scope
```

`Job.list_with_status()` parallelizes the per-job `launchctl print` calls in a thread pool.

**CLI**

```sh
launchy list                        # all scopes
launchy ls --scope user             # filter (ls is an alias)
launchy ls -s                       # add a Schedule column
launchy ls -g zoom                  # case-insensitive substring on label
launchy ls --running                # loaded + has pid
launchy ls --stopped                # loaded + no pid
launchy ls --failed                 # last exit non-zero
launchy ls -1 -g zoom               # one label per line, pipeable
launchy ls --json                   # structured output for jq
```

## Inspecting one job

**Library**

```python
job = Job.find("com.launchy.backup.daily")
job.status()       # JobStatus(label, loaded, pid, last_exit_code)
job.render()       # plist XML as a string
job.diagnose()     # list[Diagnostic] — see Health checks below
```

**CLI**

```sh
launchy info <label>     # config + state on one screen
launchy status <label>   # just loaded/pid/exit (--json for structured)
launchy show <label>     # rendered plist XML
```

## Health checks

`doctor` catches the silent failures launchd swallows: missing program paths, unwritable log dirs, intervals so short they pin the CPU, working dirs that don't exist, jobs with no trigger that will never run, plists on disk that aren't loaded. Exit code is non-zero on any failure — wire it into CI if you ship plists from a repo.

**Library**

```python
from launchy import Job, Diagnostic

for d in Job.find("com.launchy.backup.daily").diagnose():
    print(d.severity, d.check, d.detail)
```

`Diagnostic.severity` is `"ok"`, `"warn"`, or `"fail"`.

**CLI**

```sh
launchy doctor <label>      # check one job
launchy doctor              # sweep everything
launchy doctor -v           # include passing checks too
```

## Lifecycle

**Library**

```python
job.start()     # kickstart -k
job.stop()      # SIGTERM
job.reload()    # bootout + bootstrap (after editing the plist on disk)
job.disable()   # mark disabled in launchd, then unload. Survives reboots.
job.enable()    # remove disabled flag and bootstrap.
```

`disable`/`enable` are distinct from `stop`/`start`: they write to launchd's persistent store and survive reboots. Use them to pause a job for a week without uninstalling.

**CLI**

```sh
launchy start <label>
launchy stop <label>
launchy reload <label>
launchy disable <label>
launchy enable <label>
```

### Bulk operations

`uninstall`, `start`, `stop`, `reload`, `disable`, `enable` all accept `--prefix` or `--grep` instead of a single label. A confirmation prompt shows the count and a sample; `--force`/`-f` skips it.

**Library** — no separate API; just iterate:

```python
for job in Job.list(prefix="com.launchy.test."):
    job.uninstall()
```

**CLI**

```sh
launchy rm --grep test --force                  # bulk remove (rm is alias for uninstall)
launchy stop --prefix com.launchy.batch.        # bulk stop
launchy disable --grep zoom                     # interactive confirm
```

## Logs

**Library** — paths exposed as properties:

```python
job.resolved_stdout_path    # Path or None — explicit override, else log_dir/<label>.out.log
job.resolved_stderr_path
```

**CLI**

```sh
launchy logs <label>          # tail (last 10 lines)
launchy logs <label> -f       # follow (kqueue-backed via `tail -f`)
launchy logs <label> --err    # stderr instead of stdout
```

## Removing jobs

**Library**

```python
Job.find("com.launchy.x").uninstall()
```

**CLI**

```sh
launchy uninstall <label>           # confirmation prompt
launchy rm <label> --force          # skip prompt
launchy rm --grep test --force      # bulk
```

## Shell completion

`launchy --install-completion` writes a completion script for your shell. Tab-completes installed labels across all scopes.

## Reference

### Triggers

| launchd key             | Python type                          | Example                          |
|-------------------------|--------------------------------------|----------------------------------|
| `RunAtLoad`             | `bool`                               | `run_at_load=True`               |
| `StartOnMount`          | `bool`                               | `start_on_mount=True`            |
| `StartInterval`         | `datetime.timedelta`                 | `interval=timedelta(minutes=5)`  |
| `StartCalendarInterval` | `CalendarSpec \| list[CalendarSpec]` | `calendar={"hour": 2}`           |
| `WatchPaths`            | `list[Path]`                         | `watch_paths=[Path("~/Drop")]`   |
| `QueueDirectories`      | `list[Path]`                         | `queue_directories=[...]`        |
| `KeepAlive`             | `bool \| KeepAliveConditions`        | `keep_alive=True`                |

`CalendarSpec` and `KeepAliveConditions` are `TypedDict`s with snake_case keys (`hour`, `minute`, `weekday`, `successful_exit`, etc). launchy maps them to launchd's PascalCase at render time.

CLI shorthand for the common cases:

- `--every 5m` / `1h` / `30s` / `2d` → `StartInterval`
- `--at "02:00"` / `--at "Mon 09:00"` → `StartCalendarInterval`
- `--run-at-load`, `--start-on-mount`, `--keep-alive`, `--watch PATH`, `--queue-dir PATH`

### Scopes

| Scope             | Plist location                | Notes                                   |
|-------------------|-------------------------------|-----------------------------------------|
| `Scope.USER`      | `~/Library/LaunchAgents`      | Default. No privileges required.        |
| `Scope.ALL_USERS` | `/Library/LaunchAgents`       | Requires root to install.               |
| `Scope.SYSTEM`    | `/Library/LaunchDaemons`      | Runs without a user session. Root only. |

`Job.find(label)` and most CLI commands search all three scopes by default; pass `scope=` or `--scope` to filter. `install` defaults to `Scope.USER` since it has to pick a destination.

### Exceptions

```python
from launchy import LaunchyError, JobNotFound, NotInstalled, PermissionDeniedError, LaunchctlError
```

- `JobNotFound` — `load`/`find` couldn't locate the label.
- `NotInstalled` — lifecycle op on a job whose plist isn't on disk.
- `PermissionDeniedError` — `Scope.ALL_USERS` or `Scope.SYSTEM` op without root.
- `LaunchctlError` — any `launchctl` non-zero exit; carries `returncode`, `stderr`, `argv`.

### Gotchas

- launchd never catches up on missed runs. If your Mac was asleep when a `StartCalendarInterval` should have fired, it runs once on wake — not for every missed slot.
- `StartInterval` is measured from load time, not wall clock. "Every hour" doesn't necessarily land on `:00`.
- After editing a plist on disk you must `reload()` — launchd caches the loaded version.
- `log_dir` must exist before launchd starts the job. launchy creates it on `install()`. If you point at a network volume that isn't mounted at load time, launchd silently refuses to write logs.

## License

MIT.
