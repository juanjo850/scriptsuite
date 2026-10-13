"""Single-line status bar at the very bottom of the screen."""

from __future__ import annotations

from rich.text import Text
from textual.containers import Horizontal
from textual.widgets import Static

from ..discovery import ScriptInfo
from ..runner import RunState

_STATE_COLOURS = {
    RunState.IDLE: "grey58",
    RunState.RUNNING: "#7aa2f7",
    RunState.PASSED: "#9ece6a",
    RunState.FAILED: "#f7768e",
    RunState.ABORTED: "#e0af68",
}


class StatusBar(Horizontal):
    """Message on the left, run state and key hints on the right."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
    }
    StatusBar .message {
        width: 1fr;
        height: 1;
    }
    StatusBar .state {
        width: auto;
        height: 1;
        padding: 0 1;
    }
    StatusBar .keys {
        width: auto;
        height: 1;
        color: $text-muted;
    }
    StatusBar .keys .key {
        color: $accent;
    }
    """

    def compose(self):
        yield Static(Text(""), classes="message", id="status-message")
        yield Static(Text(""), classes="state", id="status-state")
        yield Static(Text(""), classes="keys", id="status-keys")

    # -- rendering ------------------------------------------------------

    def set_message(self, message: str, style: str = "") -> None:
        text = Text()
        if style:
            text.append("● ", style=style)
        text.append(message)
        widget = self.query("#status-message")
        if widget:
            widget[0].update(text)

    def set_state(
        self, state: RunState, info: ScriptInfo | None, detail: str = ""
    ) -> None:
        widget = self.query("#status-state")
        text = Text()
        if info is not None:
            text.append(state.glyph + " ", style=_STATE_COLOURS[state])
            text.append(f"{info.name} · {state.label}", style=_STATE_COLOURS[state])
        if detail:
            text.append("  ")
            text.append(detail, style="grey42")
        if widget:
            widget[0].update(text)

    def set_keys(self, keys: list[tuple[str, str]]) -> None:
        """``[("enter", "run"), ("f5", "rescan")]`` renders as key hints."""
        text = Text()
        for index, (key, label) in enumerate(keys):
            if index:
                text.append("  ")
            text.append(f" {key} ", style="reverse #7aa2f7")
            text.append(f" {label}")
        keys_widget = self.query("#status-keys")
        if keys_widget:
            keys_widget[0].update(text)
