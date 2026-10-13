"""Unit tests for the read-only source pane."""

from __future__ import annotations

from pathlib import Path

import pytest

from scriptsuite.widgets.source_view import (
    MAX_BYTES,
    MAX_LINES,
    SourceView,
    _lexer_for,
    _read,
)


def info_for(path: Path, language: str):
    from scriptsuite.discovery import ScriptInfo

    stat = path.stat()
    return ScriptInfo(
        name=path.stem,
        path=path,
        root=path.parent,
        language=language,
        description="",
        command=(path,),
        is_test=False,
        executable=False,
        mtime=stat.st_mtime,
        size=stat.st_size,
    )


# -- reading ----------------------------------------------------------------


def test_reads_the_whole_file(tmp_path: Path):
    path = tmp_path / "a.py"
    path.write_text("one\ntwo\nthree\n")
    source, note = _read(info_for(path, "python"))
    assert source == "one\ntwo\nthree\n"
    assert note == ""


def test_missing_file_is_reported_not_raised(tmp_path: Path):
    from scriptsuite.discovery import ScriptInfo

    missing = ScriptInfo(
        name="gone",
        path=tmp_path / "gone.py",
        root=tmp_path,
        language="python",
        description="",
        command=("gone.py",),
        is_test=False,
        executable=False,
        mtime=0.0,
        size=0,
    )
    source, note = _read(missing)
    assert source == ""
    assert "could not read" in note


def test_long_files_are_truncated(tmp_path: Path):
    path = tmp_path / "big.py"
    path.write_text("x = 1\n" * (MAX_LINES + 50))
    source, note = _read(info_for(path, "python"))
    assert len(source.splitlines()) == MAX_LINES
    assert "first" in note


def test_oversize_files_are_truncated(tmp_path: Path):
    path = tmp_path / "huge.py"
    path.write_text("#" * (MAX_BYTES + 1000))
    source, note = _read(info_for(path, "python"))
    assert len(source) <= MAX_BYTES
    assert "too large" in note


# -- lexers -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("language", "lexer"),
    [
        ("python", "python"),
        ("shell", "bash"),
        ("expect", "tcl"),
        ("tcl", "tcl"),
        ("ruby", "ruby"),
        ("node", "javascript"),
    ],
)
def test_language_maps_to_a_lexer(language: str, lexer: str, tmp_path: Path):
    path = tmp_path / "x"
    path.write_text("")
    assert _lexer_for(info_for(path, language)) == lexer


def test_unknown_language_falls_back_to_guessing(tmp_path: Path):
    path = tmp_path / "x.py"
    path.write_text("")
    assert _lexer_for(info_for(path, "mystery")) == "python"


def test_unguessable_file_falls_back_to_text(tmp_path: Path):
    path = tmp_path / "x.qqq"
    path.write_text("")
    assert _lexer_for(info_for(path, "mystery")) == "default"


# -- rendering --------------------------------------------------------------


async def test_shows_the_selected_script_source(tmp_path: Path):
    from textual.app import App, ComposeResult

    path = tmp_path / "a.py"
    path.write_text("def hello():\n    return 'hi'\n")

    class T(App):
        def compose(self) -> ComposeResult:
            yield SourceView(id="bottom-pane")

    app = T()
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        view = app.query_one(SourceView)
        view.show(info_for(path, "python"))
        await pilot.pause()
        head = view.query_one("#source-head").render().plain
        assert "a.py" in head
        assert "python" in head
        assert "3 lines" in head or "2 lines" in head


async def test_no_selection_says_so():
    from textual.app import App, ComposeResult

    class T(App):
        def compose(self) -> ComposeResult:
            yield SourceView(id="bottom-pane")

    app = T()
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        view = app.query_one(SourceView)
        view.show(None)
        await pilot.pause()
        assert "no script selected" in view.query_one("#source-head").render().plain


def test_code_block_is_a_styled_rich_renderable(tmp_path: Path):
    """The pane is handed a Rich renderable, not a flat string."""
    from rich.syntax import Syntax

    path = tmp_path / "a.py"
    source = "import argparse\n\n\ndef go():\n    return 1\n"
    path.write_text(source)

    block = SourceView()._render_source(info_for(path, "python"), source, "")
    assert isinstance(block, Syntax), f"expected a Syntax block, got {type(block)}"
    assert block.lexer.name == "Python"


async def test_the_source_reaches_the_screen(tmp_path: Path):
    from textual.app import App, ComposeResult

    path = tmp_path / "a.py"
    path.write_text("UNIQUE_SOURCE_MARKER = 1\n")

    class T(App):
        def compose(self) -> ComposeResult:
            yield SourceView(id="bottom-pane")

    app = T()
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        app.query_one(SourceView).show(info_for(path, "python"))
        await pilot.pause()
        text = "\n".join(strip.text for strip in app.screen._compositor.render_strips())
        assert "UNIQUE_SOURCE_MARKER" in text


def test_syntax_renderable_actually_produces_colour(tmp_path: Path):
    """Rendering the block must emit styles, not one flat run of text."""
    import io
    import re

    from rich.console import Console

    path = tmp_path / "a.py"
    path.write_text("import argparse\n\n\ndef go():\n    return 1\n")
    info = info_for(path, "python")

    view = SourceView()
    block = view._render_source(info, path.read_text(), "")

    buffer = io.StringIO()
    Console(file=buffer, force_terminal=True, width=100, color_system="truecolor").print(
        block
    )
    codes = set(re.findall(r"\x1b\[([0-9;]*)m", buffer.getvalue()))
    assert len(codes) > 3, f"expected highlighted output, saw {codes}"


async def test_truncation_is_visible_in_the_heading(tmp_path: Path):
    from textual.app import App, ComposeResult

    path = tmp_path / "big.py"
    path.write_text("x = 1\n" * (MAX_LINES + 10))

    class T(App):
        def compose(self) -> ComposeResult:
            yield SourceView(id="bottom-pane")

    app = T()
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        view = app.query_one(SourceView)
        view.show(info_for(path, "python"))
        await pilot.pause()
        head = view.query_one("#source-head").render().plain
        assert "first" in head


async def test_switching_scripts_replaces_the_content(tmp_path: Path):
    from textual.app import App, ComposeResult

    first = tmp_path / "one.py"
    first.write_text("FIRST_MARKER\n")
    second = tmp_path / "two.py"
    second.write_text("SECOND_MARKER\n")

    class T(App):
        def compose(self) -> ComposeResult:
            yield SourceView(id="bottom-pane")

    app = T()
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        view = app.query_one(SourceView)
        view.show(info_for(first, "python"))
        await pilot.pause()
        assert view.query_one("#source-head").render().plain.startswith("one.py")

        view.show(info_for(second, "python"))
        await pilot.pause()
        assert view.query_one("#source-head").render().plain.startswith("two.py")


async def test_the_view_is_read_only():
    """No editable widget is ever mounted in the pane."""
    from textual.app import App, ComposeResult
    from textual.widgets import Input

    class T(App):
        def compose(self) -> ComposeResult:
            yield SourceView(id="bottom-pane")

    app = T()
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        view = app.query_one(SourceView)
        assert list(view.query(Input)) == []


async def test_escape_asks_for_focus_back():
    from textual.app import App, ComposeResult

    seen: list[SourceView.Dismiss] = []

    class T(App):
        def compose(self) -> ComposeResult:
            yield SourceView(id="bottom-pane")

        def on_source_view_dismiss(self, event: SourceView.Dismiss) -> None:
            seen.append(event)

    app = T()
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        app.query_one(SourceView).action_release()
        await pilot.pause()
        assert seen
