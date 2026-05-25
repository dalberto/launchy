# Security

## Reporting a vulnerability

Report privately via GitHub Security Advisories
(<https://github.com/dalberto/launchy/security/advisories/new>). Please don't
open a public issue for a vulnerability. Expect an initial response within a
few days.

## Trust model

launchy manages macOS launchd jobs — installing, starting, and stopping
programs that run on your Mac. Treat that scope honestly:

- `Scope.SYSTEM` (LaunchDaemons) requires root and installs jobs that run
  outside any user session. A compromised daemon plist is a full-system
  compromise. Validate `program` argv and `env` before passing them to
  `Job(scope=Scope.SYSTEM)`, especially in code that ingests untrusted input.
- launchy shells out to `launchctl`. Any string passed as `label`, `program`,
  `env` values, or `working_dir` ends up in plist XML and (for the label) in
  `launchctl` argv. launchy does not sanitize these — they are treated as
  trusted input from the caller.
- The CLI is a thin wrapper. The same trust assumptions apply to anything you
  pass on the command line.
