"""Discovery of runnable scripts inside a scripts directory."""

from __future__ import annotations

import ast
import os
import re
import shlex
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Language handling
# ---------------------------------------------------------------------------

#: Human readable language per file extension.
LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".pyw": "python",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".fish": "shell",
    ".js": "node",
    ".mjs": "node",
    ".cjs": "node",
    ".ts": "node",
    ".rb": "ruby",
    ".pl": "perl",
    ".php": "php",
    ".lua": "lua",
    ".go": "go",
    ".rs": "rust",
    ".exp": "expect",
    ".tcl": "tcl",
}

#: Default launch command per extension.  ``{path}`` is substituted.
DEFAULT_COMMANDS: dict[str, tuple[str, ...]] = {
    ".py": (sys.executable, "{path}"),
    ".pyw": (sys.executable, "{path}"),
    ".sh": ("{path}",),
    ".bash": ("{path}",),
    ".zsh": ("zsh", "{path}"),
    ".fish": ("fish", "{path}"),
    ".js": ("node", "{path}"),
    ".mjs": ("node", "{path}"),
    ".cjs": ("node", "{path}"),
    ".ts": ("node", "--experimental-strip-types", "{path}"),
    ".rb": ("ruby", "{path}"),
    ".pl": ("perl", "{path}"),
    ".php": ("php", "{path}"),
    ".lua": ("lua", "{path}"),
    ".go": ("go", "run", "{path}"),
    ".rs": ("rust-script", "{path}"),
    ".exp": ("expect", "{path}"),
    ".tcl": ("tclsh", "{path}"),
}

#: Directory names never worth walking into.
SKIP_DIRS = frozenset(
    {
        "__pycache__",
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "build",
        "dist",
        ".idea",
        ".vscode",
    }
)

#: First lines of a file are inspected when guessing what it does.
_HEADER_LINES = 40

#: Descriptions are shown on a single sidebar row, so keep them short.
_MAX_DESCRIPTION = 68

_TEST_HINTS = ("pytest", "unittest", "nose", "bats", "tap", "testify", "junit")

