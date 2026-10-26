"""Left pane: the list of discovered scripts."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import var
from textual.widgets import Input, Label, ListItem, ListView, Static

from ..discovery import ScriptInfo
from ..runner import RunState

LANGUAGE_TAGS: dict[str, str] = {
    "python": "py",
    "shell": "sh",
    "node": "js",
    "ruby": "rb",
    "perl": "pl",
    "php": "php",
    "lua": "lua",
    "go": "go",
    "rust": "rs",
    "expect": "exp",
    "tcl": "tcl",
}

_STATE_COLOURS = {
    RunState.IDLE: "grey58",
    RunState.RUNNING: "#7aa2f7",
    RunState.PASSED: "#9ece6a",
    RunState.FAILED: "#f7768e",
    RunState.ABORTED: "#e0af68",
}


def status_glyph(state: RunState) -> Text:
    """Coloured status marker for the sidebar row."""
    text = Text(state.glyph, style=_STATE_COLOURS[state])
    if state is RunState.RUNNING:
        text.stylize("bold")
    return text


class ScriptRow(ListItem):
    """A sidebar entry: status glyph, name, blurb, language tag."""

    DEFAULT_CSS = """
    ScriptRow {
        height: 2;
        padding: 0 1;
        layout: horizontal;
        background: transparent;
    }
    ScriptRow:hover {
        background: $panel;
    }
    ScriptRow.--current {
        background: $boost;
    }
    ScriptRow > .glyph {
        width: 2;
        height: 2;
        content-align: left middle;
    }
    ScriptRow > .meta {
        width: 1fr;
        height: 2;
        layout: vertical;
        overflow: hidden;
    }
    ScriptRow .name {
        width: 1fr;
        height: 1;
        text-style: bold;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    ScriptRow .blurb {
        width: 1fr;
        height: 1;
        color: $text-muted;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    ScriptRow > .tag {
        width: 3;
        height: 2;
        content-align: right middle;
        color: $text-muted;
    }
    """

    def __init__(self, info: ScriptInfo, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.info = info
        self.state = RunState.IDLE
        self.summary: str | None = None

    class Clicked(Message):
        """A row was clicked; ``chain`` is 1 for a single, 2 for a double click."""

        def __init__(self, row: ScriptRow, chain: int) -> None:
            super().__init__()
            self.row = row
            self.chain = chain

    def _on_click(self, event: events.Click) -> None:
        # Replace ListItem's behaviour, which selects on every click. One click
        # only moves the highlight; a double click is what starts a run.
        event.stop()
        # ListItem's own `_on_click` runs later in the MRO unless prevented;
        # that base handler is what posts `_ChildClicked` and selects.
        event.prevent_default()
        self.post_message(self.Clicked(self, event.chain))

    def compose(self) -> ComposeResult:
        yield Static(status_glyph(self.state), classes="glyph")
        with Vertical(classes="meta"):
            yield Label(self.render_name(), classes="name")
            yield Label(self.render_blurb(), classes="blurb")
        yield Static(self.render_tag(), classes="tag")

    # -- rendering ------------------------------------------------------

    def render_name(self) -> Text:
        text = Text(self.info.name, style="bold")
        if self.info.is_test:
            text.append("  test", style="#7aa2f7")
        return text

    def render_blurb(self) -> Text:
        body = self.summary or self.info.description or self.info.rel_path
        return Text(body, style="grey58")

    def render_tag(self) -> Text:
        tag = LANGUAGE_TAGS.get(self.info.language, self.info.language[:3])
        return Text(tag, style="grey42")

    def set_state(self, state: RunState, summary: str | None = None) -> None:
        self.state = state
        self.summary = summary
        self._refresh()

    def _refresh(self) -> None:
        if not self.is_mounted:
            return
        self.query_one(".glyph", Static).update(status_glyph(self.state))
        self.query_one(".name", Label).update(self.render_name())
        self.query_one(".blurb", Label).update(self.render_blurb())
        self.query_one(".tag", Static).update(self.render_tag())

    def set_current(self, is_current: bool) -> None:
        self.set_class(is_current, "--current")


class ScriptActivated(Message):
    """The highlighted script was activated (Enter / double-click)."""

    def __init__(self, info: ScriptInfo) -> None:
        super().__init__()
        self.info = info


class ScriptList(ListView):
    """Sidebar list with a live filter on top of discovery."""

    DEFAULT_CSS = """
    ScriptList {
        background: transparent;
        height: 1fr;
        padding: 0;
        border: none;
        scrollbar-color: $accent 20%;
        scrollbar-background: $panel;
    }
    """

    BINDINGS = [
        Binding("/", "filter", "Filter", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    filter_text = var("", init=False)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._all: list[ScriptInfo] = []
        self._rows: list[ScriptRow] = []
        self._token = 0
        self._selected_key: str | None = None
        #: The row currently carrying the ``--current`` class, tracked so a
        #: highlight change only re-styles two rows instead of every row.
        self._current_row: ScriptRow | None = None
        #: Optional callback ``key -> (state, summary)`` used when rows are
        #: rebuilt, so a rescan does not erase every run badge.
        self.state_for: Any = None

    # -- population -----------------------------------------------------

    def set_scripts(self, scripts: list[ScriptInfo]) -> None:
        self._all = list(scripts)
        self.schedule_rebuild()

    def schedule_rebuild(self) -> None:
        """Rebuild off the current call path; safe to call at any time."""
        self._token += 1
        token = self._token
        self.run_worker(self._rebuild(token), group="rebuild", exclusive=True)

    def watch_filter_text(self) -> None:
        self.schedule_rebuild()

    def visible_scripts(self) -> list[ScriptInfo]:
        needle = self.filter_text.strip().lower()
        if not needle:
            return list(self._all)
        return [
            info
            for info in self._all
            if needle in info.name.lower()
            or needle in info.rel_path.lower()
            or needle in info.language.lower()
            or needle in info.description.lower()
        ]

    def rows(self) -> list[ScriptRow]:
        """The rows currently mounted, in display order."""
        return list(self._rows)

    async def _rebuild(self, token: int) -> None:
        scripts = self.visible_scripts()
        if token != self._token:
            return

        desired = self._selected_key
        rows = [ScriptRow(info) for info in scripts]

        await self.clear()
        if token != self._token:
            # A newer rebuild was requested while this one was in flight.
            self._rows = []
            return

        self._rows = rows
        self._current_row = None
        if rows:
            # Mount first: ListView rejects an index beyond its current nodes.
            await self.mount_all(rows)
            index = next((i for i, row in enumerate(rows) if row.info.key == desired), 0)
            self.index = index
            if self.state_for is not None:
                for row in rows:
                    state, summary = self.state_for(row.info.key)
                    row.set_state(state, summary)
        self._mark_current()

    # -- selection ------------------------------------------------------

    @property
    def current_info(self) -> ScriptInfo | None:
        row = self.current_row
        return row.info if row else None

    @property
    def current_row(self) -> ScriptRow | None:
        if self.index is None or not (0 <= self.index < len(self._rows)):
            return None
        return self._rows[self.index]

    def row_for(self, key: str) -> ScriptRow | None:
        for row in self._rows:
            if row.info.key == key:
                return row
        return None

    def focus_script(self, key: str) -> None:
        self._selected_key = key
        for index, row in enumerate(self._rows):
            if row.info.key == key:
                self.index = index
                return

    def _mark_current(self) -> None:
        current = self.current_row
        if current is not None:
            self._selected_key = current.info.key
        previous = self._current_row
        if previous is not None and previous is not current and previous.parent is not None:
            previous.set_current(False)
        if current is not None and current is not previous:
            current.set_current(True)
        self._current_row = current

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        # Deliberately not stopped: the app listens for this to retitle the
        # header as the selection moves.
        self._mark_current()

    @on(ScriptRow.Clicked)
    def _on_row_clicked(self, event: ScriptRow.Clicked) -> None:
        event.stop()
        self.focus()
        try:
            index = self._nodes.index(event.row)
        except ValueError:  # pragma: no cover - row removed mid-click
            return
        self.index = index
        if event.chain >= 2:
            self.post_message(self.Selected(self, event.row, index))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        self._mark_current()
        info = self.current_info
        if info is not None:
            self.post_message(ScriptActivated(info))

    def action_filter(self) -> None:
        """Jump focus to the filter field above the list."""
        field = self.screen.query_one("#filter-input", Input)
        field.focus()


class SidebarHeader(Horizontal):
    """Title row above the list."""

    DEFAULT_CSS = """
    SidebarHeader {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
    }
    SidebarHeader .title {
        width: 1fr;
        text-style: bold;
    }
    SidebarHeader .count {
        width: auto;
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label(" SCRIPTS", classes="title")
        yield Label("", classes="count", id="script-count")

    def set_count(self, shown: int, total: int) -> None:
        label = self.query_one("#script-count", Label)
        label.update(f"{shown}/{total}" if shown != total else str(total))
