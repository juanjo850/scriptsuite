"""Unit tests for the interactive pty runner."""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from scriptsuite.runner import RunState, ScriptRun, run_once

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


class Collector:
    """Collects the text a run emits."""

    def __init__(self) -> None:
        self.chunks: list[str] = []

    def __call__(self, text) -> None:  # noqa: ANN001 - rich Text
        self.chunks.append(text.plain)

    @property
    def text(self) -> str:
        return "".join(self.chunks)


async def run(argv, **kwargs):
    collector = kwargs.pop("collector", None) or Collector()
    finished = await run_once(argv, on_output=collector, **kwargs)
    return finished, collector.text


# -- basics -----------------------------------------------------------------


async def test_captures_stdout():
    finished, output = await run(python("print('hello')"))
    assert finished.stats.exit_code == 0
    assert finished.state is RunState.PASSED
    assert output == "hello\n"


async def test_captures_stderr_separately_but_merged():
    _, output = await run(python("import sys; sys.stderr.write('oops\\n')"))
    assert output == "oops\n"


async def test_both_streams_are_interleaved_in_order():
    code = (
        "import sys\n"
        "sys.stdout.write('one\\n'); sys.stdout.flush()\n"
        "sys.stderr.write('two\\n'); sys.stderr.flush()\n"
        "sys.stdout.write('three\\n')\n"
    )
    _, output = await run(python(code))
    assert output.split() == ["one", "two", "three"]


async def test_non_zero_exit_is_a_failure():
    finished, _ = await run(python("raise SystemExit(3)"))
    assert finished.stats.exit_code == 3
    assert finished.state is RunState.FAILED


async def test_exception_exits_non_zero():
    finished, output = await run(python("raise ValueError('boom')"))
    assert finished.stats.exit_code != 0
    assert "ValueError" in output


async def test_elapsed_is_recorded():
    finished, _ = await run(python("import time; time.sleep(0.2)"))
    assert finished.stats.elapsed >= 0.15
    assert finished.stats.format_elapsed().endswith("s")


async def test_missing_command_is_reported_not_raised():
    collector = Collector()
    finished, output = await run(
        ["definitely-not-a-real-command-xyz"], collector=collector
    )
    assert finished.state is RunState.ABORTED
    assert "command not found" in finished.stats.error
    assert "command not found" in output


# -- terminal semantics -----------------------------------------------------


async def test_child_sees_a_terminal():
    _, output = await run(python("import sys; print('tty', sys.stdout.isatty())"))
    assert output.strip() == "tty True"


async def test_ansi_colour_survives():
    _, output = await run(python(r"print('\033[32mgreen\033[0m')"))
    assert "green" in output


async def test_progress_repaint_collapses_to_one_line():
    code = (
        "import sys, time\n"
        "for pct in (10, 50, 100):\n"
        "    sys.stdout.write(f'\\r{pct}%')\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(0.02)\n"
        "print()\n"
    )
    _, output = await run(python(code))
    lines = [line for line in output.splitlines() if line.strip()]
    assert lines == ["100%"]


async def test_crlf_output_survives_the_ptys_onlcr():
    # A pty turns the program's "\r\n" into "\r\r\n".  Handling the carriage
    # return as an erase rather than a cursor move would blank the line.
    _, output = await run(python(r"import sys; sys.stdout.write('first\r\nsecond\r\n')"))
    assert output == "first\nsecond\n"


async def test_final_line_before_exit_is_not_lost():
    _, output = await run(python("print('everything'); print('the last line')"))
    assert output.splitlines()[-1] == "the last line"


async def test_output_without_a_trailing_newline_survives():
    _, output = await run(python("import sys; sys.stdout.write('final words')"))
    assert output == "final words"


async def test_env_is_passed_through():
    collector = Collector()
    await run(
        python("import os; print(os.environ.get('SMOKE', 'unset'))"),
        env={**os.environ, "SMOKE": "visible"},
        collector=collector,
    )
    assert collector.text.strip() == "visible"


async def test_cwd_is_honoured(tmp_path):
    collector = Collector()
    await run(
        python("import os; print(os.getcwd())"), cwd=str(tmp_path), collector=collector
    )
    assert collector.text.strip() == str(tmp_path)


