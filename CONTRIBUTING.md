# Contributing

Thanks for taking the time to improve `scriptsuite`.

## Setup

```sh
python -m venv .venv
.venv/bin/pip install -e ".[dev,docs]"
```

The interactive runner uses a POSIX pseudo-terminal, so development and the
integration tests require Linux or macOS.

## Fast feedback

```sh
.venv/bin/python -m pytest -m "not integration"   # seconds
.venv/bin/ruff check src tests tools
.venv/bin/ruff format src tests tools
```

The full suite starts real child processes on real ptys and takes around two
minutes:

```sh
.venv/bin/python -m pytest
```

Run one behaviour at a time with `pytest tests/test_app.py -k prompt`.

## Pull requests

- Keep changes focused and add a regression test for a bug fix.
- Update `README.md` when behaviour or keybindings change.
- Regenerate the docs when the README changes:

  ```sh
  .venv/bin/python tools/screenshot.py
  .venv/bin/python tools/build_docs.py
  ```

- Add an entry under `## [Unreleased]` in `CHANGELOG.md` for user-visible
  changes.

## Release

Maintainers tag `vX.Y.Z`; `.github/workflows/release.yml` builds, verifies and
publishes the package to PyPI through trusted publishing, then creates a GitHub
release.
