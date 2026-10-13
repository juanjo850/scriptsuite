"""Discovery of the command-line options a script declares.

Python scripts are parsed with :mod:`ast`, so ``argparse`` options come back
with their real help text, whether they take a value, and their choices.
Everything else is scanned: shell ``getopts`` is understood properly, and
otherwise any ``--flag`` in the source is offered as a reference.

Nothing here executes the script, and a parse failure is never fatal: a
script that cannot be understood simply shows no options.
"""

from __future__ import annotations

import ast
import re
import shlex
from dataclasses import dataclass, field, replace

#: Actions that never consume a following value.
FLAG_ACTIONS = frozenset(
    {"store_true", "store_false", "store_const", "count", "help", "version"}
)
#: Actions that may appear more than once.
REPEATING_ACTIONS = frozenset({"append", "append_const"})

_FLAG_ACTIONS = re.compile(r"\baction\s*=\s*['\"](\w+)['\"]")
_GETOPTS = re.compile(r"""\bgetopts\s+['"]([^'"]+)['"]""")
_USAGE = re.compile(
    r"^\s*(?:#\s*|puts?\s+|echo\s+)?usage\s*[:\-]\s*(.+)$", re.IGNORECASE | re.MULTILINE
)
_LONG_FLAG = re.compile(r"(?<![\w-])(--[a-zA-Z][\w-]*)(?![\w-])")
#: Help text must read like prose: a letter, then letters, digits or spaces.
#: This rejects things like `-f "$path"` or `--flag']))`, which are code, not
#: documentation.
_FLAG_WITH_HELP = re.compile(
    r"(?P<flag>--[\w-]+|-[a-zA-Z])[\s'\"`]{1,4}(?P<help>[A-Za-z][\w ,.'()/-]{2,90})"
)
#: Positional argument access, by shell/expect convention.
_POSITIONAL = re.compile(r"(?:\$argv\((\d+)\)|\$(?:\{(\d+)\}|(\d+))|\$@)")
#: Tcl keeps its arguments in a list: ``lindex $argv 0``.
_TCL_ARGV = re.compile(r"\$(?:::)?argv\b")
#: Python without argparse still indexes ``sys.argv`` directly.
_PY_ARGV = re.compile(r"\b(?:sys\.)?argv\[(\d+)\]")

#: ``cmdline::getoptions`` specification variable, e.g.
#: ``cmdline::getoptions argv $parameters`` names ``parameters``.
_CMDLINE_CALL = re.compile(r"cmdline::getoptions\s+(\w+)\s+\$(\w+)")
#: Type names a getoptions spec accepts, written ``name.type`` or ``-type``.
_TCL_TYPES = {
    "flag",
    "bool",
    "boolean",
    "arg",
    "text",
    "string",
    "integer",
    "int",
    "double",
    "float",
}
#: Types that are a switch rather than a value.
_TCL_FLAG_TYPES = {"flag", "bool", "boolean"}


@dataclass(frozen=True)
class Option:
    """One command-line option a script accepts."""

    flags: tuple[str, ...]
    help: str = ""
    takes_value: bool = False
    metavar: str = ""
    choices: tuple[str, ...] = ()
    default: str | None = None
    required: bool = False
    repeatable: bool = False

    @property
    def label(self) -> str:
        """Flags as shown in the panel, e.g. ``-v, --verbose``."""
        return ", ".join(self.flags)

    @property
    def emit_flag(self) -> str:
        """The spelling to pass on the command line: long form preferred."""
        longs = [flag for flag in self.flags if flag.startswith("--")]
        return longs[0] if longs else self.flags[0]

    @property
    def value_hint(self) -> str:
        """What follows the flag when one is needed."""
        if not self.takes_value:
            return ""
        if self.metavar:
            return self.metavar
        if self.choices:
            return "{" + "|".join(self.choices) + "}"
        return "VALUE"


@dataclass(frozen=True)
class Argument:
    """A positional argument the script expects."""

    name: str
    help: str = ""
    required: bool = False
    nargs: str = ""


@dataclass(frozen=True)
class Field:
    """One editable control in the arguments pane.

    ``kind`` is ``"value"`` (a text field) or ``"flag"`` (a checkbox).
    """

    id: str
    label: str
    kind: str = "value"
    help: str = ""
    placeholder: str = ""
    positional: bool = False
    #: The token to emit, e.g. ``--verbose`` or ``target``.
    token: str = ""

    @property
    def takes_value(self) -> bool:
        return self.kind == "value"