# -- stdin ------------------------------------------------------------------


async def test_line_input_reaches_the_child():
    collector = Collector()
    script = ScriptRun(
        key="stdin",
        argv=python("print('got:', input())"),
        on_output=collector,
    )
    await script.start()
    await asyncio.sleep(0.2)
    script.write("hello\n")
    await script.wait()
    assert "got: hello" in collector.text


async def test_raw_escape_sequences_reach_the_child_verbatim():
    # This is what raw mode is for: arrow keys and friends survive the round
    # trip byte for byte.
    collector = Collector()
    script = ScriptRun(
        key="raw",
        argv=python("import sys; print(repr(sys.stdin.readline()))"),
        on_output=collector,
    )
    await script.start()
    await asyncio.sleep(0.2)
    script.write(b"\x1b[A\x1b[B\n")
    await script.wait()
    assert "'\\x1b[A\\x1b[B\\n'" in collector.text


async def test_raw_input_can_be_sent_before_the_child_reads():
    collector = Collector()
    script = ScriptRun(
        key="buffered",
        argv=python(
            "import sys\n"
            "print('got', sys.stdin.readline().strip())\n"
            "print('got', sys.stdin.readline().strip())\n"
        ),
        on_output=collector,
    )
    await script.start()
    script.write("one\ntwo\n")
    await script.wait()
    assert "got one" in collector.text
    assert "got two" in collector.text


async def test_ctrl_d_gives_the_child_eof():
    collector = Collector()
    script = ScriptRun(
        key="eof",
        argv=python("import sys; print(repr(sys.stdin.read()))"),
        on_output=collector,
    )
    await script.start()
    await asyncio.sleep(0.2)
    script.write("one\ntwo\n\x04")
    await asyncio.wait_for(script.wait(), timeout=10)
    assert "'one\\ntwo\\n'" in collector.text


# -- signals ----------------------------------------------------------------


async def test_interrupt_delivers_sigint():
    collector = Collector()
    script = ScriptRun(
        key="sigint",
        argv=python(
            "import signal, sys, time\n"
            "signal.signal(signal.SIGINT, lambda *a: (print('caught'), sys.exit(7)))\n"
            "print('ready'); sys.stdout.flush()\n"
            "time.sleep(30)\n"
        ),
        on_output=collector,
    )
    await script.start()
    for _ in range(100):
        if "ready" in collector.text:
            break
        await asyncio.sleep(0.02)
    script.interrupt()
    await script.wait()
    assert "caught" in collector.text
    assert script.stats.exit_code == 7


async def test_terminate_stops_a_long_run():
    script = ScriptRun(key="kill", argv=python("import time; time.sleep(60)"))
    await script.start()
    assert script.running
    script.terminate()
    await script.wait()
    assert not script.running
    assert script.state is RunState.ABORTED


async def test_child_is_its_own_process_group():
    script = ScriptRun(key="pgid", argv=python("print(1)"))
    await script.start()
    assert script.pid is not None
    assert os.getpgid(script.pid) == script.pid
    await script.wait()


async def test_child_process_group_is_disjoint_from_the_parent():
    # Because the child gets its own session, a Ctrl-C aimed at the UI's
    # process group cannot reach it.
    script = ScriptRun(key="isolated", argv=python("import time; time.sleep(0.5)"))
    await script.start()
    assert script.pid is not None
    assert os.getpgid(script.pid) != os.getpgid(0)
    await script.wait()


# -- resize -----------------------------------------------------------------


async def test_resize_updates_the_terminal_size():
    script = ScriptRun(
        key="resize", argv=python("import os; print(os.get_terminal_size().columns)")
    )
    collector = Collector()
    script._on_output = collector
    await script.start()
    script.resize(40, 123)
    await script.wait()
    assert collector.text.strip() == "123"


# -- prompting --------------------------------------------------------------


