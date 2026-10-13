"""Title strip above the output: which script is running and how it went."""

from __future__ import annotations

from rich.text import Text
from textual.containers import Horizontal
from textual.widgets import Static

from ..runner import RunState

_STATE_COLOURS = {
    RunState.IDLE: "grey58",
    RunState.RUNNING: "#7aa2f7",
    RunState.PASSED: "#9ece6a",
    RunState.FAILED: "#f7768e",
    RunState.ABORTED: "#e0af68",
}

_COUNT_COLOURS = {
    "passed": "#9ece6a",
    "failed": "#f7768e",
    "errors": "#f7768e",
    "skipped": "#e0af68",
    "xfailed": "#e0af68",
    "xpassed": "#7dcfff",
}

_MAX_IDENTITY = 46
_MAX_COUNTS = 44


class HeaderBar(Horizontal):
    """Runs head: script name, state badge, counts, duration.

    Only two cells compete for space.  An ``auto`` sibling next to a ``1fr``
    one collapses the fraction when the terminal is narrow, so the counts are
    capped and the name gets a guaranteed minimum.
    """

    DEFAULT_CSS = """
    HeaderBar {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text;
    }
    HeaderBar .identity {
        width: 1fr;
        min-width: 12;
        height: 1;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    HeaderBar .counts {
        width: auto;
        max-width: 44;
        height: 1;
        padding-left: 2;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)

    def compose(self):
        yield Static(Text(""), classes="identity", id="run-identity")
        yield Static(Text(""), classes="counts", id="run-counts")

    # -- rendering ------------------------------------------------------

    def update_run(
        self,
        name: str | None,
        state: RunState,
        summary_line: str = "",
        counts: list[tuple[str, int]] | None = None,
        duration: str = "",
    ) -> None:
        # A timer can reach the widget before compose() has run; skip then.
        identity = self.query("#run-identity")
        counts_widget = self.query("#run-counts")
        if not identity or not counts_widget:
            return
        identity[0].update(self._identity(name, state, duration))
        counts_widget[0].update(self._counts(counts or [], summary_line))

    def _identity(self, name: str | None, state: RunState, duration: str) -> Text:
        text = Text()
        if name is None:
            text.append("no script selected", style="grey58 italic")
            return text

        text.append(f"{state.glyph} ", style=_STATE_COLOURS[state])
        text.append(name, style="bold")
        if state is RunState.RUNNING:
            text.append("  running", style=_STATE_COLOURS[state])
        elif state not in (RunState.IDLE, RunState.ABORTED) and duration:
            text.append(f"  {duration}", style="grey58")
        text.truncate(_MAX_IDENTITY, overflow="ellipsis")
        return text

    def _counts(self, counts: list[tuple[str, int]], fallback: str) -> Text:
        text = Text()
        if counts:
            for index, (label, value) in enumerate(counts):
                if index:
                    text.append("  ")
                text.append(f"{value} ", style=_COUNT_COLOURS.get(label, "white"))
                text.append(label, style="grey58")
        elif fallback:
            text.append(fallback, style="grey58")
        text.truncate(_MAX_COUNTS, overflow="ellipsis")
        return text