@dataclass(frozen=True)
class ProgramInfo:
    """Everything the panel shows about one script's interface."""

    language: str
    summary: str = ""
    usage: str = ""
    options: tuple[Option, ...] = field(default_factory=tuple)
    positionals: tuple[Argument, ...] = field(default_factory=tuple)
    positional_count: int | None = None
    detected: str = "none"

    @property
    def has_options(self) -> bool:
        return bool(self.options)

    @property
    def takes_positional(self) -> bool:
        return bool(self.positional_count or self.positionals)

    @property
    def positional_hint(self) -> str:
        count = len(self.positionals) or self.positional_count
        if not count:
            return ""
        if count == 1:
            return "takes 1 positional argument"
        return f"takes up to {count} positional arguments"

    def named_positionals(self) -> tuple[Argument, ...]:
        """Positionals to offer, named where the source names them."""
        if self.positionals:
            return self.positionals
        # Counted but unnamed (a `$argv` script, or one indexing sys.argv):
        # still worth a field each, just labelled by position.
        return tuple(
            Argument(name=f"arg {index + 1}")
            for index in range(self.positional_count or 0)
        )

    def fields(self) -> tuple[Field, ...]:
        """Every control the pane should draw, in command-line order."""
        fields: list[Field] = []
        for index, argument in enumerate(self.named_positionals()):
            fields.append(
                Field(
                    id=f"pos{index}",
                    label=argument.name,
                    kind="value",
                    help=argument.help,
                    placeholder=argument.nargs or "",
                    positional=True,
                    token=argument.name,
                )
            )
        for option in self.options:
            fields.append(
                Field(
                    id=option.flags[-1],
                    label=option.label,
                    kind="value" if option.takes_value else "flag",
                    help=option.help,
                    placeholder=_value_placeholder(option) if option.takes_value else "",
                    token=option.emit_flag,
                )
            )
        return tuple(fields)


def _value_placeholder(option: Option) -> str:
    """Hint for an empty value field: what to type, and what the default is."""
    parts: list[str] = []
    if option.choices:
        parts.append("{" + "|".join(option.choices) + "}")
    elif option.metavar:
        parts.append(option.metavar)
    else:
        parts.append("VALUE")
    if option.default not in (None, ""):
        parts.append(f"default {option.default}")
    if option.required:
        parts.append("required")
    return " \u00b7 ".join(parts)


# ---------------------------------------------------------------------------
# Tcl / Expect: cmdline::getoptions
# ---------------------------------------------------------------------------


def _tcl_block(source: str, name: str) -> str | None:
    """The body of ``set <name> { ... }``, honouring nested braces."""
    match = re.search(r"\bset\s+" + re.escape(name) + r"\s*\{", source)
    if not match:
        return None
    depth = 0
    start = match.end() - 1
    for index in range(start, len(source)):
        char = source[index]
        if source[index - 1 : index] == "\\":
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[match.end() : index]
    return None


def _tcl_tokens(spec: str) -> list[str]:
    """Split a braced Tcl list into words, honouring quoted strings."""
    spec = spec.strip()
    if spec.startswith("{") and spec.endswith("}"):
        spec = spec[1:-1]

    tokens: list[str] = []
    current: list[str] = []
    quoted = False
    started = False

    for char in spec:
        if char == '"' and not quoted:
            quoted, started = True, True
            continue
        if char == '"' and quoted:
            quoted = False
            tokens.append("".join(current))
            current, started = [], False
            continue
        if char.isspace() and not quoted:
            if current or started:
                tokens.append("".join(current))
                current, started = [], False
            continue
        current.append(char)
    if current:
        tokens.append("".join(current))
    return tokens


def _cmdline_options(source: str) -> tuple[Option, ...]:
    """Parse a ``cmdline::getoptions`` specification block.

    Specs read ``{name.type default "description"}``, or ``{name "description"}``
    for a plain switch.  Everything is passed with a leading ``--``, which is
    what the package expects -- ``expect`` reserves ``-`` for its own options.
    """
    call = _CMDLINE_CALL.search(source)
    if not call:
        return ()
    body = _tcl_block(source, call.group(2))
    if body is None:
        return ()

    options: list[Option] = []
    for chunk in re.findall(r"\{[^{}]*\}", body):
        tokens = _tcl_tokens(chunk)
        if not tokens:
            continue
        base = tokens[0].split(".")[0]
        if not re.fullmatch(r"[A-Za-z][\w-]*", base):
            continue

        name, _, kind = tokens[0].partition(".")
        rest = tokens[1:]
        kind = kind.lstrip(".-")
        if not kind and rest and rest[0].lstrip(".-") in _TCL_TYPES:
            kind = rest.pop(0).lstrip(".-")
        # No type token means a plain switch: `{debug "Enable debug output"}`.
        kind = kind or "flag"

        default: str | None = None
        description = ""
        if len(rest) == 1:
            description = rest[0]
        elif rest:
            default, description = rest[-2], rest[-1]

        options.append(
            Option(
                flags=(f"--{name}",),
                help=description,
                takes_value=kind not in _TCL_FLAG_TYPES,
                default=default,
            )
        )
    return tuple(options)