async def test_settled_prompt_line_marks_the_run_as_waiting():
    collector = Collector()
    script = ScriptRun(
        key="prompt",
        argv=python("import sys; print('q? ', end='', flush=True); sys.stdin.readline()"),
        on_output=collector,
    )
    await script.start()
    for _ in range(60):
        await asyncio.sleep(0.02)
        if script.awaiting_input:
            break
    assert script.awaiting_input, "a settled unterminated line means it is prompting"

    # The UI drives the release; that is what actually shows the prompt.
    for _ in range(10):
        script.release_pending()
        await asyncio.sleep(0.02)
    assert "q?" in collector.text

    script.write("\n")
    await script.wait()
    assert not script.awaiting_input


async def test_more_output_clears_the_waiting_flag():
    script = ScriptRun(
        key="chatty",
        argv=python(
            "import sys, time\n"
            "while True:\n"
            "    sys.stdout.write('tick ')\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(0.03)\n"
        ),
    )
    await script.start()
    await asyncio.sleep(0.3)
    assert not script.awaiting_input, "a chatty script is not prompting"
    script.terminate()
    await script.wait()


async def test_a_finished_run_is_not_waiting():
    script = ScriptRun(key="quick", argv=python("print('done', end='')"))
    await script.start()
    await script.wait()
    assert not script.awaiting_input


async def test_a_fast_progress_bar_is_not_a_prompt():
    script = ScriptRun(
        key="bar",
        argv=python(
            "import sys, time\n"
            "for i in range(30):\n"
            "    sys.stdout.write(f'\\r{i}%')\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(0.03)\n"
        ),
    )
    await script.start()
    # Only judge it while the bar is actually repainting; once the child
    # exits and stops writing, a leftover partial line looks settled by
    # definition.
    while script._proc is not None and script._proc.poll() is None:
        await asyncio.sleep(0.02)
        assert not script.awaiting_input, "a repainting bar must not look like a prompt"
        script.release_pending()
    await script.wait()


# -- lifecycle --------------------------------------------------------------


async def test_start_is_a_noop_while_running():
    script = ScriptRun(key="twice", argv=python("import time; time.sleep(0.5)"))
    await script.start()
    assert await script.start() is True
    await script.wait()


async def test_run_can_be_restarted_after_completion():
    script = ScriptRun(key="restart", argv=python("print('again')"))
    collector = Collector()
    script._on_output = collector
    await script.start()
    await script.wait()
    first = script.stats.exit_code
    await script.start()
    await script.wait()
    assert script.stats.exit_code == first
    assert collector.text.count("again") == 2


async def test_close_is_idempotent():
    script = ScriptRun(key="close", argv=python("print(1)"))
    await script.start()
    await script.wait()
    script.close()
    script.close()


async def test_on_fired_fires_once():
    finished: list[ScriptRun] = []
    script = ScriptRun(
        key="done", argv=python("print(1)"), on_finished=lambda run: finished.append(run)
    )
    await script.start()
    await script.wait()
    script._finish()
    assert finished == [script]


async def test_unterminated_trailing_output_is_not_lost():
    collector = Collector()
    await run(
        python("import sys; sys.stdout.write('no newline at all')"), collector=collector
    )
    assert "no newline at all" in collector.text


async def test_large_output_survives_intact():
    collector = Collector()
    await run(python("print('x' * 200000)"), collector=collector)
    assert collector.text.strip() == "x" * 200000


async def test_a_finished_run_releases_its_pty():
    # Regression: the TUI path used to drop the ScriptRun without calling
    # close(), leaking one fd per run until no script could start.
    finished, _ = await run(python("print('done')"))
    assert finished._master is None
    assert finished.pid is not None


async def test_master_is_non_blocking():
    script = ScriptRun(key="nb", argv=python("import time; time.sleep(5)"))
    await script.start()
    try:
        assert os.get_blocking(script._master) is False
    finally:
        script.terminate(force=True)
        await script.wait()


async def test_stop_requested_before_start_is_not_lost():
    # The UI sets `active` before its worker calls start(); a stop in that
    # window must still kill the child instead of letting it run to completion.
    script = ScriptRun(key="early", argv=python("import time; time.sleep(30)"))
    script.terminate()
    assert await script.start() is True
    await asyncio.wait_for(script.wait(), timeout=5)
    assert script.state is RunState.ABORTED
