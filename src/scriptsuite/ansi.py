"""Incremental ANSI -> Rich ``Text`` decoding.

Two problems have to be solved for streamed child output:

*Chunk boundaries.*  Reads split anywhere, including in the middle of an escape
sequence, so a trailing incomplete sequence is carried over to the next chunk.

*Line repaints.*  A terminal treats ``\\r`` as "return to the start of the
line".  Progress bars rely on it: each update rewrites the same line, and the
intermediate states should never reach the log.  But a prompt with no newline
at all (``Enter your name:``) must appear immediately.

The decoder therefore keeps the trailing partial line unrendered and only
releases it when it is complete (``\\n``) or when the caller asks after a short
grace period.
"""

from __future__ import annotations

import codecs
import re
import time

from rich.text import Text

_ESC = "\x1b"

#: A real escape sequence is short.  Past this, treat the bytes as a malformed
#: sequence and discard them rather than buffering without bound.
_MAX_CARRY = 8192

#: A single line longer than this is emitted instead of held forever.
_MAX_PARTIAL = 1_000_000

# Control characters that carry no meaning for a log line.
_NOISE = frozenset({ord("\x00"), ord("\x07"), ord("\x08"), ord("\x0b"), ord("\x0c")})

# Final byte of a CSI sequence, per ECMA-48: 0x40..0x7E.
_CSI_FINAL = re.compile(r"[\x40-\x7e]")

# OSC / DCS / SOS / PM / APC: a header, a body, then BEL or ST.  Rich drops
# ST-terminated ones but leaks a BEL-terminated body into the text, so these
# are removed here instead.
_STRING_SEQUENCE = re.compile(r"\x1b[\]PX^_].*?(?:\x07|\x1b\\)", re.DOTALL)

#: How long a trailing partial line may sit before it is shown anyway, so an
#: unterminated prompt is not held back indefinitely.
PARTIAL_LINE_GRACE = 0.12


def split_incomplete_escape(data: str) -> tuple[str, str]:
    """Split ``data`` into ``(complete, carry)``.

    ``complete`` is safe to hand on; ``carry`` is a trailing escape sequence
    that has not received its terminator and must be prepended to the next
    chunk.
    """
    index = data.rfind(_ESC)
    if index == -1:
        return data, ""

    tail = data[index:]

    if len(tail) == 1:
        # Bare ESC, waiting for its final byte.
        return data[:index], tail

    kind = tail[1]

    if kind == "]":
        # OSC: runs until BEL or ST (ESC backslash).
        if "\x07" in tail[2:] or "\x1b\\" in tail[2:]:
            return data, ""
        return data[:index], tail

    if kind == "[":
        # CSI: parameters and intermediates, then a final byte in 0x40..0x7E.
        if _CSI_FINAL.search(tail[2:]):
            return data, ""
        return data[:index], tail

    if kind in "PX^_":
        # DCS / SOS / PM / APC: string sequences terminated by ST.
        if "\x1b\\" in tail[2:]:
            return data, ""
        return data[:index], tail

    # Two-byte escape such as ESC c; complete as-is.
    return data, ""


def resolve_carriage_returns(data: str) -> str:
    """Replay ``\\r`` the way a terminal does: move the cursor, then overwrite.

    Modelling this as "split on \\r and keep the tail" is wrong in both
    directions.  A pty turns a program's ``\\r\\n`` into ``\\r\\r\\n``, and a
    trailing ``\\r`` moves the cursor without erasing anything — so
    ``"done\\r\\r"`` must still read as ``"done"``, not as an empty line.
    """
    if "\r" not in data:
        return data

    lines: list[str] = []
    for line in data.split("\n"):
        if "\r" not in line:
            lines.append(line)
            continue
        buffer: list[str] = []
        position = 0
        for char in line:
            if char == "\r":
                position = 0
                continue
            if position < len(buffer):
                buffer[position] = char
            else:
                buffer.append(char)
            position += 1
        lines.append("".join(buffer))

    return "\n".join(lines)


def _resolve_line(data: str) -> str:
    """Resolve CRs for plain text; leave ANSI-bearing lines for :func:`to_text`.

    A line with escape sequences must keep them: the style-aware resolver in
    ``to_text`` needs to know which cells were overwritten.
    """
    if "\x1b" in data:
        return data
    return resolve_carriage_returns(data)


def _normalize_crlf(data: str) -> str:
    """Collapse the pty's ``\\r\\r\\n`` (and plain CRLF) into ``\\n``.

    Rich's ANSI parser treats a bare ``\\r`` as "discard the current line", so
    a CRLF left in place would erase the line's text before the newline.
    """
    if "\r" not in data:
        return data
    return re.sub(r"\r+\n", "\n", data)


