"""Tests for the stdin bar's key handling and raw encoding."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding

from scriptsuite.widgets.input_bar import InputBar, _encode


def key(name: str) -> SimpleNamespace:
    return SimpleNamespace(key=name)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a", b"a"),
        ("space", b" "),
        ("enter", b"\r"),
        ("escape", b"\x1b"),
        ("tab", b"\t"),
        ("backspace", b"\x7f"),
        ("up", b"\x1b[A"),
        ("f1", b"\x1bOP"),
        ("f12", b"\x1b[24~"),
        ("ctrl+c", b"\x03"),
        ("ctrl+shift+k", b"\x0b"),
        ("alt+x", b"\x1bx"),
        ("shift+up", b"\x1b[1;2A"),
        ("ctrl+right", b"\x1b[1;5C"),
        ("ctrl+shift+delete", b"\x1b[3;6~"),
        ("ctrl+alt+up", b"\x1b[1;7A"),
        ("unknown-key", b""),
    ],
)
def test_encode(name: str, expected: bytes) -> None:
    assert _encode(key(name)) == expected


def test_encode_prefers_the_printable_character():
    assert _encode(SimpleNamespace(key="exclamation_mark", character="!")) == b"!"
    assert _encode(SimpleNamespace(key="alt+shift+x", character="X")) == b"\x1bX"
    assert _encode(SimpleNamespace(key="ctrl+c", character="\x03")) == b"\x03"


class Harness(App[None]):
    BINDINGS = [Binding("f5", "mark", "Mark")]

    def __init__(self) -> None:
        super().__init__()
        self.dismissed = 0
        self.keystrokes: list[bytes] = []
        self.marks = 0

    def compose(self) -> ComposeResult:
        yield InputBar(id="bar")

    def action_mark(self) -> None:
        self.marks += 1

    @on(InputBar.Dismiss)
    def _dismissed(self, event: InputBar.Dismiss) -> None:
        event.stop()
        self.dismissed += 1

    @on(InputBar.Keystroke)
    def _keystroke(self, event: InputBar.Keystroke) -> None:
        event.stop()
        self.keystrokes.append(event.data)


@pytest.mark.asyncio
async def test_app_shortcuts_reach_the_app_while_the_bar_is_focused() -> None:
    app = Harness()
    async with app.run_test() as pilot:
        app.query_one("#bar", InputBar).focus()
        await pilot.press("f5")
        await pilot.pause()
        assert app.marks == 1


@pytest.mark.asyncio
async def test_line_mode_editing_keys_work() -> None:
    app = Harness()
    async with app.run_test() as pilot:
        bar = app.query_one("#bar", InputBar)
        bar.focus()
        await pilot.press("a", "b", "c")
        await pilot.press("left")
        await pilot.press("backspace")
        await pilot.pause()
        assert bar.value == "ac"


@pytest.mark.asyncio
async def test_escape_leaves_line_mode() -> None:
    app = Harness()
    async with app.run_test() as pilot:
        app.query_one("#bar", InputBar).focus()
        await pilot.press("escape")
        await pilot.pause()
        assert app.dismissed == 1


@pytest.mark.asyncio
async def test_f6_toggles_raw_mode() -> None:
    app = Harness()
    async with app.run_test() as pilot:
        bar = app.query_one("#bar", InputBar)
        bar.focus()
        await pilot.press("f6")
        await pilot.pause()
        assert bar.raw is True
        await pilot.press("f6")
        await pilot.pause()
        assert bar.raw is False


@pytest.mark.asyncio
async def test_raw_mode_forwards_escape_and_ctrl_c() -> None:
    app = Harness()
    async with app.run_test() as pilot:
        bar = app.query_one("#bar", InputBar)
        bar.focus()
        bar.raw = True
        await pilot.press("escape", "ctrl+c")
        await pilot.pause()
        assert app.dismissed == 0  # escape went to the child, not the app
        assert app.keystrokes == [b"\x1b", b"\x03"]
