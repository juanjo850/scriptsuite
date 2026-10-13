"""Run scripts in a pseudo-terminal so interactive programs behave normally.

Pipes are not enough for a test-suite workbench: a child writing colour codes
or a progress bar needs a real terminal, and the user needs to type into one.
So each run gets a private pty.

The child is started with the pty slave as all three standard streams and in
its own session, which keeps its Ctrl-C away from the UI.  Because the pty is
not the child's *controlling* terminal, signals are delivered explicitly to its
process group (``killpg``) rather than relying on the line discipline.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import fcntl
import os
import pty
import signal
import struct
import subprocess
import termios
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum

from rich.text import Text

from .ansi import AnsiDecoder, to_text

_READ_SIZE = 65536
#: Poll interval used while waiting for the child to exit.
_REAP_INTERVAL = 0.04
#: How long to keep reading after the child is reaped, to catch a last line.
_SETTLE_SECONDS = 0.25
#: Grace before an unterminated line is shown and read as a prompt.
_PROMPT_GRACE = 0.12

#: EOF is reported as EIO on Linux once the child side is gone.
_EOF_ERRNOS = {errno.EIO}


class RunState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ABORTED = "aborted"

    @property
    def is_active(self) -> bool:
        return self is RunState.RUNNING

    @property
    def glyph(self) -> str:
        return {
            RunState.IDLE: "○",
            RunState.RUNNING: "●",
            RunState.PASSED: "✔",
            RunState.FAILED: "✖",
            RunState.ABORTED: "■",
        }[self]

    @property
    def label(self) -> str:
        return {
            RunState.IDLE: "idle",
            RunState.RUNNING: "running",
            RunState.PASSED: "passed",
            RunState.FAILED: "failed",
            RunState.ABORTED: "aborted",
        }[self]


@dataclass
class RunStats:
    started_at: float = 0.0
    finished_at: float = 0.0
    exit_code: int | None = None
    signal: int | None = None
    error: str | None = None

    @property
    def elapsed(self) -> float:
        end = self.finished_at or time.monotonic()
        return max(0.0, end - self.started_at)

    def format_elapsed(self) -> str:
        seconds = self.elapsed
        if seconds < 60:
            return f"{seconds:.1f}s"
        minutes, seconds = divmod(int(seconds), 60)
        return f"{minutes}m{seconds:02d}s"


OutputCallback = Callable[[Text], None]
FinishedCallback = Callable[["ScriptRun"], None]


def set_winsize(fd: int, rows: int, cols: int) -> None:
    """Push a window size onto a pty so the child can redraw itself."""
    with contextlib.suppress(OSError):
        fcntl.ioctl(
            fd, termios.TIOCSWINSZ, struct.pack("HHHH", max(1, rows), max(1, cols), 0, 0)
        )


def _configure_slave(fd: int) -> None:
    """Take over signalling so Ctrl-C is ours to route, not the tty's."""
    try:
        attrs = termios.tcgetattr(fd)
    except termios.error:
        return
    attrs[0] &= ~termios.ISIG  # keep ^C as a literal byte
    attrs[0] |= termios.ICANON | termios.ECHO
    attrs[3] &= ~termios.ECHOCTL
    with contextlib.suppress(termios.error):
        termios.tcsetattr(fd, termios.TCSANOW, attrs)


