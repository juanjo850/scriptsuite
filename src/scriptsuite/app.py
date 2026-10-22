"""The scriptsuite workspace: sidebar, output, reserved panes."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Input

from .discovery import ScriptInfo, discover, known_extensions, resolve_scripts_dir
from .options import ProgramInfo, build_arguments, describe
from .results import ResultParser, TestSummary
from .runner import RunState, ScriptRun
from .screens.help import HelpScreen
from .widgets.args_panel import ArgsPanel, FieldRow
from .widgets.header_bar import HeaderBar
from .widgets.input_bar import InputBar
from .widgets.output_pane import OutputPane
from .widgets.script_list import ScriptActivated, ScriptList, SidebarHeader
from .widgets.source_view import SourceView
from .widgets.status_bar import StatusBar

#: Output is buffered and flushed on this cadence so a chatty script does not
#: trigger a repaint per read.
FLUSH_INTERVAL = 1 / 25
#: Force an early flush once this much text is waiting.
FLUSH_CHARS = 48_000
#: How often the elapsed-time readout refreshes while a run is active.
TICK_INTERVAL = 0.25

#: Below these terminal widths the layout tightens, so the workspace keeps
#: enough room to stay usable on an 80-column terminal.
NARROW_WIDTH = 112
COMPACT_WIDTH = 92

_COLOURS = {
    RunState.PASSED: "#9ece6a",
    RunState.FAILED: "#f7768e",
    RunState.ABORTED: "#e0af68",
    RunState.RUNNING: "#7aa2f7",
    RunState.IDLE: "",
}

#: Tokyo Night-ish palette; keeps the chrome calm so script output stands out.
PALETTE = {
    "primary": "#7aa2f7",
    "secondary": "#bb9af7",
    "accent": "#7aa2f7",
    "foreground": "#c0caf5",
    "background": "#11111b",
    "surface": "#1a1b26",
    "panel": "#16161e",
    "boost": "#24283b",
    "text": "#c0caf5",
    "text-muted": "#8b93b8",
    "border": "#2f344d",
    "success": "#9ece6a",
    "warning": "#e0af68",
    "error": "#f7768e",
    "scrollbar": "#2f344d",
    "scrollbar-hover": "#3b4261",
    "scrollbar-active": "#7aa2f7",
}


def _display_command(info: ScriptInfo, argv: Sequence[str]) -> str:
    """A runnable-looking command line: short interpreter, relative script."""
    parts = [Path(argv[0]).name]
    for argument in argv[1:]:
        parts.append(argument.replace(str(info.path), info.rel_path))
    return " ".join(parts)


class ScriptSuiteApp(App[None]):
    """A four-pane workspace for driving scripts in real time."""

    CSS_PATH = "styles.tcss"
    TITLE = "scriptsuite"
    SUB_TITLE = "test script workspace"

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+r", "rerun", "Re-run", show=True),
        Binding("ctrl+k", "stop", "Stop", show=True),
        Binding("ctrl+c", "interrupt", "Interrupt", show=True),
        Binding("ctrl+shift+k", "cancel_suite", "Cancel suite", show=False),
        Binding("ctrl+l", "clear_output", "Clear", show=True),
        Binding("ctrl+a", "run_all", "Run all", show=False),
        Binding("f1", "help", "Keys", show=True, priority=True),
        Binding("f2", "focus_stdin", "Input", show=False),
        Binding("f3", "focus_args", "Args", show=False),
        Binding("ctrl+u", "clear_args", "Clear args", show=False),
        Binding("f5", "rescan", "Rescan", show=True),
        Binding("f6", "toggle_raw", "Raw", show=False),
        Binding("f7", "toggle_source_max", "Expand", show=False),
        Binding("f8", "toggle_bottom", "Source", show=False),
        Binding("f9", "toggle_right", "Right", show=False),
        Binding("f11", "toggle_sidebar", "Sidebar", show=False),
        Binding("escape", "clear_filter", "Clear filter", show=False),
    ]

    def __init__(
        self,
        scripts_dir: Path | None = None,
        python: str | None = None,
        recursive: bool = True,
    ) -> None:
        super().__init__()
        # Absolute, always: the child runs with cwd set to the scripts folder,
        # so a relative path here would be resolved a second time against it.
        self.scripts_dir: Path = (
            (scripts_dir or resolve_scripts_dir()).expanduser().resolve()
        )
        self.python_override = python
        self.recursive = recursive

        self.scripts: list[ScriptInfo] = []
        self.current: ScriptInfo | None = None
        #: The run in flight.  Named `active`, not `run`, because `App.run` is
        #: the framework entry point and an attribute would shadow it.
        self.active: ScriptRun | None = None
        self.parser = ResultParser()

        #: Last known state and summary per script key, shown in the sidebar.
        self.states: dict[str, RunState] = {}
        self.summaries: dict[str, TestSummary] = {}

        #: Per script: the argument controls' values, remembered as the
        #: selection moves, plus whatever was left in the free-form box.
        self.arguments: dict[str, dict[str, str | bool]] = {}
        self.extra_arguments: dict[str, str] = {}

        self._pending = Text()
        self._pending_chars = 0
        #: Set when the user leaves the input bar on purpose, so a still-waiting
        #: prompt does not immediately drag focus back.
        self._input_left = False
        self._queue: list[ScriptInfo] = []
        self._suite: list[ScriptInfo] = []
        self._suite_active = False
        self._suite_cancelled = False
        self._generation = 0
        #: Grace timer that escalates a stop to SIGKILL.
        self._kill_timer: Any = None

        # Widget handles, resolved once on mount to keep the hot path cheap.
        self._sidebar: ScriptList | None = None
        self._filter: Input | None = None
        self._stdin: InputBar | None = None
        self._output: OutputPane | None = None
        self._header: HeaderBar | None = None
        self._status: StatusBar | None = None
        self._sidebar_header: SidebarHeader | None = None
        self._args: ArgsPanel | None = None
        self._source: SourceView | None = None

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def get_css_variables(self) -> dict[str, str]:
        """Layer a low-contrast, cool palette over the active theme."""
        return {**super().get_css_variables(), **PALETTE}

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="workspace"):
            with Vertical(id="sidebar"):
                yield SidebarHeader()
                yield Input(placeholder="filter…", id="filter-input", compact=True)
                yield ScriptList(id="sidebar-list")

            with Vertical(id="center-col"):
                yield HeaderBar()
                yield OutputPane(id="output")
                yield InputBar(id="stdin-input")
                yield SourceView(id="bottom-pane")

            yield ArgsPanel(id="right-pane")

        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        self._sidebar = self.query_one("#sidebar-list", ScriptList)
        self._filter = self.query_one("#filter-input", Input)
        self._stdin = self.query_one("#stdin-input", InputBar)
        self._output = self.query_one("#output", OutputPane)
        self._header = self.query_one(HeaderBar)
        self._status = self.query_one("#status-bar", StatusBar)
        self._sidebar_header = self.query_one(SidebarHeader)
        self._args = self.query_one("#right-pane", ArgsPanel)
        self._source = self.query_one("#bottom-pane", SourceView)
        self._sidebar.state_for = self._row_state

        self.set_interval(FLUSH_INTERVAL, self._flush_output)
        self.set_interval(TICK_INTERVAL, self._tick)

        self._apply_responsive_classes()
        self._set_stdin_enabled(False, None)
        self._set_status_keys()
        self._update_header()
        self._show_args(None)
        self._show_source(None)

        if self.scripts_dir.is_dir():
            self.action_rescan()
        else:
            self._show_missing_dir()

        # The list is the primary target: Enter must run, not submit a filter.
        self.set_focus(self._sidebar)

    def _show_missing_dir(self) -> None:
        text = Text()
        text.append("scripts folder not found\n\n", style="bold #f7768e")
        text.append("  looked in  ", style="grey58")
        text.append(f"{self.scripts_dir}\n\n", style="bold")
        text.append("create it, or point somewhere else:\n\n", style="grey70")
        text.append("  mkdir -p script\n", style="#7aa2f7")
        text.append("  scriptsuite --scripts-dir path/to/scripts\n", style="#7aa2f7")
        self._output.write_text(text)  # type: ignore[union-attr]
        self._set_message("scripts folder missing", "#f7768e")

    def _show_empty_dir(self, rejected: list[str]) -> None:
        """Explain an empty sidebar: where it looked, and what it accepts."""
        text = Text()
        text.append(
            f"no runnable scripts in {self.scripts_dir}\n\n", style="bold #e0af68"
        )

        text.append("point it somewhere else:\n\n", style="grey70")
        text.append("  scriptsuite --scripts-dir path/to/scripts\n", style="#7aa2f7")
        text.append(
            "  SCRIPTSUITE_SCRIPTS_DIR=path/to/scripts scriptsuite\n\n", style="#7aa2f7"
        )

        text.append("a file is listed when it has a known extension\n", style="grey70")
        text.append(f"({known_extensions()})\n", style="grey58")
        text.append("or a #! shebang, whatever the extension.\n\n", style="grey70")

        if rejected:
            shown = ", ".join(rejected[:6])
            more = f" (+{len(rejected) - 6} more)" if len(rejected) > 6 else ""
            text.append("skipped, no shebang:  ", style="grey58")
            text.append(f"{shown}{more}\n", style="grey42")
        self._output.write_text(text)  # type: ignore[union-attr]

    def _clear_output(self) -> None:
        if self._output is not None:
            self._output.clear()

    # ------------------------------------------------------------------
    # Arguments pane
    # ------------------------------------------------------------------

    def _show_args(self, info: ScriptInfo | None) -> None:
        """Point the right pane at ``info``, restoring its field values."""
        if self._args is None:
            return
        key = info.key if info else None
        self._args.show(
            info,
            self.arguments.get(key, {}) if key else {},
            self.extra_arguments.get(key, "") if key else "",
            self._program_info(info),
        )

    def _program_info(self, info: ScriptInfo | None) -> ProgramInfo:
        if info is None:
            return ProgramInfo(language="")
        return describe(info.path, info.language)

    def _argv_for(self, info: ScriptInfo) -> tuple[tuple[str, ...], str | None]:
        """The full command for ``info``, including its remembered arguments."""
        tokens, problem = build_arguments(
            self._program_info(info),
            self.arguments.get(info.key, {}),
            self.extra_arguments.get(info.key, ""),
        )
        return (*info.command, *tokens), problem

    def _show_source(self, info: ScriptInfo | None) -> None:
        """Point the bottom pane at ``info``'s source."""
        if self._source is not None:
            self._source.show(info)

    @on(SourceView.Dismiss)
    def _on_source_dismissed(self, event: SourceView.Dismiss) -> None:
        event.stop()
        if self._sidebar is not None:
            self._sidebar.focus()

    @on(ArgsPanel.Dismiss)
    def _on_args_dismissed(self, event: ArgsPanel.Dismiss) -> None:
        """Escape in the arguments field hands focus back to the list."""
        event.stop()
        if self._sidebar is not None:
            self._sidebar.focus()

    @on(ArgsPanel.ValueChanged)
    def _on_arg_field_changed(self, event: ArgsPanel.ValueChanged) -> None:
        """A field or checkbox in the pane was edited."""
        event.stop()
        key = event.script_key
        if key and self._args is not None and self._args.key == key:
            self.arguments.setdefault(key, {})[event.field_id] = event.value

    @on(Input.Changed, "#args-input")
    def _on_extra_changed(self, event: Input.Changed) -> None:
        event.stop()
        if self._args is not None and self._args.key:
            self.extra_arguments[self._args.key] = event.value

    def action_focus_args(self) -> None:
        """Jump to the first argument control for the highlighted script."""
        if self._args is None:
            return
        right = self.query_one("#right-pane")
        if not right.display or self.screen.has_class("-compact"):
            self._set_message("right pane is hidden · f9 to show it", "#e0af68")
            return
        rows = self._args.query(FieldRow)
        if rows:
            # The checkbox in a flag row lives inside `.flagline`, so look
            # recursively rather than only at direct children.
            for control in rows[0].query("Input, Checkbox"):
                if control.can_focus:
                    control.focus()
                    return
            for control in rows[0].children:
                if control.can_focus:
                    control.focus()
                    return
        field = self._args.query_one("#args-input", Input)
        if field.disabled:
            self._set_message("select a script first", "#e0af68")
            return
        field.focus()
        field.cursor_position = len(field.value)

    def action_clear_args(self) -> None:
        """Forget every argument for the highlighted script."""
        if self._args is None:
            return
        if self._args.key:
            self.arguments.pop(self._args.key, None)
            self.extra_arguments.pop(self._args.key, None)
        self._show_args(self.current)
        self._set_message("arguments cleared")

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def action_rescan(self) -> None:
        """Re-scan the scripts folder in the background and refresh the sidebar."""
        self._set_message("scanning…")
        self.run_worker(self._rescan(), name="scanner", group="scan", exclusive=True)

    async def _rescan(self) -> None:
        skipped: list[str] = []
        found = await asyncio.to_thread(
            discover,
            self.scripts_dir,
            self.python_override,
            self.recursive,
            skipped,
        )
        self.scripts = found
        if self._sidebar is not None:
            self._sidebar.set_scripts(found)
        self._update_counts()

        if not found:
            self._show_empty_dir(skipped)
            self._set_message(f"no runnable scripts in {self.scripts_dir}", "#e0af68")
        else:
            self._set_message(f"{len(found)} script(s) · {self.scripts_dir}")

        if self.current is not None:
            previous = self.current.key
            self.current = next((info for info in found if info.key == previous), None)
        if self.current is None and found:
            self.current = found[0]

        self._show_source(self.current)
        self._show_args(self.current)

    def _row_state(self, key: str) -> tuple[RunState, str | None]:
        summary = self.summaries.get(key)
        return (
            self.states.get(key, RunState.IDLE),
            summary.summary_line() if summary else None,
        )

    def _update_counts(self) -> None:
        assert self._sidebar is not None and self._sidebar_header is not None
        self._sidebar_header.set_count(
            len(self._sidebar.visible_scripts()), len(self.scripts)
        )

    # ------------------------------------------------------------------
    # Running
    # ------------------------------------------------------------------

    @on(ScriptActivated)
    def _on_script_activated(self, event: ScriptActivated) -> None:
        event.stop()
        self.action_rerun()

    @on(Input.Changed, "#filter-input")
    def _on_filter_changed(self, event: Input.Changed) -> None:
        event.stop()
        if self._sidebar is not None:
            self._sidebar.filter_text = event.value
        self._update_counts()

    @on(Input.Submitted, "#filter-input")
    def _on_filter_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._sidebar.focus()  # type: ignore[union-attr]

    def action_clear_filter(self) -> None:
        """Escape in the filter box clears it instead of doing nothing."""
        if self._filter is None or not self._filter.has_focus:
            return
        if self._filter.value:
            self._filter.value = ""
        if self._sidebar is not None:
            self._sidebar.focus()

    @on(InputBar.Dismiss)
    def _on_stdin_dismissed(self, event: InputBar.Dismiss) -> None:
        """Escape from the input bar hands focus back to the list.

        The prompt is still waiting, so remember the choice: without this the
        next flush tick would pull focus straight back.
        """
        event.stop()
        self._input_left = True
        if self._stdin is not None and self._stdin.has_focus:
            self._stdin.blur()
        if self._sidebar is not None:
            self._sidebar.focus()

    @on(InputBar.Submitted)
    def _on_stdin_submitted(self, event: InputBar.Submitted) -> None:
        event.stop()
        if self.active is None or not self.active.running:
            self._set_message("nothing is running", "#e0af68")
            return
        self.active.write(event.data)
        self._set_message(f"sent · {event.text}" if event.text else "sent empty line")

    @on(InputBar.Keystroke)
    def _on_stdin_keystroke(self, event: InputBar.Keystroke) -> None:
        event.stop()
        if self.active is not None and self.active.running and event.data:
            self.active.write(event.data)

    @on(InputBar.Interrupt)
    def _on_stdin_interrupt(self, event: InputBar.Interrupt) -> None:
        event.stop()
        self.action_interrupt()

    def notify_raw_mode(self, raw: bool) -> None:
        self._set_message(
            "raw input · keystrokes go straight to the script" if raw else "line input",
            "#e0af68" if raw else "",
        )

    def action_rerun(self) -> None:
        info = self._selected()
        if info is None:
            self._set_message("no script selected", "#e0af68")
            return
        # A manual run replaces the suite rather than leaving a stale queue
        # that would resume later.
        self._cancel_suite(quiet=True)
        self._start(info)

    def action_run_all(self) -> None:
        """Run every visible script in sequence, then report the totals."""
        assert self._sidebar is not None
        queue = self._sidebar.visible_scripts()
        if not queue:
            # An empty filter result means "nothing visible", not "everything".
            self._set_message("nothing visible to run", "#e0af68")
            return
        self._cancel_kill_timer()
        if self.active is not None and not self.active.done:
            self.active.terminate(force=True)
            self.active.close()
            self.active = None
        self._suite = list(queue)
        self._queue = list(queue)
        self._suite_active = True
        self._suite_cancelled = False
        self._set_message(f"suite · 1/{len(self._queue)}", "#7aa2f7")
        self._run_next_in_queue()

    def _run_next_in_queue(self) -> None:
        if self._suite_cancelled:
            self._finish_suite(cancelled=True)
            return
        if not self._queue:
            self._finish_suite()
            return
        info = self._queue.pop(0)
        total = len(self._suite)
        done = total - len(self._queue)
        self._start(info, on_done=self._run_next_in_queue)
        self._set_message(f"suite · {done}/{total} · {info.name}", "#7aa2f7")

    @property
    def suite_running(self) -> bool:
        """True while a run-all sequence is in progress."""
        return self._suite_active

    def _cancel_suite(self, quiet: bool = False) -> None:
        """Stop a run-all sequence without starting the next script."""
        was_active = self._suite_active
        self._suite_active = False
        self._suite_cancelled = True
        self._queue.clear()
        if was_active and not quiet:
            self._set_message("suite cancelled", "#e0af68")

    def _finish_suite(self, cancelled: bool = False) -> None:
        passed = sum(
            1 for info in self._suite if self.states.get(info.key) is RunState.PASSED
        )
        failed = sum(
            1 for info in self._suite if self.states.get(info.key) is RunState.FAILED
        )
        other = len(self._suite) - passed - failed
        colour = "#9ece6a" if failed == 0 else "#f7768e"
        suffix = f" · {other} not run" if other else ""
        prefix = "suite cancelled" if cancelled else "suite finished"
        self._suite_active = False
        self._queue.clear()
        self._set_message(f"{prefix} · {passed} passed · {failed} failed{suffix}", colour)

    def _selected(self) -> ScriptInfo | None:
        if self._sidebar is not None:
            info = self._sidebar.current_info
            if info is not None:
                return info
        return self.current

    def _start(self, info: ScriptInfo, on_done: Any = None) -> None:
        """Begin a run of ``info``, replacing whatever was running."""
        self.current = info
        if self._sidebar is not None:
            self._sidebar.focus_script(info.key)

        # Retire any in-flight run so its callback cannot clobber this state.
        self._generation += 1
        generation = self._generation
        previous = self.active
        # Detach first: callbacks from the old run are ignored from here on.
        self.active = None
        self._cancel_kill_timer()
        if previous is not None:
            # `running` is false for a run whose worker has not called start()
            # yet; terminate(force=True) still leaves a pending SIGKILL so it
            # cannot spawn a child after we have moved on.
            previous.terminate(force=True)
            previous.close()

        self.parser.reset()
        self._pending = Text()
        self._pending_chars = 0
        self._input_left = False
        self.states[info.key] = RunState.RUNNING
        self.summaries.pop(info.key, None)

        assert self._output is not None
        self._output.clear()
        self._output.jump_to_end()

        argv, problem = self._argv_for(info)

        banner = Text()
        banner.append("$ ", style="grey42")
        banner.append(_display_command(info, argv), style="bold #7dcfff")
        banner.append("\n")
        if problem:
            banner.append("bad quoting in arguments: ", style="#f7768e")
            banner.append(f"{problem}\n", style="grey58")
        self._output.write_text(banner)

        run = ScriptRun(
            key=info.key,
            argv=argv,
            cwd=str(self.scripts_dir),
            env=self._child_env(),
            on_output=lambda text: self._on_output(text, generation),
            on_finished=lambda finished: self._on_run_finished(finished, on_done),
            size=self._pane_size(),
        )
        self.active = run

        self._mark_row(info, RunState.RUNNING, None)
        self._set_status_state(RunState.RUNNING, info)
        self._set_message(f"running · {info.name}", "#7aa2f7")
        self._set_stdin_enabled(True, info)
        self._update_header()

        self.run_worker(self._drive_run(run), name="run-driver", group="driver")

    async def _drive_run(self, run: ScriptRun) -> None:
        try:
            if not await run.start():
                return
            self.call_after_refresh(self._push_size, run)
            await run.wait()
        finally:
            # `_finish` closes the pty, but keep this as a backstop for an
            # unexpected exception in the driver.
            run.close()

    def _child_env(self) -> dict[str, str]:
        rows, cols = self._pane_size()
        env = dict(os.environ)
        env.setdefault("TERM", "xterm-256color")
        env["PYTHONUNBUFFERED"] = "1"
        env["FORCE_COLOR"] = "1"
        env["COLUMNS"] = str(cols)
        env["LINES"] = str(rows)
        env["SCRIPTSUITE"] = "1"
        return env

    # -- pty callbacks --------------------------------------------------

    def _on_output(self, text: Text, generation: int) -> None:
        """Called from the pty reader; batch writes for a smooth repaint."""
        if generation != self._generation:
            return
        self._pending.append_text(text)
        self._pending_chars += len(text.plain)
        if self._pending_chars >= FLUSH_CHARS:
            self._flush_output()

    def _flush_output(self) -> None:
        # An unterminated prompt or a progress tail is released on a timer.
        if self.active is not None:
            self.active.release_pending()
            self._focus_input_if_prompted()
        if not self._pending_chars or self._output is None:
            return
        chunk, self._pending = self._pending, Text()
        self._pending_chars = 0
        self._output.write_text(chunk)
        self.parser.feed(chunk.plain)

    def _on_run_finished(self, run: ScriptRun, on_done: Any = None) -> None:
        # Ignore results from a run that a newer run has already superseded.
        if self.active is not run:
            return

        self._cancel_kill_timer()
        self._flush_output()
        summary = self.parser.finalize(run.stats.exit_code)
        state = self._reconcile(run.state, summary)

        # Match strictly on the run's own key.  Falling back to `current` would
        # attribute a result to whichever script happens to be highlighted now,
        # which can be a different script after F5 or arrow-key navigation.
        info = next((item for item in self.scripts if item.key == run.key), None)
        if info is not None:
            self.states[info.key] = state
            self.summaries[info.key] = summary

        self._mark_row(info, state, summary)
        if self._stdin is not None and self._stdin.has_focus:
            self._stdin.blur()
            if self._sidebar is not None:
                self._sidebar.focus()
        self._set_stdin_enabled(False, info)
        self._set_status_state(state, info)
        if info is not None:
            self._set_message(*self._result_message(info, state, summary, run))
        else:
            self._set_message(f"run finished · {state.label}", _COLOURS.get(state, ""))
        self._update_header()

        self.active = None
        if on_done is not None:
            on_done()

    @staticmethod
    def _reconcile(state: RunState, summary: TestSummary) -> RunState:
        """Let a parsed verdict correct the raw exit code, but not hide a crash.

        A suite that reports success and then exits non-zero (a teardown crash,
        a missing dependency in the runner) is still a failed run.  The
        opposite is allowed: a runner that reports failures but exits 0.
        """
        if state in (RunState.ABORTED, RunState.IDLE):
            return state
        if summary.ok is None:
            return state
        if summary.ok:
            return RunState.FAILED if state is RunState.FAILED else RunState.PASSED
        return RunState.FAILED

    def _result_message(
        self,
        info: ScriptInfo | None,
        state: RunState,
        summary: TestSummary,
        run: ScriptRun,
    ) -> tuple[str, str]:
        name = info.name if info else "run"
        if state is RunState.ABORTED:
            return f"{name} aborted", "#e0af68"

        detail = summary.summary_line()
        if summary.failures:
            names = ", ".join(summary.failures[:2])
            more = "…" if len(summary.failures) > 2 else ""
            detail = f"{detail} · {names}{more}" if detail else names
        if not detail and run.stats.exit_code is not None:
            detail = f"exit {run.stats.exit_code}"
        suffix = f" · {detail}" if detail else ""
        return f"{name} {state.label}{suffix}", _COLOURS.get(state, "")

    # -- state ----------------------------------------------------------

    def _mark_row(
        self, info: ScriptInfo | None, state: RunState, summary: TestSummary | None
    ) -> None:
        if info is None or self._sidebar is None:
            self._set_status_state(state, info)
            return
        row = self._sidebar.row_for(info.key)
        if row is not None:
            row.set_state(state, summary.summary_line() if summary else None)
        self._set_status_state(state, info)

    def _set_stdin_enabled(self, enabled: bool, info: ScriptInfo | None) -> None:
        if self._stdin is None:
            return
        if enabled and info is not None:
            self._stdin.set_enabled(True, f"stdin → {info.name} · type, then enter")
        else:
            self._stdin.set_enabled(False, "start a script to send it input")

    def _update_header(self) -> None:
        if self._header is None:
            return
        info = self.current
        if info is None:
            self._header.update_run(None, RunState.IDLE)
            return

        state = self.states.get(info.key, RunState.IDLE)
        summary = self.summaries.get(info.key)
        run = (
            self.active
            if self.active is not None and self.active.key == info.key
            else None
        )

        if run is not None and run.running:
            duration = run.stats.format_elapsed()
        elif summary is not None and summary.duration is not None:
            duration = f"{summary.duration:.2f}s"
        else:
            duration = ""

        self._header.update_run(
            info.name,
            state,
            summary.summary_line() if summary else "",
            summary.counts() if summary else [],
            duration,
        )

    def _tick(self) -> None:
        """Refresh the elapsed-time readout while a run is active."""
        if self.active is not None and self.active.running:
            self._update_header()

    # ------------------------------------------------------------------
    # Following a prompt
    # ------------------------------------------------------------------

    #: Focus areas from which it is safe to jump to the input bar: the user
    #: has not deliberately gone somewhere else.
    _NEUTRAL_FOCUS = (ScriptList, OutputPane)

    def _focus_input_if_prompted(self) -> None:
        """When a script asks a question, put the caret where the answer goes.

        Only from the sidebar or the output: if the user has gone to the
        arguments pane or the source, they meant to, and yanking them back
        would be worse than making them press f2.
        """
        run = self.active
        if run is None or not run.awaiting_input or self._stdin is None:
            self._input_left = False
            return
        if self._stdin.disabled:
            return

        if self._input_left:
            return

        focused = self.focused
        if focused is self._stdin:
            return
        if focused is not None and not isinstance(focused, self._NEUTRAL_FOCUS):
            return

        self._stdin.focus()
        self._stdin.cursor_position = len(self._stdin.value)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_stop(self) -> None:
        if self.active is None or self.active.done:
            self._set_message("nothing is running", "#e0af68")
            return
        self._terminate_with_escalation()
        self._set_message("stopping…", "#e0af68")

    def action_cancel_suite(self) -> None:
        """Stop the whole run-all sequence, not just the current script."""
        if not self._suite_active:
            self.action_stop()
            return
        self._cancel_suite()
        if self.active is not None and not self.active.done:
            self._terminate_with_escalation()

    def _terminate_with_escalation(self) -> None:
        """SIGTERM now; SIGKILL if the run has not finished shortly after."""
        run = self.active
        if run is None:
            return
        self._cancel_kill_timer()
        run.terminate()
        self._kill_timer = self.set_timer(2.0, lambda: self._force_kill(run))

    def _force_kill(self, run: ScriptRun) -> None:
        self._kill_timer = None
        if self.active is run and not run.done:
            run.terminate(force=True)
            self._set_message("force-killed the run", "#f7768e")

    def _cancel_kill_timer(self) -> None:
        if self._kill_timer is not None:
            self._kill_timer.stop()
            self._kill_timer = None

    def action_interrupt(self) -> None:
        if self.active is not None and self.active.running:
            self.active.interrupt()
            self._set_message("sent SIGINT", "#e0af68")
        else:
            self._set_message("nothing is running", "#e0af68")

    def action_clear_output(self) -> None:
        if self._output is None:
            return
        self._output.clear()
        self._output.jump_to_end()
        self._set_message("output cleared")

    def action_focus_stdin(self) -> None:
        if self._stdin is None:
            return
        if self._stdin.disabled:
            self._set_message("start a script to send it input", "#e0af68")
            return
        self._stdin.focus()
        self._stdin.cursor_position = len(self._stdin.value)

    def action_toggle_raw(self) -> None:
        if self._stdin is None or self._stdin.disabled:
            self._set_message("start a script to send it input", "#e0af68")
            return
        if self._stdin.raw:
            self._stdin.raw = False
        else:
            self._stdin.action_toggle_raw()
        self._stdin.focus()

    def action_help(self) -> None:
        # Pressing F1 while help is open must close it, not stack another copy.
        if isinstance(self.screen, HelpScreen):
            self.screen.dismiss(None)
            return
        self.push_screen(HelpScreen())

    def action_toggle_source_max(self) -> None:
        """Expand the source pane, or restore it to its normal height."""
        pane = self.query_one("#bottom-pane")
        if not pane.display:
            # F7 is also the way back to a collapsed source pane.
            pane.display = True
            self.screen.set_class(True, "-source-max")
            self._set_message("source pane expanded")
        else:
            self.screen.set_class(not self.screen.has_class("-source-max"), "-source-max")
            maximized = self.screen.has_class("-source-max")
            self._set_message(
                "source pane expanded" if maximized else "source pane restored"
            )
        self._set_status_keys()
        self._resize_active()

    def action_toggle_right(self) -> None:
        self._toggle_pane("#right-pane", "right pane")

    def action_toggle_bottom(self) -> None:
        pane = self.query_one("#bottom-pane")
        if pane.display:
            pane.display = False
            self.screen.set_class(False, "-source-max")
            self._set_message("source pane hidden")
        else:
            pane.display = True
            self.screen.set_class(False, "-source-max")
            self._set_message("source pane shown")
        self._set_status_keys()
        self._resize_active()

    def action_toggle_sidebar(self) -> None:
        self._toggle_pane("#sidebar", "sidebar")

    def _toggle_pane(self, selector: str, name: str) -> None:
        pane = self.query_one(selector)
        pane.display = not pane.display
        self._set_status_keys()
        self._set_message(f"{name} {'hidden' if not pane.display else 'shown'}")
        self._resize_active()

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    def _pane_size(self) -> tuple[int, int]:
        """Viewport size of the output pane: what the child should see."""
        if self._output is None:
            return 24, 80
        size = self._output.container_size
        return max(1, size.height), max(1, size.width)

    def _push_size(self, run: ScriptRun) -> None:
        run.resize(*self._pane_size())

    def _resize_active(self) -> None:
        """Push the new viewport after a layout change, not just a resize."""
        if self.active is not None and self.active.running:
            self.call_after_refresh(self._push_size, self.active)

    def on_resize(self) -> None:
        self._apply_responsive_classes()
        self._resize_active()

    def on_unmount(self) -> None:
        """Do not leave a child, its process group, or its pty behind on quit."""
        self._cancel_kill_timer()
        run = self.active
        self.active = None
        if run is not None:
            # A run may still be waiting for its worker to call start(); the
            # pending force-stop stops it from spawning after shutdown.
            run.terminate(force=True)
            run.close()

    def _apply_responsive_classes(self) -> None:
        """Tag the screen so the stylesheet can shed width when space is tight."""
        screen = self.screen
        screen.set_class(self.size.width < NARROW_WIDTH, "-narrow")
        screen.set_class(self.size.width < COMPACT_WIDTH, "-compact")

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------

    def _set_message(self, message: str, colour: str = "") -> None:
        if self._status is not None:
            self._status.set_message(message, colour)

    def _set_status_state(
        self, state: RunState, info: ScriptInfo | None, detail: str = ""
    ) -> None:
        if self._status is not None:
            self._status.set_state(state, info, detail)

    def _set_status_keys(self) -> None:
        keys: list[tuple[str, str]] = [
            ("enter", "run"),
            ("f2", "input"),
            ("f5", "rescan"),
            # Keep the two bottom-pane controls together.
            ("f7", "expand"),
            ("f8", "source"),
            ("f1", "keys"),
        ]
        if self.query_one("#right-pane").display and not self.screen.has_class(
            "-compact"
        ):
            keys.append(("f9", "right"))
        if self._status is not None:
            self._status.set_keys(keys)

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    @on(ScriptList.Highlighted)
    def _on_highlighted(self, event: ScriptList.Highlighted) -> None:
        event.stop()
        if self._sidebar is None:
            return
        info = self._sidebar.current_info
        self._show_args(info)
        self._show_source(info)
        if info is None or info.key == (self.current.key if self.current else None):
            return
        self.current = info
        self._update_header()
