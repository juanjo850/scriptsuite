"""Bottom pane: the highlighted script's source, read-only.

Syntax-highlighted with Rich (Pygments underneath), so no tree-sitter grammar
is needed.  Follows the sidebar selection, and degrades to plain text for
anything Pygments does not recognise.
"""

from __future__ import annotations

from typing import Any

from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Static

from ..discovery import ScriptInfo

#: Discovery language -> Pygments lexer.  Expect has no grammar of its own and
#: is a Tcl dialect, so it borrows the Tcl lexer.
LEXERS: dict[str, str] = {
    "python": "python",
    "shell": "bash",
    "expect": "tcl",
    "tcl": "tcl",
    "node": "javascript",
    "ruby": "ruby",
    "perl": "perl",
    "lua": "lua",
    "php": "php",
    "go": "go",
    "rust": "rust",
}

#: Files larger than this are truncated: the point is a quick look, and
#: rendering a huge file in a narrow pane is neither quick nor useful.
MAX_BYTES = 512 * 1024
#: Whitespace-only trailing noise is dropped so the height stays honest.
MAX_LINES = 2_000

#: Monokai's palette sits well against the app's own background.
THEME = "monokai"

#: Used when the pane's own background cannot be read yet.
FALLBACK_BACKGROUND = "#1a1b26"


class SourceView(Vertical):
    """Read-only source for the highlighted script."""

    DEFAULT_CSS = """
    SourceView {
        width: 1fr;
        height: 1fr;
        border: round $border;
        background: $surface;
        padding: 0 1;
        layout: vertical;
    }
    SourceView:focus-within {
        border: round $accent;
    }
    SourceView > .head {
        width: 1fr;
        height: 1;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    SourceView > #source-body {
        width: 1fr;
        height: 1fr;
        padding: 0;
        scrollbar-size-vertical: 1;
    }
    SourceView > .note {
        width: 1fr;
        height: 1;
        color: $text-muted;
        text-style: italic;
    }
    """

    BINDINGS = [
        Binding("escape", "release", "Back to scripts", show=False, priority=True),
    ]

    class Dismiss(Message):
        """Escape was pressed; the app should return focus to the list."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.key: str | None = None

    def compose(self) -> ComposeResult:
        yield Static(Text(""), classes="head", id="source-head")
        yield VerticalScroll(Static("", markup=False), id="source-body")

    # -- content --------------------------------------------------------

    def show(self, info: ScriptInfo | None) -> None:
        """Point the pane at ``info`` and render its source."""
        self.key = info.key if info else None
        head = self.query("#source-head")
        body = self.query("#source-body")
        if not head or not body:
            return
        head, body = head[0], body[0]

        body.remove_children()
        if info is None:
            head.update(Text("no script selected", style="grey58 italic"))
            body.mount(Static("", markup=False))
            body.scroll_home(animate=False)
            return

        source, note = _read(info)
        head.update(self._head(info, source, note))

        widgets: list[Any] = []
        if note and source:
            # A truncation notice as its own line: the note is not text, and
            # Rich renderables cannot be concatenated into a Text.
            widgets.append(Static(Text(f"… {note}", style="#e0af68"), classes="note"))
        widgets.append(Static(self._render_source(info, source, note), markup=False))
        body.mount(*widgets)
        body.scroll_home(animate=False)

    def _head(self, info: ScriptInfo, source: str, note: str) -> Text:
        text = Text()
        text.append(info.rel_path, style="bold")
        text.append(f"  {info.language}", style="grey42")
        count = source.count("\n") + 1 if source else 0
        text.append(f"  {count} lines", style="grey42")
        if note:
            text.append(f"  · {note}", style="#e0af68")
        text.truncate(150, overflow="ellipsis")
        return text

    def _render_source(self, info: ScriptInfo, source: str, note: str) -> Any:
        if not source and note:
            return Text(note, style="grey58 italic")

        code = Syntax(
            source,
            lexer=_lexer_for(info),
            theme=THEME,
            line_numbers=True,
            # A narrow pane wraps rather than hiding the end of the line behind
            # a horizontal scrollbar nobody will find.
            word_wrap=True,
            padding=0,
            # The theme carries its own background; use ours so the block does
            # not sit as a lighter rectangle inside the panel.
            background_color=self._background(),
        )
        return code

    def _background(self) -> str:
        """The pane's own background, so the code block blends into it.

        Rich wants a plain ``#rrggbb`` string and rejects anything with an
        alpha channel -- which is exactly what an unresolved or transparent
        style hands back -- so the value is validated before use.
        """
        try:
            hexed = getattr(self.background_colors[1], "hex", None)
        except Exception:  # noqa: BLE001 - before the style is resolved
            hexed = None
        if isinstance(hexed, str) and len(hexed) == 7 and hexed.startswith("#"):
            return hexed
        return FALLBACK_BACKGROUND

    # -- focus ----------------------------------------------------------

    def action_release(self) -> None:
        self.post_message(self.Dismiss())


def _lexer_for(info: ScriptInfo) -> str:
    """A Pygments lexer name for the script, falling back to a guess."""
    mapped = LEXERS.get(info.language)
    if mapped:
        return mapped
    try:
        return Syntax.guess_lexer(info.rel_path)
    except Exception:  # noqa: BLE001 - guessing is best effort
        return "text"


def _read(info: ScriptInfo) -> tuple[str, str]:
    """The file's text, plus a note if it was cut short or could not be read."""
    try:
        # Read one byte past the limit so "too large" is detected without
        # pulling a multi-gigabyte file into memory first.
        with info.path.open("rb") as handle:
            raw_bytes = handle.read(MAX_BYTES + 1)
    except OSError as exc:
        return "", f"could not read ({exc.strerror or exc})"

    too_large = len(raw_bytes) > MAX_BYTES
    raw = raw_bytes[:MAX_BYTES].decode("utf-8", errors="replace")

    note = "file too large, showing the start" if too_large else ""

    lines = raw.splitlines()
    if len(lines) > MAX_LINES:
        raw = "\n".join(lines[:MAX_LINES])
        note = note or f"showing the first {MAX_LINES} lines"

    return raw, note