class ScriptRun:
    """A single execution of a single script."""

    def __init__(
        self,
        key: str,
        argv: Sequence[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        on_output: OutputCallback | None = None,
        on_finished: FinishedCallback | None = None,
        size: tuple[int, int] = (24, 80),
    ) -> None:
        self.key = key
        self.argv = tuple(argv)
        self.cwd = cwd
        self.env = env
        self.state = RunState.IDLE
        self.stats = RunStats()

        self._on_output = on_output
        self._on_finished = on_finished
        self._size = size

        self._pid: int | None = None
        self._master: int | None = None
        self._decoder = AnsiDecoder()
        self._proc: subprocess.Popen[bytes] | None = None
        self._aborted = False
        self._pending_stop: bool | None = None
        self._finished = False
        self._prompt_waiting = False
        self._watcher_installed = False

    # -- properties -----------------------------------------------------

    @property
    def pid(self) -> int | None:
        return self._pid

    @property
    def running(self) -> bool:
        return self.state.is_active

    @property
    def awaiting_input(self) -> bool:
        """True while the child looks like it is sitting at a prompt.

        A prompt is an unterminated line that stopped changing.  The decoder
        holds exactly such a line, so the answer is either "we already released
        a settled line from a live child", or "the line is settled right now".
        """
        if not self.state.is_active:
            return False
        return self._prompt_waiting or self._decoder.settled()

    # -- lifecycle ------------------------------------------------------

    async def start(self) -> bool:
        """Launch the child and hook the pty into the event loop."""
        if self.state.is_active:
            return True

        self._decoder.reset()
        self._finished = False
        self._prompt_waiting = False
        # A stop requested before the process existed is honoured here.
        pending_stop = self._pending_stop
        self._pending_stop = None
        self._aborted = pending_stop is not None
        self.stats = RunStats(started_at=time.monotonic())

        try:
            master, slave = pty.openpty()
        except OSError as exc:  # pragma: no cover - pty exhausted
            self.stats.error = f"cannot allocate pty: {exc}"
            self.state = RunState.ABORTED
            return False

        _configure_slave(slave)
        set_winsize(master, *self._size)

        try:
            self._proc = subprocess.Popen(  # noqa: S603 - argv is fully explicit
                list(self.argv),
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=self.cwd,
                env=self.env,
                pass_fds=(slave,),
                start_new_session=True,
                close_fds=True,
            )
        except FileNotFoundError:
            os.close(master)
            os.close(slave)
            self.stats.error = f"command not found: {self.argv[0]}"
            self.stats.finished_at = time.monotonic()
            self.state = RunState.ABORTED
            self._emit_error()
            return False
        except OSError as exc:
            os.close(master)
            os.close(slave)
            self.stats.error = f"cannot start {self.argv[0]}: {exc}"
            self.stats.finished_at = time.monotonic()
            self.state = RunState.ABORTED
            self._emit_error()
            return False

        os.close(slave)

        self._master = master
        self._pid = self._proc.pid
        self.state = RunState.RUNNING

        loop = asyncio.get_running_loop()
        loop.add_reader(master, self._readable)
        self._watcher_installed = True

        if pending_stop is not None:
            self.terminate(force=pending_stop)
        return True

    async def wait(self) -> RunStats:
        """Await completion, streaming output as it arrives."""
        proc = self._proc
        if proc is None:
            return self.stats

        while self.state.is_active:
            if proc.poll() is not None:
                await self._settle()
                self._finish()
                break
            await asyncio.sleep(_REAP_INTERVAL)

        return self.stats

    async def _settle(self) -> None:
        """Collect output the child wrote in its last moments.

        A process can print a final line and exit before the reader has been
        scheduled again; closing the pty straight away would drop it.  Keep
        draining until the master has been quiet twice in a row.
        """
        deadline = time.monotonic() + _SETTLE_SECONDS
        quiet = 0
        while time.monotonic() < deadline and quiet < 2:
            if self._drain():
                quiet = 0
            else:
                quiet += 1
            await asyncio.sleep(0.02)
        self._flush_decoder()

    # -- io -------------------------------------------------------------

    def _readable(self) -> None:
        master = self._master
        if master is None:
            return
        try:
            chunk = os.read(master, _READ_SIZE)
        except (BlockingIOError, InterruptedError):
            return
        except OSError as exc:
            if exc.errno in _EOF_ERRNOS or exc.errno == errno.EBADF:
                self._drain()
            return

        if not chunk:
            self._drain()
            return

        self._emit(self._decoder.feed(chunk))

    def _drain(self) -> int:
        """Consume everything currently buffered; returns the chunk count.

        Only bytes are read here.  Flushing the decoder is left to
        :meth:`_flush_decoder`, so a held partial line is not emitted twice
        while the run is settling.
        """
        master = self._master
        if master is None:
            return 0
        count = 0
        while True:
            try:
                chunk = os.read(master, _READ_SIZE)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                break
            if not chunk:
                break
            self._emit(self._decoder.feed(chunk))
            count += 1
        return count

    def _flush_decoder(self) -> None:
        """Emit whatever the decoder still holds, once, at end of stream."""
        self._emit(self._decoder.flush())

    def _emit(self, data: str) -> None:
        """Hand decoded text to the callback, if there is any."""
        if not data:
            return
        self._prompt_waiting = False
        if self._on_output is not None:
            self._on_output(to_text(data))

    def release_pending(self, force: bool = False) -> None:
        """Emit a settled partial line (an unterminated prompt, a progress tail).

        The UI calls this on a timer so a prompt with no trailing newline still
        shows up promptly, while a progress bar being repainted stays collapsed
        onto one line.
        """
        if self._decoder is None:
            return
        settled = self._decoder.release(force=force)
        self._emit(settled)
        if settled.strip() and self.state.is_active:
            # A line that sat unterminated long enough to be shown, from a
            # child that is still alive: it is waiting for us.
            self._prompt_waiting = True

    def write(self, data: str | bytes) -> None:
        """Send keystrokes to the child.  Never raises."""
        master = self._master
        if master is None:
            return
        payload = data.encode() if isinstance(data, str) else data
        while payload:
            try:
                written = os.write(master, payload)
            except (BlockingIOError, InterruptedError):
                continue
            except OSError:
                return
            payload = payload[written:]

    def interrupt(self) -> None:
        """Send Ctrl-C (SIGINT) to the child's process group."""
        if self._pid is None or not self.state.is_active:
            return
        self._signal_group(signal.SIGINT)

    def terminate(self, force: bool = False) -> None:
        """Ask the run to stop; ``force`` escalates straight to SIGKILL.

        Safe to call before the child exists: the request is remembered and
        applied the moment it is spawned, so a stop issued right after launch
        is never lost.
        """
        self._aborted = True
        if self._pid is None or not self.state.is_active:
            self._pending_stop = force
            return
        self._signal_group(signal.SIGKILL if force else signal.SIGTERM)

    def resize(self, rows: int, cols: int) -> None:
        """Propagate a new window size and nudge the child to redraw."""
        self._size = (rows, cols)
        if self._master is not None:
            set_winsize(self._master, rows, cols)
        if self._pid is not None and self.state.is_active:
            # The pty is not a controlling terminal, so the kernel will not
            # raise SIGWINCH for us.
            self._signal_group(signal.SIGWINCH)

    def close(self) -> None:
        """Release the pty; safe to call more than once."""
        self._remove_watcher()
        self._drain()
        self._flush_decoder()
        if self._master is not None:
            with contextlib.suppress(OSError):
                os.close(self._master)
            self._master = None

    # -- internals ------------------------------------------------------

    def _signal_group(self, sig: int) -> None:
        """Signal the whole process group, falling back to the child alone."""
        assert self._pid is not None
        try:
            os.killpg(self._pid, sig)
        except (ProcessLookupError, PermissionError):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(self._pid, sig)

    def _remove_watcher(self) -> None:
        if not self._watcher_installed:
            return
        self._watcher_installed = False
        if self._master is None:
            return
        with contextlib.suppress(RuntimeError, ValueError, OSError):
            asyncio.get_running_loop().remove_reader(self._master)

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._prompt_waiting = False

        proc = self._proc
        code = proc.returncode if proc is not None else None
        self.stats.finished_at = time.monotonic()
        self.stats.exit_code = code
        if code is not None and code < 0:
            self.stats.signal = -code

        if self._aborted or self.stats.error:
            self.state = RunState.ABORTED
        elif code == 0:
            self.state = RunState.PASSED
        else:
            self.state = RunState.FAILED

        self._remove_watcher()
        if self._on_finished is not None:
            self._on_finished(self)

    def _emit_error(self) -> None:
        if self.stats.error is None:
            return
        text = Text()
        text.append("scriptsuite: ", style="bold #f7768e")
        text.append(self.stats.error, style="#f7768e")
        text.append("\n")
        if self._on_output is not None:
            self._on_output(text)
        self._finish()


async def run_once(
    argv: Sequence[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    on_output: OutputCallback | None = None,
    size: tuple[int, int] = (24, 80),
) -> ScriptRun:
    """Run a command to completion, streaming its output to ``on_output``.

    Returns the finished :class:`ScriptRun`, whose ``stats`` and ``state``
    describe the outcome.
    """
    run = ScriptRun(
        key="once", argv=argv, cwd=cwd, env=env, on_output=on_output, size=size
    )
    await run.start()
    if run.state is not RunState.RUNNING:
        run.close()
        return run
    try:
        await run.wait()
    finally:
        run.close()
    return run
