"""End-to-end tests for the workspace, driven through Textual's pilot.

These exercise the real widgets against real child processes on a real pty.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from textual.widgets import Checkbox, Input, Static

from scriptsuite.app import ScriptSuiteApp
from scriptsuite.discovery import discover
from scriptsuite.runner import RunState
from scriptsuite.screens.help import HelpScreen
from scriptsuite.widgets.args_panel import ArgsPanel, FieldRow
from scriptsuite.widgets.header_bar import HeaderBar
from scriptsuite.widgets.output_pane import OutputPane
from scriptsuite.widgets.source_view import SourceView
from scriptsuite.widgets.status_bar import StatusBar

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A small scripts folder with one of everything."""
    root = tmp_path / "script"
    root.mkdir()

    (root / "hello.py").write_text('"""Says hello."""\nprint("hello world")\n')
    (root / "boom.py").write_text(
        '"""Always fails."""\nimport sys\nprint("before the crash")\nsys.exit(2)\n'
    )
    (root / "checked.py").write_text(
        '"""Checks the terminal."""\nimport sys\nprint("tty", sys.stdout.isatty())\n'
    )
    (root / "echoer.py").write_text(
        '"""Reads two lines."""\n'
        "import sys\n"
        "print('ready')\n"
        "print('one:', sys.stdin.readline().strip())\n"
        "print('two:', sys.stdin.readline().strip())\n"
    )
    (root / "pytestish.py").write_text(
        '"""Fake runner."""\nprint(\'===== 4 passed, 1 failed in 0.12s =====\')\n'
    )
    (root / "args.py").write_text(
        '"""Takes arguments."""\n'
        "import sys\n"
        "first = sys.argv[1] if len(sys.argv) > 1 else ''\n"
        "second = sys.argv[2] if len(sys.argv) > 2 else ''\n"
        "print(f'target={first} arg={second}')\n"
    )
    (root / "options.py").write_text(
        '"""Declares options."""\n'
        "import argparse\n"
        "p = argparse.ArgumentParser(description='Runs the thing.')\n"
        "p.add_argument('target', help='where to go')\n"
        "p.add_argument('-v', '--verbose', action='store_true', help='say more')\n"
        "p.add_argument('--out', metavar='FILE', help='explain what happened')\n"
        "p.parse_args()\n"
    )
    return root


@pytest.fixture
async def app(tree: Path):
    instance = ScriptSuiteApp(scripts_dir=tree)
    async with instance.run_test(size=(150, 44)) as pilot:
        # The sidebar builds its rows in a worker; wait for them.
        for _ in range(40):
            await pilot.pause()
            if instance.query_one("#sidebar-list").rows():
                break
        await pilot.pause()
        yield instance, pilot


def output_text(app) -> str:
    return "\n".join(getattr(line, "text", "") for line in app.query_one("#output").lines)


async def wait_for_idle(app, pilot, limit: int = 200) -> None:
    for _ in range(limit):
        await pilot.pause(0.05)
        if app.active is None:
            break
    await pilot.pause()


async def run_script(app, pilot, name: str) -> None:
    target = next(info for info in app.scripts if info.name == name)
    app.query_one("#sidebar-list").focus_script(target.key)
    await pilot.pause()
    app.action_rerun()
    await wait_for_idle(app, pilot)


# -- framework integration --------------------------------------------------


async def test_app_does_not_shadow_framework_methods():
    # `App.run` is the entry point; an instance attribute of the same name
    # would replace it with None and break `scriptsuite` on launch.
    instance = ScriptSuiteApp(scripts_dir=ROOT / "script")
    assert callable(instance.run)
    assert callable(instance.run_worker)


async def test_cli_entrypoint_is_reachable():
    from scriptsuite.cli import main

    assert callable(main)


async def test_a_relative_scripts_dir_still_runs(tree: Path, monkeypatch):
    """cwd is the scripts folder, so a relative path would double up."""
    monkeypatch.chdir(tree.parent)
    instance = ScriptSuiteApp(scripts_dir=Path("script"))
    assert instance.scripts_dir.is_absolute()

    async with instance.run_test(size=(140, 40)) as pilot:
        for _ in range(40):
            await pilot.pause()
            if instance.query_one("#sidebar-list").rows():
                break
        await run_script(instance, pilot, "hello")
        assert "hello world" in output_text(instance)
        assert "can't open file" not in output_text(instance)


# -- arguments pane ---------------------------------------------------------


def field_rows(app) -> list[FieldRow]:
    return list(app.query_one("#right-pane", ArgsPanel).query(FieldRow))


def field_labels(app) -> list[str]:
    return [row.field.label for row in field_rows(app)]


def panel_text(app) -> str:
    panel = app.query_one("#right-pane", ArgsPanel)
    return " ".join(str(widget.render()) for widget in panel.query("#args-fields *"))


async def test_right_pane_is_the_arguments_panel(app):
    instance, _ = app
    assert instance.query_one("#right-pane") is instance.query_one(ArgsPanel)


