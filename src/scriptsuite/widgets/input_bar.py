"""The stdin bar: what the user types reaches the running script.

Two modes:

* **line** (default) -- edit a line and press Enter to send it plus a newline.
* **raw** -- every keystroke is forwarded verbatim, for programs that want
  escape sequences or drive their own key handling.
"""

from __future__ import annotations

from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.reactive import var
from textual.widgets import Input


class Dismiss(Message):
    """Escape was pressed; the app should return focus to the list."""


class InputBar(Input):
    """Single-line prompt wired straight to the child's stdin."""

    DEFAULT_CSS = """
    InputBar {
        height: 3;
        border: round $border;
        border-title-align: left;
        padding: 0 1;
        background: $boost;
    }
    InputBar:focus {
        border: round $accent;
    }
    InputBar.-disabled {
        opacity: 40%;
    }
    InputBar.-raw {
        border: round $warning;
    }
    """

    BINDINGS = [
        Binding("escape", "release", "Back to script", show=False, priority=True),
        Binding("f6", "toggle_raw", "Raw keys", show=False, priority=True),
    ]

    class Dismiss(Dismiss):
        """Escape was pressed; the app should return focus to the list."""

    class Submitted(Message):
        """Text plus the exact bytes that should reach the child."""

        def __init__(self, text: str, data: bytes) -> None:
            super().__init__()
            self.text = text
            self.data = data

    class Keystroke(Message):
        """A single key forwarded verbatim (raw mode)."""

        def __init__(self, data: bytes) -> None:
            super().__init__()
            self.data = data

    class Interrupt(Message):
        """Ctrl-C requested for the running child."""

    raw = var(False)
    enabled = var(True)

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._history: list[str] = []
        self._history_index = 0

    # -- state ----------------------------------------------------------

    def set_enabled(self, enabled: bool, placeholder: str) -> None:
        self.enabled = enabled
        self.disabled = not enabled
        self.set_class(not enabled, "-disabled")
        if not enabled:
            self.raw = False
        self.placeholder = placeholder

    def reset(self) -> None:
        self.value = ""
        self._history.clear()
        self._history_index = 0
        self.raw = False

    def watch_raw(self, raw: bool) -> None:
        self.set_class(raw, "-raw")
        self.border_title = " stdin · raw " if raw else " stdin "
        self.placeholder = (
            "keystrokes forwarded verbatim · F6 to return to line mode"
            if raw
            else self.placeholder
        )

    # -- events ---------------------------------------------------------

    def _on_key(self, event: events.Key) -> None:
        # These stay local in every mode.
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            self.raw = False
            self.post_message(self.Dismiss())
            return
        if event.key == "ctrl+c":
            event.stop()
            event.prevent_default()
            self.post_message(self.Interrupt())
            return
        if event.key == "f6":
            return

        if self.raw:
            event.stop()
            event.prevent_default()
            self.post_message(self.Keystroke(_encode(event)))
            return

        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self._submit()
            return

        if event.key in ("up", "down"):
            recalled = self._recall(-1 if event.key == "up" else 1)
            if recalled is not None:
                event.stop()
                event.prevent_default()
                self.value = recalled
                self.cursor_position = len(recalled)
            return

        event.stop()

    def _submit(self) -> None:
        raw = self.value
        self.value = ""
        if raw:
            self._history.append(raw)
        self._history_index = len(self._history)
        self.post_message(self.Submitted(raw, (raw + "\n").encode()))

    def _recall(self, direction: int) -> str | None:
        if not self._history:
            return None
        self._history_index = max(
            0, min(len(self._history), self._history_index + direction)
        )
        if self._history_index == len(self._history):
            return ""
        return self._history[self._history_index]

    # -- actions --------------------------------------------------------

    def action_release(self) -> None:
        if self.raw:
            self.raw = False
        self.post_message(self.Dismiss())

    def action_toggle_raw(self) -> None:
        if not self.enabled:
            return
        self.raw = not self.raw
        self.notify_raw_toggle(self.raw)

    def notify_raw_toggle(self, raw: bool) -> None:
        """Hook for the app to surface the mode change in the status bar."""
        app_notify = getattr(self.app, "notify_raw_mode", None)
        if callable(app_notify):
            app_notify(raw)


#: Single printable characters map straight to their UTF-8 bytes.
_SIMPLE_KEYS = {
    "space": b" ",
    "tab": b"\t",
    "enter": b"\r",
    "escape": b"\x1b",
    "backspace": b"\x7f",
    "delete": b"\x1b[3~",
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "right": b"\x1b[C",
    "left": b"\x1b[D",
    "home": b"\x1b[H",
    "end": b"\x1b[F",
    "pageup": b"\x1b[5~",
    "pagedown": b"\x1b[6~",
    "insert": b"\x1b[2~",
}

#: Control-key naming convention used by Textual.
_CTRL_LETTERS = "abcdefghijklmnopqrstuvwxyz"


def _encode(event: events.Key) -> bytes:
    key = event.key
    if len(key) == 1:
        return key.encode()
    if key in _SIMPLE_KEYS:
        return _SIMPLE_KEYS[key]
    if key.startswith("ctrl+") and len(key) == 6 and key[5] in _CTRL_LETTERS:
        return bytes([ord(key[5].upper()) - 64])
    if key.startswith("f") and key[1:].isdigit():
        code = {"f1": 11, "f2": 12, "f3": 13, "f4": 14, "f5": 15}.get(key)
        if code:
            return bytes([code])
    if key.startswith("alt+") and len(key) == 5:
        return b"\x1b" + key[4].encode()
    return b""
