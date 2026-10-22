# Changelog

All notable changes to this project are documented here.  The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Completed runs no longer leak their pty file descriptor.
- The pty master is non-blocking, so a child that stops reading cannot freeze
  the interface.
- Quitting terminates the active child; stop escalates from SIGTERM to SIGKILL
  after a grace period.
- The stdin bar no longer swallows cursor/editing keys and most app shortcuts.
- Raw mode forwards escape, Ctrl-C, function keys and paste verbatim, including
  shifted punctuation and Unicode characters.
- A stop pressed while a run is still starting is honoured instead of being
  ignored.
- `--no-recursive` now applies to the interactive workspace as well as `--list`.
- Rescanning runs off the UI thread and preserves the selected script and run
  badges.
- Residual output from a previous run can no longer bleed into the next run or
  its result parser.
- A run-all sequence can be cancelled with `ctrl+shift+k`.
- Coloured carriage-return progress bars render correctly.
- TAP counts are accumulated instead of being recounted from an 8 KB window.
- A malformed or unterminated escape stream cannot grow the decoder buffer
  without bound.
- A later test summary replaces earlier counts instead of merging with them.
- Scripts under a hidden or `build`/`dist` ancestor are discovered again.
- Versioned Python shebangs (`python3.12`) are recognised as Python.
- Executable shell scripts without a shebang run through the shell.
- `append_const` and `nargs=0` are treated as flags, and Tcl `lindex $argv N`
  reveals the real positional count.
- The source pane reads at most its display limit instead of the whole file.
- Argument edits are scoped to the script they came from, and the pane updates
  in place when the interface has not changed.
- Layout changes push the new size to the running child.
- Help cannot stack on top of itself, and its text matches the implementation.
- Improved muted-text contrast, scrollbar size and checkbox accessible names.

### Added

- CI (lint, tests on Linux/macOS and Python 3.11–3.14, packaging, docs drift).
- Tag-triggered PyPI release workflow with trusted publishing.
- `tests/test_cli.py`, pytest markers, timeouts and coverage reporting.
- `CHANGELOG.md`, `CONTRIBUTING.md`, `MANIFEST.in`, `Makefile`,
  `.pre-commit-config.yaml` and Dependabot.

## [0.1.0] - 2026-10-13

### Added

- Initial release: discovery, pty execution, streamed ANSI output, stdin and
  raw mode, argument/option introspection, source pane and result summaries.