_HASH_COMMENT = re.compile(r"^\s*(?:#|//|;)\s?(?P<body>.*)$")


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScriptInfo:
    """Everything the UI needs to display and launch one script."""

    name: str
    path: Path
    root: Path
    language: str
    description: str
    command: tuple[str, ...]
    is_test: bool
    executable: bool
    mtime: float
    size: int

    @property
    def rel_path(self) -> str:
        try:
            return str(self.path.relative_to(self.root))
        except ValueError:
            return str(self.path)

    @property
    def key(self) -> str:
        """Stable identity used to match results back to a script."""
        return str(self.path)

    def preview_command(self) -> str:
        """Human readable command line for the header.

        The interpreter is reduced to its basename and the script path to its
        form relative to the scripts folder, so the line stays on one row.
        """
        parts = list(self.command)
        if parts:
            parts[0] = Path(parts[0]).name
        return " ".join(parts).replace(str(self.path), self.rel_path)


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def resolve_scripts_dir(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Locate the scripts directory.

    Order of precedence: explicit argument, ``SCRIPTSUITE_SCRIPTS_DIR``,
    then the conventional ``script`` / ``scripts`` / ``test`` names relative to
    the current working directory.
    """
    if explicit:
        return Path(explicit).expanduser().resolve()

    env_value = os.environ.get("SCRIPTSUITE_SCRIPTS_DIR")
    if env_value:
        return Path(env_value).expanduser().resolve()

    cwd = Path.cwd()
    for name in ("script", "scripts"):
        candidate = cwd / name
        if candidate.is_dir():
            return candidate.resolve()

    return (cwd / "script").resolve()


def _read_header(path: Path) -> list[str]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(16 * 1024)
    except OSError:
        return []
    text = raw.decode("utf-8", errors="replace")
    if "\x00" in text:
        return []
    return text.splitlines()[:_HEADER_LINES]


def _shebang_command(lines: list[str]) -> tuple[str, ...] | None:
    if not lines or not lines[0].startswith("#!"):
        return None
    try:
        parts = shlex.split(lines[0][2:].strip())
    except ValueError:
        return None
    if not parts:
        return None

    # ``#!/usr/bin/env -S python3 -u`` — the -S splits the rest.
    if Path(parts[0]).name == "env":
        rest = parts[1:]
        if rest and rest[0] == "-S":
            rest = rest[1:]
        if not rest:
            return None
        return tuple(rest)
    return tuple(parts)


def _describe_python(source: str, lines: list[str]) -> tuple[str, bool]:
    """Return ``(description, is_test)`` for a Python script."""
    is_test = False
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return (_clip(_first_comment(lines)), is_test)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0].lower() in _TEST_HINTS:
                    is_test = True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0].lower() in _TEST_HINTS:
                is_test = True
        elif isinstance(node, ast.ClassDef):
            for base in node.bases:
                name = ""
                if isinstance(base, ast.Name):
                    name = base.id
                elif isinstance(base, ast.Attribute):
                    name = base.attr
                if name in {"TestCase", "IsolatedAsyncioTestCase", "FunctionTestCase"}:
                    is_test = True
        elif isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and node.name.startswith("test"):
            is_test = True

    docstring = ast.get_docstring(tree)
    if docstring:
        summary = next(
            (line.strip() for line in docstring.splitlines() if line.strip()), ""
        )
        return (_clip(summary), is_test)

    return (_clip(_first_comment(lines)), is_test)


def _clip(text: str) -> str:
    """Shorten a description to one sidebar row's worth of text."""
    text = " ".join(text.split())
    if len(text) <= _MAX_DESCRIPTION:
        return text
    cut = text[: _MAX_DESCRIPTION - 1]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return f"{cut.rstrip(' .,;:')}…"


def _first_comment(lines: list[str]) -> str:
    """Best-effort description from the leading comment block."""
    start = 1 if lines and lines[0].startswith("#!") else 0
    for line in lines[start:]:
        match = _HASH_COMMENT.match(line)
        if match:
            body = match.group("body").strip()
            if body:
                return body
            continue
        stripped = line.strip()
        if not stripped:
            continue
        break
    return ""


def _describe(lines: list[str], language: str, path: Path) -> tuple[str, bool]:
    if language == "python":
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            source = ""
        return _describe_python(source, lines)

    name = path.stem
    is_test = (
        name.startswith("test")
        or name.endswith("_test")
        or ".test" in name
        or any(hint in "\n".join(lines).lower() for hint in _TEST_HINTS)
    )
    return (_clip(_first_comment(lines)), is_test)


#: Interpreter used when a script has no shebang and cannot be exec'd directly.
_FALLBACK_SHELLS = {
    ".sh": "sh",
    ".bash": "bash",
    ".zsh": "zsh",
    ".fish": "fish",
}

#: Shebang interpreter -> language.  Versioned names such as ``python3.12``
#: are normalised by stripping the trailing version.
_SHEBANG_LANGUAGES = {
    "python": "python",
    "pypy": "python",
    "bash": "shell",
    "sh": "shell",
    "zsh": "shell",
    "fish": "shell",
    "dash": "shell",
    "ksh": "shell",
    "node": "node",
    "nodejs": "node",
    "ruby": "ruby",
    "perl": "perl",
    "php": "php",
    "lua": "lua",
    "tclsh": "tcl",
    "expect": "expect",
}


def _language_from_shebang(shebang: tuple[str, ...]) -> str | None:
    """The language implied by an interpreter path, if it is one we know."""
    name = Path(shebang[0]).name.lower()
    if name.startswith("python"):
        return "python"
    if name.startswith("pypy"):
        return "python"
    return _SHEBANG_LANGUAGES.get(name)


def _build_command(
    path: Path,
    language: str,
    shebang: tuple[str, ...] | None,
    python: str | None,
    executable: bool,
) -> tuple[str, ...]:
    suffix = path.suffix.lower()

    if shebang:
        parts = list(shebang)
        # A python shebang always honours the interpreter the app runs under so
        # the script sees the same environment as the UI.
        if python and Path(parts[0]).name.startswith("python"):
            parts[0] = python
        return (*parts, str(path))

    template = DEFAULT_COMMANDS.get(suffix)
    if template is None:
        if language == "python":
            template = (python or sys.executable, "{path}")
        else:
            template = ("{path}",)

    if not executable and template == ("{path}",):
        # The kernel cannot exec this (no shebang, no +x), so name the shell.
        shell = _FALLBACK_SHELLS.get(suffix)
        if shell:
            template = (shell, "{path}")
    elif template == ("{path}",) and suffix in _FALLBACK_SHELLS:
        # An executable shell script without a shebang would still fail with
        # ENOEXEC, so the interpreter is not optional here.
        template = (_FALLBACK_SHELLS[suffix], "{path}")

    command = [
        python if (python and item == sys.executable) else item for item in template
    ]
    return tuple(item.format(path=str(path)) for item in command)


def _has_shebang(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(2) == b"#!"
    except OSError:
        return False


def _looks_like_a_script(suffix: str) -> bool:
    """Would this skipped file plausibly have been meant to run?"""
    if not suffix:
        return True
    return suffix not in NON_SCRIPT_SUFFIXES


def known_extensions() -> str:
    """Human readable list of the extensions discovery recognises."""
    return " ".join(sorted(LANGUAGES))


def _scan_file(path: Path, root: Path, python: str | None) -> ScriptInfo | None:
    lines = _read_header(path)
    suffix = path.suffix.lower()
    language = LANGUAGES.get(suffix)

    shebang = _shebang_command(lines)
    if shebang:
        language = _language_from_shebang(shebang) or language
    if language is None:
        return None

    description, is_test = _describe(lines, language, path)

    try:
        info = path.stat()
    except OSError:
        return None

    executable = bool(info.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))

    return ScriptInfo(
        name=path.stem,
        path=path,
        root=root,
        language=language,
        description=description,
        command=_build_command(path, language, shebang, python, executable),
        is_test=is_test,
        executable=executable,
        mtime=info.st_mtime,
        size=info.st_size,
    )


#: Extensions that are never scripts, used only to keep the "skipped" hint
#: focused on files that look like they were meant to be run.
NON_SCRIPT_SUFFIXES = frozenset(
    {
        ".txt",
        ".md",
        ".rst",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".csv",
        ".tsv",
        ".log",
        ".lock",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".html",
        ".css",
        ".sql",
        ".db",
    }
)


def discover(
    root: Path,
    python: str | None = None,
    recursive: bool = True,
    collect_skipped: list[str] | None = None,
) -> list[ScriptInfo]:
    """Return every runnable script under ``root``, sorted by path.

    ``collect_skipped`` optionally receives the names of files that were passed
    over, so the UI can explain an empty list instead of just showing one.
    """
    if not root.is_dir():
        return []

    found: list[ScriptInfo] = []
    if recursive:
        iterator: object = root.rglob("*")
    else:
        iterator = root.glob("*")

    for path in iterator:  # type: ignore[union-attr]
        if path.name.startswith(".") or not path.is_file():
            continue
        try:
            relative = path.relative_to(root)
        except ValueError:
            relative = path
        if any(part in SKIP_DIRS or part.startswith(".") for part in relative.parts[:-1]):
            continue
        # An unknown extension is not a reason to skip: the shebang says how
        # to run it.  Checking costs a two-byte read per unfamiliar file.
        suffix = path.suffix.lower()
        if suffix not in LANGUAGES and not _has_shebang(path):
            if collect_skipped is not None and _looks_like_a_script(suffix):
                collect_skipped.append(path.name)
            continue

        info = _scan_file(path, root, python)
        if info is not None:
            found.append(info)

    found.sort(key=lambda item: str(item.rel_path).lower())
    return found


def directory_fingerprint(root: Path) -> tuple[float, ...]:
    """Cheap change token used to skip a rescan when nothing moved."""
    if not root.is_dir():
        return ()
    stamps: list[float] = []
    for path in sorted(root.rglob("*")):
        try:
            stamps.append(path.stat().st_mtime)
        except OSError:
            continue
    return tuple(stamps)