async def test_panel_field_is_disabled_when_nothing_is_selected(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    instance = ScriptSuiteApp(scripts_dir=empty)
    async with instance.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert instance.query_one("#args-input", Input).disabled is True


async def test_panel_field_is_enabled_once_a_script_is_highlighted(app):
    instance, pilot = app
    await pilot.pause()
    assert instance.query_one("#args-input", Input).disabled is False


async def test_panel_follows_the_highlighted_script(app):
    instance, pilot = app
    sidebar = instance.query_one("#sidebar-list")
    target = next(i for i in instance.scripts if i.name == "options")
    sidebar.index = sidebar.visible_scripts().index(target)
    await pilot.pause()
    head = instance.query_one("#args-head", Static).render().plain
    assert head.startswith("options.py")


async def test_arguments_are_remembered_per_script(app):
    instance, pilot = app
    sidebar = instance.query_one("#sidebar-list")
    scripts = list(instance.scripts)

    def first_value_field():
        for row in field_rows(instance):
            if row.field.takes_value:
                return row.query_one(Input)
        return None

    sidebar.index = 0
    await pilot.pause()
    field = first_value_field()
    if field is not None:
        field.value = "kept"
        await pilot.pause()

    sidebar.index = 1
    await pilot.pause()
    other = first_value_field()
    if field is not None and other is not None:
        assert other.value == "", "a different script should start empty"

    sidebar.index = 0
    await pilot.pause()
    back = first_value_field()
    if field is not None and back is not None:
        assert back.value == "kept", "returning should restore that script's args"
        assert instance.arguments[scripts[0].key]
        assert instance.arguments.get(scripts[1].key, {}) == {}


async def test_arguments_reach_the_script(app):
    instance, pilot = app
    target = next(i for i in instance.scripts if i.name == "args")
    instance.arguments[target.key] = {"pos0": "one"}
    await run_script(instance, pilot, "args")
    assert "target=one" in output_text(instance)


async def test_arguments_are_split_like_a_shell(app):
    instance, pilot = app
    target = next(i for i in instance.scripts if i.name == "args")
    await run_script(instance, pilot, "args")
    instance.arguments[target.key] = {}
    field = instance.query_one("#args-input", Input)
    field.value = "two 'three four'"
    await pilot.pause()
    instance.action_rerun()
    await wait_for_idle(instance, pilot)
    assert "target=two arg=three four" in output_text(instance)


async def test_banner_shows_the_arguments(app):
    instance, pilot = app
    target = next(i for i in instance.scripts if i.name == "args")
    instance.arguments[target.key] = {"pos0": "host1", "pos1": "--fast"}
    await run_script(instance, pilot, "args")
    assert "$ python args.py host1 --fast" in output_text(instance)


async def test_empty_arguments_run_bare(app):
    instance, pilot = app
    await run_script(instance, pilot, "args")
    assert "target=" in output_text(instance)


async def test_clear_args_action(app):
    instance, pilot = app
    target = next(i for i in instance.scripts if i.name == "args")
    instance.arguments[target.key] = {"pos0": "-v"}
    instance._show_args(target)
    await pilot.pause()
    instance.action_clear_args()
    await pilot.pause()
    assert instance.arguments.get(target.key, {}) == {}
    assert instance.query_one("#args-input", Input).value == ""


async def test_focus_args_jumps_to_the_first_control(app):
    instance, pilot = app
    instance.action_focus_args()
    await pilot.pause()
    rows = field_rows(instance)
    assert rows, "the panel should offer at least one control"
    assert instance.focused in list(rows[0].children)


async def test_focus_args_is_refused_without_a_script(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    instance = ScriptSuiteApp(scripts_dir=empty)
    async with instance.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        instance.action_focus_args()
        await pilot.pause()
        assert instance.focused is not instance.query_one("#args-input", Input)


# -- following a prompt -----------------------------------------------------


@pytest.fixture
def talker(tmp_path: Path) -> Path:
    """A script that prints a prompt without a newline, then reads a line."""
    root = tmp_path / "talk"
    root.mkdir()
    (root / "ask.py").write_text(
        "import sys\n"
        'print("q? ", end="", flush=True)\n'
        "print(sys.stdin.readline().strip())\n"
    )
    return root


async def test_a_prompt_takes_focus(talker: Path):
    instance = ScriptSuiteApp(scripts_dir=talker)
    async with instance.run_test(size=(140, 40)) as pilot:
        for _ in range(40):
            await pilot.pause()
            if instance.query_one("#sidebar-list").rows():
                break
        assert instance.focused is instance.query_one("#sidebar-list")

        instance.action_rerun()
        for _ in range(80):
            await pilot.pause(0.05)
            if instance.active is not None and instance.active.awaiting_input:
                break
        await pilot.pause()
        assert instance.focused is instance.query_one("#stdin-input")


async def test_typing_at_a_prompt_answers_it(talker: Path):
    instance = ScriptSuiteApp(scripts_dir=talker)
    async with instance.run_test(size=(140, 40)) as pilot:
        for _ in range(40):
            await pilot.pause()
            if instance.query_one("#sidebar-list").rows():
                break
        instance.action_rerun()
        for _ in range(80):
            await pilot.pause(0.05)
            if instance.active is not None and instance.active.awaiting_input:
                break
        await pilot.pause()

        bar = instance.query_one("#stdin-input")
        assert bar.has_focus
        bar.value = "ada"
        await pilot.press("enter")
        await wait_for_idle(instance, pilot)
        assert "ada" in output_text(instance)


async def test_a_prompt_does_not_steal_deliberate_focus(talker: Path):
    instance = ScriptSuiteApp(scripts_dir=talker)
    async with instance.run_test(size=(140, 40)) as pilot:
        for _ in range(40):
            await pilot.pause()
            if instance.query_one("#sidebar-list").rows():
                break
        field = instance.query_one("#filter-input")
        field.focus()
        await pilot.pause()

        instance.action_rerun()
        for _ in range(80):
            await pilot.pause(0.05)
            if instance.active is not None and instance.active.awaiting_input:
                break
        await pilot.pause()
        assert instance.focused is field, "the user chose the filter; leave them there"
        instance.action_stop()
        await wait_for_idle(instance, pilot)


async def test_a_progress_bar_is_not_mistaken_for_a_prompt(tmp_path: Path):
    root = tmp_path / "bar"
    root.mkdir()
    (root / "bar.py").write_text(
        "import sys, time\n"
        "for i in range(20):\n"
        "    sys.stdout.write(f'\\r{i*5}%')\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(0.03)\n"
        "print('\\nbar done')\n"
    )
    instance = ScriptSuiteApp(scripts_dir=root)
    async with instance.run_test(size=(140, 40)) as pilot:
        for _ in range(40):
            await pilot.pause()
            if instance.query_one("#sidebar-list").rows():
                break
        instance.action_rerun()
        moved = False
        for _ in range(100):  # ~2s, covering the bar's whole life
            await pilot.pause(0.02)
            if instance.active is None:
                break
            if instance.focused is instance.query_one("#stdin-input"):
                moved = True
                break
        assert not moved, "a repainting progress bar must not grab focus"
        await wait_for_idle(instance, pilot)
        assert "bar done" in output_text(instance)


async def test_focus_returns_to_the_list_when_the_run_ends(talker: Path):
    instance = ScriptSuiteApp(scripts_dir=talker)
    async with instance.run_test(size=(140, 40)) as pilot:
        for _ in range(40):
            await pilot.pause()
            if instance.query_one("#sidebar-list").rows():
                break
        instance.action_rerun()
        for _ in range(80):
            await pilot.pause(0.05)
            if instance.active is not None and instance.active.awaiting_input:
                break
        await pilot.pause()
        assert instance.query_one("#stdin-input").has_focus

        instance.query_one("#stdin-input").value = "x"
        await pilot.press("enter")
        await wait_for_idle(instance, pilot)
        assert instance.focused is instance.query_one("#sidebar-list")


async def test_escape_leaves_the_input_bar(talker: Path):
    instance = ScriptSuiteApp(scripts_dir=talker)
    async with instance.run_test(size=(140, 40)) as pilot:
        for _ in range(40):
            await pilot.pause()
            if instance.query_one("#sidebar-list").rows():
                break
        instance.action_rerun()
        for _ in range(80):
            await pilot.pause(0.05)
            if instance.active is not None and instance.active.awaiting_input:
                break
        await pilot.pause()
        assert instance.query_one("#stdin-input").has_focus

        await pilot.press("escape")
        await pilot.pause()
        assert instance.focused is instance.query_one("#sidebar-list")
        instance.action_stop()
        await wait_for_idle(instance, pilot)


async def test_each_further_prompt_takes_focus_back(tmp_path: Path):
    root = tmp_path / "chat"
    root.mkdir()
    (root / "chat.py").write_text(
        "import sys\n"
        "for label in ('first', 'second'):\n"
        "    sys.stdout.write(f'{label}? ')\n"
        "    sys.stdout.flush()\n"
        "    print(sys.stdin.readline().strip())\n"
    )
    instance = ScriptSuiteApp(scripts_dir=root)
    async with instance.run_test(size=(140, 40)) as pilot:
        for _ in range(40):
            await pilot.pause()
            if instance.query_one("#sidebar-list").rows():
                break
        instance.action_rerun()
        bar = instance.query_one("#stdin-input")

        for answer in ("a", "b"):
            for _ in range(80):
                await pilot.pause(0.05)
                if instance.active is None:
                    break
                if instance.active.awaiting_input and instance.focused is bar:
                    break
            assert instance.focused is bar, f"prompt after {answer!r} did not take focus"
            bar.value = answer
            await pilot.press("enter")
            await pilot.pause(0.15)

        await wait_for_idle(instance, pilot)
        text = output_text(instance)
        assert "a" in text and "b" in text


async def test_a_later_prompt_reclaims_focus_after_escape(talker: Path):
    instance = ScriptSuiteApp(scripts_dir=talker)
    async with instance.run_test(size=(140, 40)) as pilot:
        for _ in range(40):
            await pilot.pause()
            if instance.query_one("#sidebar-list").rows():
                break
        instance.action_rerun()
        for _ in range(80):
            await pilot.pause(0.05)
            if instance.active is not None and instance.active.awaiting_input:
                break
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert instance.focused is instance.query_one("#sidebar-list")

        # Answering resolves the prompt; the next one should pull focus back.
        bar = instance.query_one("#stdin-input")
        bar.value = "first"
        await pilot.press("enter")
        for _ in range(80):
            await pilot.pause(0.05)
            if instance.active is None:
                break
            if instance.active.awaiting_input and instance.focused is bar:
                break
        await pilot.pause()
        assert instance.focused is bar
        instance.action_stop()
        await wait_for_idle(instance, pilot)


async def test_raw_mode_turns_off_with_f6(talker: Path):
    instance = ScriptSuiteApp(scripts_dir=talker)
    async with instance.run_test(size=(140, 40)) as pilot:
        for _ in range(40):
            await pilot.pause()
            if instance.query_one("#sidebar-list").rows():
                break
        instance.action_rerun()
        for _ in range(80):
            await pilot.pause(0.05)
            if instance.active is not None and instance.active.awaiting_input:
                break
        await pilot.pause()
        bar = instance.query_one("#stdin-input")
        bar.raw = True
        await pilot.press("f6")
        await pilot.pause()
        assert bar.raw is False
        instance.action_stop()
        await wait_for_idle(instance, pilot)


# -- discovered options -----------------------------------------------------


async def test_options_are_listed_for_argparse_scripts(app):
    instance, pilot = app
    target = next(i for i in instance.scripts if i.name == "options")
    instance._show_args(target)
    await pilot.pause()
    assert "-v, --verbose" in field_labels(instance)
    assert "--out" in field_labels(instance)


async def test_option_help_is_shown(app):
    instance, pilot = app
    target = next(i for i in instance.scripts if i.name == "options")
    instance._show_args(target)
    await pilot.pause()
    assert "explain what happened" in panel_text(instance)


async def test_script_without_options_says_so(app):
    instance, pilot = app
    target = next(i for i in instance.scripts if i.name == "hello")
    instance._show_args(target)
    await pilot.pause()
    assert field_labels(instance) == []
    assert "no options declared" in panel_text(instance)


async def test_positional_scripts_are_flagged(app):
    instance, pilot = app
    target = next(i for i in instance.scripts if i.name == "args")
    instance._show_args(target)
    await pilot.pause()
    rows = field_rows(instance)
    assert rows, "a sys.argv script should still get positional fields"
    assert all(row.field.positional for row in rows)


# -- per-argument controls --------------------------------------------------


async def test_one_control_per_argument(app):
    instance, pilot = app
    target = next(i for i in instance.scripts if i.name == "options")
    instance._show_args(target)
    await pilot.pause()

    rows = field_rows(instance)
    assert [row.field.token for row in rows] == ["target", "--verbose", "--out"]


async def test_switches_get_a_checkbox_and_values_an_input(app):
    instance, pilot = app
    target = next(i for i in instance.scripts if i.name == "options")
    instance._show_args(target)
    await pilot.pause()

    by_token = {row.field.token: row for row in field_rows(instance)}
    assert by_token["--verbose"].query_one(Checkbox) is not None
    assert by_token["--out"].query_one(Input) is not None
    assert by_token["target"].query_one(Input) is not None


async def test_typing_in_a_field_is_remembered_and_used(app):
    instance, pilot = app
    target = next(i for i in instance.scripts if i.name == "options")
    instance._show_args(target)
    await pilot.pause()

    rows = {row.field.token: row for row in field_rows(instance)}
    rows["target"].query_one(Input).value = "db-9"
    rows["--out"].query_one(Input).value = "report.txt"
    await pilot.pause()

    assert instance.arguments[target.key] == {"pos0": "db-9", "--out": "report.txt"}


async def test_toggling_a_checkbox_is_remembered(app):
    instance, pilot = app
    target = next(i for i in instance.scripts if i.name == "options")
    instance._show_args(target)
    await pilot.pause()

    row = next(r for r in field_rows(instance) if r.field.token == "--verbose")
    row.query_one(Checkbox).value = True
    await pilot.pause()
    assert instance.arguments[target.key] == {"--verbose": True}


async def test_extra_box_is_kept_apart_from_the_fields(app):
    instance, pilot = app
    target = next(i for i in instance.scripts if i.name == "args")
    instance._show_args(target)
    await pilot.pause()
    instance.query_one("#args-input", Input).value = "--extra"
    await pilot.pause()
    assert instance.extra_arguments[target.key] == "--extra"
    assert instance.arguments.get(target.key, {}) == {}


async def test_cmdline_expect_specs_get_controls(tmp_path: Path):
    root = tmp_path / "script"
    root.mkdir()
    (root / "flags.exp").write_text(
        "#!/usr/bin/expect -f\n"
        "package require cmdline\n"
        "set parameters {\n"
        '    {host.arg "" "Target host"}\n'
        '    {debug    "Enable debug output"}\n'
        "}\n"
        "array set arg [cmdline::getoptions argv $parameters]\n"
        'puts "host: $arg(host)"\n'
        'puts "debug: $arg(debug)"\n'
    )
    instance = ScriptSuiteApp(scripts_dir=root)
    async with instance.run_test(size=(150, 44)) as pilot:
        for _ in range(40):
            await pilot.pause()
            if instance.query_one("#sidebar-list").rows():
                break
        target = instance.scripts[0]
        instance._show_args(target)
        await pilot.pause()

        rows = {row.field.token: row for row in field_rows(instance)}
        assert set(rows) == {"--host", "--debug"}
        assert rows["--host"].field.kind == "value"
        assert rows["--debug"].field.kind == "flag"

        rows["--host"].query_one(Input).value = "10.0.0.9"
        rows["--debug"].query_one(Checkbox).value = True
        await pilot.pause()

        instance.action_rerun()
        for _ in range(200):
            await pilot.pause(0.05)
            if instance.active is None:
                break
        await pilot.pause()

        text = output_text(instance)
        assert "--host 10.0.0.9 --debug" in text, text
        assert "host: 10.0.0.9" in text
        assert "debug: 1" in text


# -- layout -----------------------------------------------------------------


async def test_all_four_regions_exist(app):
    instance, _ = app
    assert instance.query_one("#sidebar") is not None
    assert instance.query_one("#output") is not None
    assert instance.query_one("#right-pane", ArgsPanel) is not None
    assert instance.query_one("#bottom-pane", SourceView) is not None
    assert instance.query_one("#status-bar", StatusBar) is not None


async def test_panes_start_visible(app):
    instance, _ = app
    assert instance.query_one("#sidebar").display
    assert instance.query_one("#right-pane").display
    # The source pane starts collapsed on purpose.
    assert not instance.query_one("#bottom-pane").display


async def test_bottom_pane_toggles(app):
    instance, pilot = app
    instance.action_toggle_bottom()
    await pilot.pause()
    assert instance.query_one("#bottom-pane").display
    instance.action_toggle_bottom()
    await pilot.pause()
    assert not instance.query_one("#bottom-pane").display


async def test_expand_opens_the_collapsed_source_pane(app):
    instance, pilot = app
    instance.action_toggle_source_max()
    await pilot.pause()
    assert instance.query_one("#bottom-pane").display
    assert instance.screen.has_class("-source-max")
    instance.action_toggle_source_max()
    await pilot.pause()
    assert not instance.screen.has_class("-source-max")


async def test_bottom_pane_keys_are_adjacent(app):
    instance, _ = app
    status = instance.query_one("#status-bar", StatusBar)
    labels = [key for key, _ in status.key_hints]
    assert labels.index("f8") == labels.index("f7") + 1


async def test_right_pane_toggles(app):
    instance, pilot = app
    instance.action_toggle_right()
    await pilot.pause()
    assert not instance.query_one("#right-pane").display
    instance.action_toggle_right()
    await pilot.pause()
    assert instance.query_one("#right-pane").display


async def test_sidebar_toggles(app):
    instance, pilot = app
    instance.action_toggle_sidebar()
    await pilot.pause()
    assert not instance.query_one("#sidebar").display


async def test_hiding_the_right_pane_widens_the_workspace(app):
    instance, pilot = app
    before = instance.query_one("#output").outer_size.width
    instance.action_toggle_right()
    await pilot.pause()
    assert instance.query_one("#output").outer_size.width > before


@pytest.mark.parametrize(
    ("width", "narrow", "compact"),
    [(150, False, False), (100, True, False), (80, True, True)],
)
async def test_responsive_breakpoints(
    tree: Path, width: int, narrow: bool, compact: bool
):
    instance = ScriptSuiteApp(scripts_dir=tree)
    async with instance.run_test(size=(width, 40)) as pilot:
        await pilot.pause()
        assert instance.screen.has_class("-narrow") is narrow
        assert instance.screen.has_class("-compact") is compact
        # The workspace must never be squeezed to nothing.
        assert instance.query_one("#output").outer_size.width >= 30


async def test_compact_layout_hides_the_right_pane(tree: Path):
    instance = ScriptSuiteApp(scripts_dir=tree)
    async with instance.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        assert not instance.query_one("#right-pane").display
        # ...and F9 brings it back when the user wants it.
        instance.action_toggle_right()
        await pilot.pause()
        assert instance.query_one("#right-pane").display


# -- discovery --------------------------------------------------------------


async def test_scripts_are_discovered_on_mount(app):
    instance, _ = app
    assert {info.name for info in instance.scripts} == {
        "hello",
        "boom",
        "checked",
        "echoer",
        "pytestish",
        "args",
        "options",
    }
    assert instance.query_one("#sidebar-list").visible_scripts()


async def test_rescan_picks_up_a_new_script(app):
    instance, pilot = app
    (instance.scripts_dir / "added.py").write_text('"""Added."""\n')
    instance.action_rescan()
    await pilot.pause()
    await pilot.pause()
    assert any(info.name == "added" for info in instance.scripts)


async def test_rescan_keeps_the_selection_and_run_badges(app):
    instance, pilot = app
    await run_script(instance, pilot, "hello")
    target = next(script for script in instance.scripts if script.name == "hello")
    assert instance.states[target.key] is RunState.PASSED
    instance.query_one("#sidebar-list").focus_script(target.key)
    await pilot.pause()

    (instance.scripts_dir / "zzz.py").write_text('"""Zed."""\nprint("z")\n')
    instance.action_rescan()
    for _ in range(80):
        await pilot.pause(0.05)
        if any(script.name == "zzz" for script in instance.scripts):
            break
    await pilot.pause()

    sidebar = instance.query_one("#sidebar-list")
    assert sidebar.current_info is not None
    assert sidebar.current_info.name == "hello"
    assert instance.states[target.key] is RunState.PASSED
    row = sidebar.row_for(target.key)
    assert row is not None
    assert row.state is RunState.PASSED


async def test_filter_narrows_the_list(app):
    instance, pilot = app
    sidebar = instance.query_one("#sidebar-list")
    instance.query_one("#filter-input").value = "hello"
    await pilot.pause()
    await pilot.pause()
    assert [info.name for info in sidebar.visible_scripts()] == ["hello"]


async def test_single_click_highlights_and_double_click_runs(app):
    instance, pilot = app
    sidebar = instance.query_one("#sidebar-list")
    target = next(script for script in instance.scripts if script.name == "hello")
    row = sidebar.row_for(target.key)
    assert row is not None

    await pilot.click(row, offset=(1, 0))
    await pilot.pause()
    assert sidebar.current_info is not None
    assert sidebar.current_info.key == target.key
    assert instance.states.get(target.key) is None

    await pilot.click(row, offset=(1, 0), times=2)
    await wait_for_idle(instance, pilot)
    assert instance.states.get(target.key) is RunState.PASSED


async def test_j_and_k_move_the_sidebar_cursor(app):
    instance, pilot = app
    sidebar = instance.query_one("#sidebar-list")
    sidebar.focus()
    await pilot.pause()
    start = sidebar.index
    await pilot.press("j")
    await pilot.pause()
    assert sidebar.index == start + 1
    assert instance.current is sidebar.current_info
    await pilot.press("k")
    await pilot.pause()
    assert sidebar.index == start


async def test_filter_clearing_restores_the_list(app):
    instance, pilot = app
    sidebar = instance.query_one("#sidebar-list")
    field = instance.query_one("#filter-input")
    field.value = "zzz-nothing"
    await pilot.pause()
    await pilot.pause()
    assert sidebar.visible_scripts() == []
    field.value = ""
    await pilot.pause()
    await pilot.pause()
    assert len(sidebar.visible_scripts()) == 7


async def test_highlighting_updates_the_header(app):
    instance, pilot = app
    sidebar = instance.query_one("#sidebar-list")
    # Move to something other than the first row so a change is posted.
    sidebar.index = sidebar.visible_scripts().index(
        next(i for i in instance.scripts if i.name == "checked")
    )
    await pilot.pause()
    assert instance.current is not None
    assert instance.current.name == "checked"
    assert instance.query_one(HeaderBar) is not None


async def test_enter_on_the_focused_widget_runs_the_script(app):
    # The list must hold focus at mount, otherwise the first Enter is eaten by
    # the filter field and the run needs two keypresses.
    instance, pilot = app
    assert instance.focused is instance.query_one("#sidebar-list")

    await pilot.press("enter")
    for _ in range(200):
        await pilot.pause(0.05)
        if instance.active is None:
            break
    await pilot.pause()
    assert instance.states, "pressing enter should have started a run"


async def test_filter_takes_focus_on_slash_and_returns_on_enter(app):
    instance, pilot = app
    await pilot.press("/")
    await pilot.pause()
    assert instance.focused is instance.query_one("#filter-input")

    await pilot.press("enter")
    await pilot.pause()
    assert instance.focused is instance.query_one("#sidebar-list")


async def test_focus_stdin_refuses_when_no_run_is_active(app):
    instance, pilot = app
    instance.action_focus_stdin()
    await pilot.pause()
    assert instance.focused is instance.query_one("#sidebar-list")


async def test_focus_stdin_works_once_a_script_is_running(app):
    instance, pilot = app
    await run_script(instance, pilot, "echoer")
    instance.action_focus_stdin()
    await pilot.pause()
    assert instance.focused is instance.query_one("#stdin-input")


async def test_focus_stdin_refuses_when_disabled_mid_run(app):
    instance, pilot = app
    await run_script(instance, pilot, "hello")
    instance.action_focus_stdin()
    await pilot.pause()
    assert instance.focused is instance.query_one("#sidebar-list")


# -- running ----------------------------------------------------------------


async def test_run_streams_output(app):
    instance, pilot = app
    await run_script(instance, pilot, "hello")
    assert "hello world" in output_text(instance)
    target = next(i for i in instance.scripts if i.name == "hello")
    assert instance.states[target.key] is RunState.PASSED


async def test_run_shows_the_command_banner(app):
    instance, pilot = app
    await run_script(instance, pilot, "hello")
    text = output_text(instance)
    assert text.splitlines()[0].startswith("$ ")


async def test_failure_is_recorded(app):
    instance, pilot = app
    await run_script(instance, pilot, "boom")
    target = next(i for i in instance.scripts if i.name == "boom")
    assert instance.states[target.key] is RunState.FAILED
    assert "before the crash" in output_text(instance)


async def test_script_runs_on_a_real_terminal(app):
    instance, pilot = app
    await run_script(instance, pilot, "checked")
    assert "tty True" in output_text(instance)


async def test_parsed_summary_is_stored(app):
    instance, pilot = app
    await run_script(instance, pilot, "pytestish")
    target = next(i for i in instance.scripts if i.name == "pytestish")
    summary = instance.summaries[target.key]
    assert summary.framework == "pytest"
    assert summary.passed == 4
    assert summary.failed == 1
    assert summary.ok is False


async def test_parsed_failure_marks_the_run_failed(app):
    instance, pilot = app
    await run_script(instance, pilot, "pytestish")
    target = next(i for i in instance.scripts if i.name == "pytestish")
    assert instance.states[target.key] is RunState.FAILED


async def test_rerun_clears_previous_output(app):
    instance, pilot = app
    await run_script(instance, pilot, "boom")
    await run_script(instance, pilot, "hello")
    text = output_text(instance)
    assert "hello world" in text
    assert "before the crash" not in text


async def test_stdin_bar_is_disabled_until_a_run_starts(app):
    instance, _ = app
    assert instance.query_one("#stdin-input").disabled is True


async def test_stdin_reaches_the_script(app):
    instance, pilot = app
    await run_script(instance, pilot, "echoer")
    bar = instance.query_one("#stdin-input")
    assert bar.disabled is False

    bar.focus()
    await pilot.pause()
    for value in ("alpha", "beta"):
        bar.value = value
        await pilot.press("enter")
        await pilot.pause()
    await wait_for_idle(instance, pilot)

    text = output_text(instance)
    assert "one: alpha" in text
    assert "two: beta" in text


async def test_stdin_disabled_again_after_the_run(app):
    instance, pilot = app
    await run_script(instance, pilot, "hello")
    assert instance.query_one("#stdin-input").disabled is True


async def test_run_can_be_stopped(tmp_path: Path):
    root = tmp_path / "stopper"
    root.mkdir()
    (root / "sleeper.py").write_text(
        '"""Sleeps."""\nimport time\nprint("going")\ntime.sleep(120)\n'
    )
    instance = ScriptSuiteApp(scripts_dir=root)
    async with instance.run_test(size=(120, 40)) as pilot:
        for _ in range(40):
            await pilot.pause()
            if instance.query_one("#sidebar-list").rows():
                break
        instance.action_rerun()
        for _ in range(100):
            await pilot.pause(0.05)
            if instance.active is not None and instance.active.running:
                break
        assert instance.active is not None and instance.active.running
        instance.action_stop()
        await wait_for_idle(instance, pilot)

        target = next(i for i in instance.scripts if i.name == "sleeper")
        assert instance.states[target.key] is RunState.ABORTED
        assert "going" in output_text(instance)


# -- suite ------------------------------------------------------------------


@pytest.fixture
def batch_tree(tmp_path: Path) -> Path:
    """Scripts folder for run-all: nothing here waits for stdin."""
    root = tmp_path / "batch"
    root.mkdir()
    for name, body in {
        "one": '"""One."""\nprint("one")\n',
        "two": '"""Two."""\nimport sys\nsys.exit(1)\n',
        "three": '"""Three."""\nprint("three")\n',
    }.items():
        (root / f"{name}.py").write_text(body)
    return root


async def test_run_all_walks_every_script(batch_tree: Path):
    instance = ScriptSuiteApp(scripts_dir=batch_tree)
    async with instance.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        instance.action_run_all()
        for _ in range(300):
            await pilot.pause(0.05)
            if not instance.suite_running and instance.active is None:
                break
        await pilot.pause(0.2)

        assert len(instance.states) == 3
        assert all(state is not RunState.IDLE for state in instance.states.values())
        states = {i.name: instance.states[i.key] for i in instance.scripts}
        assert states == {
            "one": RunState.PASSED,
            "two": RunState.FAILED,
            "three": RunState.PASSED,
        }


async def test_run_all_only_touches_visible_scripts(app):
    instance, pilot = app
    field = instance.query_one("#filter-input")
    field.value = "hello"
    await pilot.pause()
    await pilot.pause()

    instance.action_run_all()
    for _ in range(300):
        await pilot.pause(0.05)
        if not instance.suite_running and instance.active is None:
            break
    await pilot.pause(0.2)

    assert len(instance.states) == 1
    target = next(i for i in instance.scripts if i.name == "hello")
    assert instance.states.get(target.key) is RunState.PASSED


async def test_cancelling_a_suite_does_not_start_the_next_script(tmp_path: Path):
    root = tmp_path / "mixed"
    root.mkdir()
    (root / "a_quick.py").write_text('"""Quick."""\nprint("done")\n')
    (root / "b_slow.py").write_text('"""Slow."""\nimport time\ntime.sleep(120)\n')
    (root / "c_after.py").write_text('"""After."""\nprint("after")\n')

    instance = ScriptSuiteApp(scripts_dir=root)
    async with instance.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        instance.action_run_all()
        for _ in range(300):
            await pilot.pause(0.05)
            if instance.active is not None and instance.active.key.endswith("b_slow.py"):
                break
        instance.action_cancel_suite()
        for _ in range(300):
            await pilot.pause(0.05)
            if not instance.suite_running and instance.active is None:
                break
        await pilot.pause(0.2)

        assert instance.suite_running is False
        states = {i.name: instance.states.get(i.key) for i in instance.scripts}
        assert states["b_slow"] is RunState.ABORTED
        assert states.get("c_after") is None


async def test_stopping_a_script_advances_the_suite(tmp_path: Path):
    root = tmp_path / "mixed"
    root.mkdir()
    (root / "a_quick.py").write_text('"""Quick."""\nprint("done")\n')
    (root / "b_slow.py").write_text('"""Slow."""\nimport time\ntime.sleep(120)\n')
    (root / "c_after.py").write_text('"""After."""\nprint("after")\n')

    instance = ScriptSuiteApp(scripts_dir=root)
    async with instance.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        instance.action_run_all()
        for _ in range(300):
            await pilot.pause(0.05)
            if instance.active is not None and instance.active.key.endswith("b_slow.py"):
                break
        instance.action_stop()
        for _ in range(300):
            await pilot.pause(0.05)
            if not instance.suite_running and instance.active is None:
                break
        await pilot.pause(0.2)

        states = {i.name: instance.states.get(i.key) for i in instance.scripts}
        assert states["a_quick"] is RunState.PASSED
        assert states["b_slow"] is RunState.ABORTED
        assert states["c_after"] is RunState.PASSED


# -- output pane ------------------------------------------------------------


async def test_clear_output_empties_the_pane(app):
    instance, pilot = app
    await run_script(instance, pilot, "hello")
    assert instance.query_one(OutputPane).lines
    instance.action_clear_output()
    await pilot.pause()
    assert not instance.query_one(OutputPane).lines


async def test_output_follows_the_tail(app):
    instance, pilot = app
    pane = instance.query_one("#output", OutputPane)

    from rich.text import Text

    pane.write_text(Text("\n".join(f"line {n}" for n in range(200)) + "\n"))
    await pilot.pause()
    assert pane.follow is True

    pane.scroll_home()
    await pilot.pause()
    assert pane.follow is False

    pane.action_follow()
    assert pane.follow is True


async def test_output_does_not_yank_the_view_while_scrolled_up(app):
    instance, pilot = app
    from rich.text import Text

    pane = instance.query_one("#output", OutputPane)
    pane.write_text(Text("\n".join(f"line {n}" for n in range(200)) + "\n"))
    await pilot.pause()
    # Detach explicitly: RichLog may still have a queued auto-scroll.
    pane.set_reactive(OutputPane.follow, False)
    await pilot.pause()

    before = pane.scroll_offset.y
    pane.write_text(Text("something new at the bottom\n"))
    await pilot.pause()
    assert pane.follow is False
    assert pane.scroll_offset.y == before, "auto-scroll must yield to the reader"


# -- help -------------------------------------------------------------------


async def test_help_overlay_opens_and_closes(app):
    instance, pilot = app
    await pilot.press("f1")
    await pilot.pause()
    assert isinstance(instance.screen, HelpScreen)
    await pilot.press("escape")
    await pilot.pause()
    assert not isinstance(instance.screen, HelpScreen)


# -- missing folder ---------------------------------------------------------


async def test_missing_scripts_folder_is_explained(tmp_path: Path):
    instance = ScriptSuiteApp(scripts_dir=tmp_path / "nope")
    async with instance.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        text = output_text(instance)
        assert "scripts folder not found" in text
        assert "--scripts-dir" in text


# -- environment ------------------------------------------------------------


async def test_scripts_run_from_the_scripts_folder(app):
    instance, pilot = app
    # Relative paths in the banner prove cwd is the scripts folder.
    await run_script(instance, pilot, "hello")
    assert "hello.py" in output_text(instance)


async def test_default_python_is_this_interpreter(tree: Path):
    found = discover(tree)
    hello = next(info for info in found if info.name == "hello")
    assert hello.command[0] == sys.executable