def to_text(data: str) -> Text:
    """Render decoded text as markup-free, styled ``Text``."""
    if not data:
        return Text()
    data = _normalize_crlf(strip_string_sequences(data))
    return strip_noise(Text.from_ansi(data))


def strip_string_sequences(data: str) -> str:
    """Remove OSC and other string sequences, which carry no log text."""
    if "\x1b" not in data:
        return data
    return _STRING_SEQUENCE.sub("", data)


def strip_noise(text: Text) -> Text:
    """Drop control characters that would corrupt the rendered line.

    Removing characters shifts the offsets of later style spans, so the spans
    are remapped through a prefix count of surviving characters.
    """
    plain = text.plain
    if not any(ord(char) in _NOISE for char in plain):
        return text

    kept_before: list[int] = [0] * (len(plain) + 1)
    chars: list[str] = []
    for index, char in enumerate(plain):
        if ord(char) not in _NOISE:
            chars.append(char)
        kept_before[index + 1] = len(chars)

    result = Text("".join(chars))
    for span in text.spans:
        start = kept_before[span.start]
        end = kept_before[span.end]
        if end > start:
            result.spans.append(type(span)(start, end, span.style))
    return result


class AnsiDecoder:
    """Byte stream -> displayable text, tolerant of chunk boundaries.

    ``feed`` returns only *complete* lines; a trailing partial line is held in
    :meth:`release` (after a grace period) or :meth:`flush` (at end of stream).
    """

    __slots__ = ("_utf8", "_carry", "_partial", "_partial_since", "_grace")

    def __init__(self, grace: float = PARTIAL_LINE_GRACE) -> None:
        self._utf8 = codecs.getincrementaldecoder("utf-8")("replace")
        self._carry = ""
        self._partial = ""
        self._partial_since = 0.0
        self._grace = grace

    def reset(self) -> None:
        """Drop all buffered state (used when a new run starts)."""
        self._utf8.reset()
        self._carry = ""
        self._partial = ""
        self._partial_since = 0.0

    # -- input ----------------------------------------------------------

    def feed(self, data: bytes) -> str:
        """Consume ``data`` and return whatever completed lines it produced."""
        return self._ingest(self._utf8.decode(data))

    def release(self, force: bool = False) -> str:
        """Return the held partial line if it has settled, else ``""``."""
        if not self._partial:
            return ""
        if not force and (time.monotonic() - self._partial_since) < self._grace:
            return ""
        return self._take_partial()

    def flush(self) -> str:
        """End of stream: return everything still buffered."""
        try:
            tail = self._utf8.decode(b"", True)
        except UnicodeDecodeError:
            tail = "\ufffd"

        carry, self._carry = self._carry, ""
        if not tail and not carry and not self._partial:
            return ""

        return self._take_partial() + self._ingest(tail + carry, complete=True)

    # -- internals ------------------------------------------------------

    def _take_partial(self) -> str:
        partial, self._partial = self._partial, ""
        self._partial_since = 0.0
        return strip_string_sequences(_resolve_line(partial))

    def _ingest(self, data: str, complete: bool = False) -> str:
        data, carry = split_incomplete_escape(self._carry + data)
        self._carry = carry if len(carry) <= _MAX_CARRY else ""
        if not data:
            return ""

        if complete:
            self._partial += data
            return self._take_partial()

        if "\n" not in data:
            self._partial += data
            if len(self._partial) >= _MAX_PARTIAL:
                # A line this long is not a prompt; stream it rather than
                # letting a child with no newline exhaust memory.
                return self._take_partial()
            # Stamp on every arrival, not just the first: a progress bar
            # repainting twice a second must keep resetting this, or the line
            # looks settled and gets released when it is not.
            if self._partial:
                self._partial_since = time.monotonic()
            return ""

        head, _, tail = data.rpartition("\n")
        self._partial += head + "\n"
        finished = strip_string_sequences(_resolve_line(self._partial))
        self._partial = tail
        self._partial_since = time.monotonic() if tail else 0.0
        return finished

    def pending(self) -> str:
        """The partial line currently held back."""
        return self._partial

    def settled(self) -> bool:
        """Is the held line a line that has stopped changing?

        A prompt is an unterminated line that has gone quiet.  A progress bar
        rewrites the same line continuously, so its stamp keeps moving and it
        never looks settled.
        """
        if not self._partial:
            return False
        return (time.monotonic() - self._partial_since) >= self._grace
