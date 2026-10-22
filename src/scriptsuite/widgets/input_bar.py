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
        #: The line-mode placeholder, restored when raw mode ends.
        self._line_placeholder = ""

    # -- state ----------------------------------------------------------

    def set_enabled(self, enabled: bool, placeholder: str) -> None:
        self.enabled = enabled
        self.disabled = not enabled
        self.set_class(not enabled, "-disabled")
        if not enabled:
            self.raw = False
        self._line_placeholder = placeholder
        if not self.raw:
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
            else self._line_placeholder
        )

    # -- events ---------------------------------------------------------

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "f6":
            # Handled by the binding below; never forwarded, or raw mode could
            # not be left.
            return

        if self.raw:
            # Verbatim includes escape and ^C: a program driving its own key
            # handling must receive them.
            event.stop()
            event.prevent_default()
            data = b"\x03" if event.key == "ctrl+c" else _encode(event)
            if data:
                self.post_message(self.Keystroke(data))
            return

        if event.key == "escape":
            # The priority binding calls `action_release`, which knows whether
            # raw mode should forward the byte or hand focus back.
            return

        if event.key == "ctrl+c":
            # Keep copy working when text is selected; otherwise ^C means
            # interrupt, as it would in a terminal.
            if self.selected_text:
                return
            event.stop()
            event.prevent_default()
            self.post_message(self.Interrupt())
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

        # Everything else belongs to Input: cursor movement, backspace,
        # selection, ctrl+u/ctrl+k editing, and so on.  Calling through is what
        # keeps the bar editable and lets unhandled keys bubble to the app.
        await super()._on_key(event)

    def _on_paste(self, event: events.Paste) -> None:
        if self.raw:
            event.stop()
            event.prevent_default()
            if event.text:
                self.post_message(self.Keystroke(event.text.encode()))
            return
        super()._on_paste(event)

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
            # Escape is a normal keystroke for a full-screen program; leave raw
            # mode with F6 instead.
            self.post_message(self.Keystroke(b"\x1b"))
            return
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


#: Keys whose byte sequence does not depend on modifiers.
_SIMPLE_KEYS = {
    "space": b" ",
    "tab": b"\t",
    "shift+tab": b"\x1b[Z",
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
    "f1": b"\x1bOP",
    "f2": b"\x1bOQ",
    "f3": b"\x1bOR",
    "f4": b"\x1bOS",
    "f5": b"\x1b[15~",
    "f6": b"\x1b[17~",
    "f7": b"\x1b[18~",
    "f8": b"\x1b[19~",
    "f9": b"\x1b[20~",
    "f10": b"\x1b[21~",
    "f11": b"\x1b[23~",
    "f12": b"\x1b[24~",
}

#: CSI modifier parameter for xterm-style modified arrows / navigation keys.
_ARROW_MODIFIERS = {
    "shift": 2,
    "alt": 3,
    "ctrl": 5,
    "ctrl+shift": 6,
    "alt+shift": 4,
    "ctrl+alt": 7,
}

#: Control-key naming convention used by Textual.
_CTRL_LETTERS = "abcdefghijklmnopqrstuvwxyz"


def _encode(event: events.Key) -> bytes:
    key = event.key
    character = getattr(event, "character", None)
    if character is not None:
        # Shifted punctuation arrives as a key name like ``exclamation_mark``;
        # the printable character is the only reliable byte.  Alt still needs
        # its ESC prefix, even when the parser supplies a character.
        if key.startswith("alt+"):
            return b"\x1b" + character.encode()
        return character.encode()
    if len(key) == 1:
        return key.encode()
    if key in _SIMPLE_KEYS:
        return _SIMPLE_KEYS[key]
    if key.startswith("ctrl+") and len(key) == 6 and key[5] in _CTRL_LETTERS:
        return bytes([ord(key[5].upper()) - 64])
    if key.startswith("alt+") and len(key) == 5:
        return b"\x1b" + key[4].encode()

    parts = key.split("+")
    base = parts[-1]
    modifiers = "+".join(parts[:-1])
    if (
        "ctrl" in modifiers.split("+")
        and len(base) == 1
        and base.lower() in _CTRL_LETTERS
    ):
        # A terminal cannot distinguish Ctrl+K from Ctrl+Shift+K; sending the
        # control byte is what a real terminal does.
        return bytes([ord(base.upper()) - 64])

    # Modified arrows / navigation: ESC [ 1 ; <modifier> <final>.
    if base in _SIMPLE_KEYS:
        sequence = _SIMPLE_KEYS[base]
        parameter = _ARROW_MODIFIERS.get(modifiers)
        if parameter is not None and sequence.startswith(b"\x1b["):
            if sequence.endswith(b"~"):
                return sequence[:-1] + f";{parameter}~".encode()
            return b"\x1b[1;" + str(parameter).encode() + sequence[-1:]
        # Alt + arrow and friends.
        if modifiers == "alt":
            return b"\x1b" + sequence

    return b""
