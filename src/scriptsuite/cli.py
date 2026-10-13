"""Command line entry point."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .discovery import discover, known_extensions, resolve_scripts_dir

DESCRIPTION = """\
Discover, run and inspect test scripts in a four-pane terminal workspace.
"""

EPILOG = """\
Without arguments, scriptsuite looks for ./script (then ./scripts).
Set SCRIPTSUITE_SCRIPTS_DIR or pass --scripts-dir to override.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scriptsuite",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "directory",
        nargs="?",
        metavar="DIR",
        help="scripts folder (default: ./script, then ./scripts)",
    )
    parser.add_argument(
        "-s",
        "--scripts-dir",
        dest="scripts_dir",
        metavar="PATH",
        help="same as DIR, but as an option",
    )
    parser.add_argument(
        "-p",
        "--python",
        metavar="EXE",
        help="interpreter used for Python scripts (default: this one)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="print the discovered scripts and exit",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="only look at the top level of the scripts folder",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"scriptsuite {__version__}",
    )
    return parser


def _selected_dir(args: argparse.Namespace) -> Path:
    return resolve_scripts_dir(args.scripts_dir or args.directory)


def _print_list(scripts_dir: Path, python: str | None, recursive: bool) -> int:
    if not scripts_dir.is_dir():
        print(f"scriptsuite: no such folder: {scripts_dir}", file=sys.stderr)
        return 2

    skipped: list[str] = []
    scripts = discover(
        scripts_dir, python=python, recursive=recursive, collect_skipped=skipped
    )
    if not scripts:
        print(f"scriptsuite: no runnable scripts in {scripts_dir}", file=sys.stderr)
        print(f"  recognised extensions: {known_extensions()}", file=sys.stderr)
        print("  or any file with a #! shebang", file=sys.stderr)
        if skipped:
            print(f"  skipped: {', '.join(skipped[:10])}", file=sys.stderr)
        return 1

    width = max(len(script.rel_path) for script in scripts)
    for script in scripts:
        marker = "test" if script.is_test else script.language
        description = script.description or ""
        line = f"{script.rel_path.ljust(width)}  {marker:<8} {description}".rstrip()
        print(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    scripts_dir = _selected_dir(args)
    python = args.python or os.environ.get("SCRIPTSUITE_PYTHON")

    if args.list:
        return _print_list(scripts_dir, python, not args.no_recursive)

    try:
        from .app import ScriptSuiteApp
    except ImportError as exc:  # pragma: no cover - guidance for bare installs
        print(
            f"scriptsuite: missing dependency ({exc}).\n"
            "Install it with:  pip install textual",
            file=sys.stderr,
        )
        return 1

    app = ScriptSuiteApp(scripts_dir=scripts_dir, python=python)
    app.run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