# ---------------------------------------------------------------------------
# Cache: re-parsing on every selection change would be wasteful
# ---------------------------------------------------------------------------

_CACHE: dict[tuple[str, float, int], ProgramInfo] = {}
_CACHE_LIMIT = 256


def describe(path, language: str) -> ProgramInfo:  # noqa: ANN001 - Path
    """Extract the interface of the script at ``path`` (cached by mtime)."""
    try:
        stat = path.stat()
        key = (str(path), stat.st_mtime, stat.st_size)
    except OSError:
        return ProgramInfo(language=language)

    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    info = _extract(path, language)

    if len(_CACHE) >= _CACHE_LIMIT:
        _CACHE.clear()
    _CACHE[key] = info
    return info


def _extract(path, language: str) -> ProgramInfo:  # noqa: ANN001
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ProgramInfo(language=language)

    if language == "python":
        # argparse is the only reliable source for Python; scanning the text
        # turns every `"--x"` inside the code into a phantom flag.
        info = _python(source)
        if info.positional_count is None:
            # No argparse: fall back to whichever sys.argv index it reads.
            highest = max(
                (int(match.group(1)) for match in _PY_ARGV.finditer(source)), default=0
            )
            if highest:
                info = replace(info, positional_count=highest)
        return info

    usage = _scan_usage(source)
    positionals = _scan_positionals(source)

    options = _cmdline_options(source)
    if options:
        # cmdline::getoptions declares its options explicitly; trust it
        # rather than guessing from the surrounding text.
        return ProgramInfo(
            language=language,
            options=options,
            positional_count=positionals,
            detected="cmdline",
        )

    options = _scan_flags(source, usage)
    return ProgramInfo(
        language=language,
        usage=usage,
        options=options,
        positional_count=positionals,
        detected="scan" if (options or positionals) else "none",
    )


# ---------------------------------------------------------------------------
# Python / argparse
# ---------------------------------------------------------------------------


def _python(source: str) -> ProgramInfo:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return ProgramInfo(language="python")

    summary = ""
    options: list[Option] = []
    arguments: list[Argument] = []
    detected = "none"

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue

        if node.func.attr == "ArgumentParser":
            summary = _literal(node, "description") or _first_arg(node) or ""
            detected = "argparse"
        elif node.func.attr == "add_argument":
            detected = "argparse"
            option, argument = _python_option(node)
            if argument is not None:
                arguments.append(argument)
            elif option is not None:
                options.append(option)

    # Fold duplicate declarations of the same flag, e.g. re-parsing in a loop.
    unique: dict[tuple[str, ...], Option] = {}
    for option in options:
        unique.setdefault(option.flags, option)

    return ProgramInfo(
        language="python",
        summary=summary,
        options=tuple(unique.values()),
        positionals=tuple(arguments),
        positional_count=len(arguments) or None,
        detected=detected,
    )


def _first_arg(node: ast.Call) -> str | None:
    for arg in node.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return None


def _python_option(node: ast.Call) -> tuple[Option | None, Argument | None]:
    """Build the option or positional declared by one ``add_argument`` call."""
    flags = tuple(
        arg.value
        for arg in node.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    )
    if not flags:
        return None, None
    if not any(flag.startswith("-") for flag in flags):
        # A positional: its first token is the metavar.
        return None, Argument(
            name=flags[0],
            help=_literal(node, "help") or "",
            required=bool(_bool(node, "required")),
            nargs=_literal(node, "nargs") or "",
        )

    action = _literal(node, "action") or "store"
    nargs = _literal(node, "nargs")
    takes_value = action not in FLAG_ACTIONS
    if nargs == 0 or (nargs == "?" and action == "store_const"):
        takes_value = False

    choices = _literal_list(node, "choices")
    default = _literal(node, "default")
    return (
        Option(
            flags=flags,
            help=_literal(node, "help") or "",
            takes_value=takes_value,
            metavar=_literal(node, "metavar") or "",
            choices=choices,
            default=None if default is None else default,
            required=_bool(node, "required") or False,
            repeatable=action in REPEATING_ACTIONS,
        ),
        None,
    )


def _value(node: ast.Call, keyword: str):
    """The literal value of a keyword argument, or ``None``."""
    for item in node.keywords:
        if item.arg != keyword:
            continue
        try:
            return ast.literal_eval(item.value)
        except (ValueError, SyntaxError):
            return None
    return None


