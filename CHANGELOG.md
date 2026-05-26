# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/).

## [0.1.1]

### Fixed

- `Job.install()`, `Job.reload()`, and `Job.enable()` no longer race-fail
  with `Bootstrap failed: 5: Input/output error` when the loaded service's
  child traps SIGTERM and takes time to exit. `launchctl bootout` dispatches
  SIGTERM and returns immediately; `launchctl print` reports the service
  gone before the child actually exits; the follow-up `bootstrap` then
  collides with launchd's still-live registration. The fix captures the
  loaded child's PID before bootout and polls `os.kill(pid, 0)` until it
  exits before bootstrapping. Deterministic 10/10 failures with a slow-quit
  child are now 0/10.

### Added

- `launchctl.bootout_and_wait(target, pid, timeout=30.0)` — public helper
  encapsulating the race fix.
- `TeardownTimeoutError(LaunchyError)` — raised when the child outlives the
  deadline (distinct from `LaunchctlError`, which signals launchctl itself
  failed). Exported from `launchy`.
- `timeout` kwarg on `Job.install()`, `Job.reload()`, `Job.enable()`
  (default 30s) for callers with long graceful shutdowns.

## [0.1.0]

Initial release.

- **Library**: `Job` dataclass wrapping every common launchd trigger
  (`RunAtLoad`, `KeepAlive`, `StartInterval`, `StartCalendarInterval`,
  `WatchPaths`, `QueueDirectories`, `StartOnMount`). Native Python types
  throughout — `timedelta`, `list[Path]`, `bool`, `TypedDict` for the keyed
  ones; snake_case at the surface, mapped to launchd's PascalCase at render
  time. Lifecycle methods: `install`, `start`, `stop`, `reload`, `disable`,
  `enable`, `status`, `uninstall`, `diagnose`. Factories: `Job.load`,
  `Job.find` (scope auto-detect), `Job.list`, `Job.list_with_status`.
- **Scopes**: user agents (`~/Library/LaunchAgents`), all-user agents
  (`/Library/LaunchAgents`), system daemons (`/Library/LaunchDaemons`).
  Scope auto-detected on lookup; explicit when installing.
- **`launchy doctor`** — health check catching the silent failures launchd
  swallows: missing program paths, unwritable log dirs, interval thrash,
  jobs with no trigger, plists on disk but not loaded. Exits non-zero on
  failures so it can gate CI. Public `Diagnostic` + `Job.diagnose()` for
  library callers.
- **CLI**: `install`, `info`, `doctor`, `list` (alias `ls`), `status`,
  `start`, `stop`, `reload`, `disable`, `enable`, `uninstall` (alias `rm`),
  `logs`, `show`. `-h` and shell completion across all scopes.
- **Shorthand schedule flags**: `--every 5m` / `1h` / `30s` / `2d`,
  `--at 02:00` / `--at "Mon 09:00"`.
- **List affordances**: scope column, optional schedule column (`-s`),
  filters (`--grep`, `--running`, `--stopped`, `--failed`), `-1` for
  pipeable output, coloured loaded dot, JSON output.
- **Bulk operations**: `uninstall`, `start`, `stop`, `reload`, `disable`,
  `enable` accept `--prefix` or `--grep` instead of a single label.
  Confirmation prompt shows count + sample; `--force` skips.
- **Install feedback**: success message reports loaded status, plist path,
  and inlines doctor warnings on the freshly-installed job.
- **Exceptions**: `LaunchyError` hierarchy — `JobNotFound`, `NotInstalled`,
  `PermissionDeniedError`, `LaunchctlError` (carries returncode, stderr, argv).

[0.1.1]: https://github.com/dalberto/launchy/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/dalberto/launchy/releases/tag/v0.1.0