def _literal(node: ast.Call, keyword: str) -> str | None:
    """Keyword value as text, ``None`` when absent, null or not a literal."""
    value = _value(node, keyword)
    if value is None:
        return None
    return str(value)


def _bool(node: ast.Call, keyword: str) -> bool | None:
    value = _value(node, keyword)
    return value if isinstance(value, bool) else None


def _literal_list(node: ast.Call, keyword: str) -> tuple[str, ...]:
    for item in node.keywords:
        if item.arg != keyword:
            continue
        try:
            value = ast.literal_eval(item.value)
        except (ValueError, SyntaxError):
            return ()
        if isinstance(value, (list, tuple, set)):
            return tuple(str(entry) for entry in value)
    return ()


# ---------------------------------------------------------------------------
# Everything else
# ---------------------------------------------------------------------------


def _scan_usage(source: str) -> str:
    match = _USAGE.search(source)
    if not match:
        return ""
    usage = match.group(1).strip().strip("'\"`")
    return " ".join(usage.split())[:100]


def _scan_positionals(source: str) -> int | None:
    """Highest positional index the script reaches for, if any."""
    highest = 0
    seen = False
    for match in _POSITIONAL.finditer(source):
        seen = True
        index = next((g for g in match.groups() if g), None)
        if index is not None:
            highest = max(highest, int(index))
    if _TCL_ARGV.search(source):
        # A Tcl list: the count is whatever the caller passed.
        seen = True
        highest = max(highest, 1)
    if not seen:
        return None
    return highest or 1


def _scan_flags(source: str, usage: str) -> tuple[Option, ...]:
    """Flags for a non-Python script.

    ``getopts`` is decoded properly.  Beyond that a bare ``-f`` in the text is
    usually something else entirely (``wc -c``, ``set -e``), so only long
    flags are kept, and only when something documents them: either an inline
    help string or a usage line.  Showing a flag that does not exist would be
    worse than showing none.
    """
    getopts = _GETOPTS.search(source)
    if getopts:
        return _from_getopts(getopts.group(1))

    helps: dict[str, str] = {}
    for match in _FLAG_WITH_HELP.finditer(source):
        flag = match.group("flag")
        text = match.group("help").strip()
        if text and flag not in helps:
            helps[flag] = text

    documented = set(_LONG_FLAG.findall(usage)) if usage else set()

    options: list[Option] = []
    for match in _LONG_FLAG.finditer(source):
        flag = match.group(1)
        if flag in {option.flags[0] for option in options}:
            continue
        help_text = helps.get(flag, "")
        if not help_text and flag not in documented:
            continue
        options.append(Option(flags=(flag,), help=help_text, takes_value=False))
        if len(options) >= 40:
            break
    return tuple(options)


def _from_getopts(optstring: str) -> tuple[Option, ...]:
    """Decode a POSIX ``getopts`` optstring such as ``":ab:c"``."""
    quiet = optstring.startswith(":")
    if quiet:
        optstring = optstring[1:]

    options: list[Option] = []
    index = 0
    while index < len(optstring):
        char = optstring[index]
        if char == ":":
            index += 1
            continue
        takes_value = index + 1 < len(optstring) and optstring[index + 1] == ":"
        options.append(
            Option(
                flags=(f"-{char}",),
                takes_value=takes_value,
                metavar="VALUE" if takes_value else "",
            )
        )
        index += 2 if takes_value else 1
    return tuple(options)


def build_arguments(
    program: ProgramInfo,
    values: dict[str, str | bool],
    extra: str = "",
) -> tuple[list[str], str | None]:
    """Assemble command-line tokens from the pane's fields.

    ``values`` maps field ids to what the user typed or toggled.  Order is
    positional arguments, then named options, then whatever was left in the
    free-form box -- the conventional shape of a command line.

    Returns the tokens and, if the free-form text had broken quoting, why.
    """
    tokens: list[str] = []
    for spec in program.fields():
        value = values.get(spec.id)
        if isinstance(value, bool):
            if value:
                tokens.append(spec.token)
            continue
        if not value:
            continue
        if spec.positional:
            tokens.append(value)
        else:
            tokens.extend([spec.token, value])

    extra_tokens, problem = split_arguments(extra)
    tokens.extend(extra_tokens)
    return tokens, problem


def split_arguments(text: str) -> tuple[list[str], str | None]:
    """Split an argument string the way a shell would.

    Returns the tokens and, if the quoting was broken, the reason.  The tokens
    are still usable so a stray quote does not stop the run.
    """
    text = text.strip()
    if not text:
        return [], None
    try:
        return shlex.split(text), None
    except ValueError as exc:
        return text.split(), str(exc)
