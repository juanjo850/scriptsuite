"""Unit tests for the ANSI stream decoder."""

from __future__ import annotations

import time

import pytest

from scriptsuite.ansi import (
    AnsiDecoder,
    resolve_carriage_returns,
    split_incomplete_escape,
    strip_noise,
    to_text,
)


def decode(*chunks: bytes) -> str:
    """Feed chunks through a decoder and return everything it produced.

    Escape sequences are still present: the decoder guarantees they are never
    torn, but rendering them is :func:`to_text`'s job.
    """
    decoder = AnsiDecoder()
    out = []
    for chunk in chunks:
        out.append(decoder.feed(chunk))
    out.append(decoder.flush())
    return "".join(out)


def rendered(*chunks: bytes) -> str:
    """What the user actually sees: escape sequences resolved away."""
    return to_text(decode(*chunks)).plain


# -- plain text -------------------------------------------------------------


def test_plain_text_round_trips():
    assert decode(b"hello world\n") == "hello world\n"


def test_unterminated_line_is_held_until_flush():
    decoder = AnsiDecoder()
    assert decoder.feed(b"Enter your name: ") == ""
    assert decoder.pending() == "Enter your name: "
    assert decoder.release(force=True) == "Enter your name: "


def test_partial_line_released_after_grace():
    decoder = AnsiDecoder(grace=0.01)
    assert decoder.feed(b"partial") == ""
    assert decoder.release() == ""  # inside the grace window
    time.sleep(0.02)
    assert decoder.release() == "partial"


def test_split_multibyte_utf8_across_chunks():
    data = "café".encode()
    assert decode(data[:4], data[4:]) == "café"


def test_invalid_utf8_becomes_replacement_characters():
    assert decode(b"\xff\xfe ok") == "\ufffd\ufffd ok"


# -- carriage returns -------------------------------------------------------


def test_crlf_line_ending_is_normalised():
    assert decode(b"line one\r\nline two\r\n") == "line one\nline two\n"


def test_trailing_cr_does_not_erase_the_line():
    # Regression: rsplit on a line-ending CR used to blank every pty line.
    assert decode(b"hello world\r\n") == "hello world\n"


def test_repaint_keeps_only_the_last_segment():
    assert decode(b"10%\r50%\r100%\r\n") == "100%\n"


def test_repaint_split_across_writes_collapses_to_one_line():
    # The point of holding the partial line: each write only overwrites the
    # not-yet-rendered tail, so the bar stays on one line.
    decoder = AnsiDecoder()
    assert decoder.feed(b"10%\r") == ""
    assert decoder.feed(b"50%\r") == ""
    assert decoder.feed(b"100%\r\n") == "100%\n"
    assert decoder.pending() == ""


def test_interior_cr_overwrites_from_column_zero():
    # A terminal moves to column 0 and overwrites; characters the shorter run
    # does not reach are still on screen.
    assert decode(b"before\rafter\n") == "aftere\n"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ("abc\r", "abc"),
        ("abc\r\n", "abc\n"),
        ("abc\rdef", "def"),
        ("abc\rdef\r", "def"),
        ("abc\rdef\nghi\r", "def\nghi"),
        ("no-cr", "no-cr"),
        # What a pty actually delivers for a program writing "\r\n":
        # ONLCR appends another CR, so the line must survive intact.
        ("abc\r\r\n", "abc\n"),
        ("10%\r\r\n", "10%\n"),
        # A progress bar under the same ONLCR treatment.
        ("10%\r\r50%\r\r100%\r\r\n", "100%\n"),
        ("longer text\rs", "songer text"),
        ("abcdef\rXY", "XYcdef"),
    ],
)
def test_resolve_carriage_returns(data, expected):
    assert resolve_carriage_returns(data) == expected


# -- escape sequences -------------------------------------------------------


def test_sgr_colour_is_applied():
    text = to_text("\x1b[31mred\x1b[0m plain\n")
    assert text.plain == "red plain\n"
    assert text.spans, "expected a style span for the coloured run"


def test_escape_sequence_split_across_chunks_is_not_lost():
    assert rendered(b"\x1b[3", b"2mgreen\x1b[0m\n") == "green\n"


def test_split_osc_is_held_until_terminated():
    decoder = AnsiDecoder()
    assert decoder.feed(b"\x1b]0;a window ti") == ""
    assert decoder.pending() == ""
    assert decoder.feed(b"tle\x07") == ""
    # Complete at last: the whole OSC is consumed, body and all.
    assert decoder.flush() == ""
    assert to_text(b"\x1b]0;a window title\x07after\n".decode()).plain == "after\n"


def test_bell_terminated_osc_body_is_not_shown():
    # Rich leaks a BEL-terminated OSC body into the text; we must not.
    assert rendered(b"\x1b]0;my title\x07visible\n") == "visible\n"


def test_st_terminated_osc_keeps_the_link_text():
    # All string sequences are stripped: OSC carries no log text, and a
    # hyperlink has nothing to click in a terminal pane anyway.
    assert rendered(b"\x1b]8;;http://example.com\x1b\\link\x1b]8;;\x1b\\") == "link"


def test_cursor_and_screen_sequences_are_dropped():
    assert rendered(b"\x1b[2Jcleared\n\x1b[?25lhidden\x1b[?25h\n") == "cleared\nhidden\n"


@pytest.mark.parametrize(
    ("data", "carry"),
    [
        ("plain", ""),
        ("\x1b[31m", ""),
        ("\x1b[1;2;3H", ""),
        ("\x1b[", "\x1b["),
        ("\x1b[31", "\x1b[31"),
        ("\x1b", "\x1b"),
        ("\x1b]0;t", "\x1b]0;t"),
        ("\x1b]0;t\x07", ""),
        ("\x1bM\x1b[0m", ""),
    ],
)
def test_split_incomplete_escape(data, carry):
    complete, held = split_incomplete_escape(data)
    assert held == carry
    assert complete + held == data


def test_split_osc_keeps_the_text_before_it():
    complete, held = split_incomplete_escape("visible\x1b]0;t")
    assert complete == "visible"
    assert held == "\x1b]0;t"


# -- noise ------------------------------------------------------------------


def test_strip_noise_preserves_styles():
    text = to_text("a\x1b[1mb\x07c")
    cleaned = strip_noise(text)
    assert cleaned.plain == "abc"


def test_strip_noise_remaps_trailing_span():
    from rich.text import Text

    text = Text("ab\x00")
    text.stylize("red", 0, 2)
    cleaned = strip_noise(text)
    assert cleaned.plain == "ab"
    assert cleaned.spans == [(0, 2, "red")]


def test_strip_noise_is_a_noop_without_control_chars():
    from rich.text import Text

    text = Text("clean")
    assert strip_noise(text) is text


# -- reset ------------------------------------------------------------------


def test_reset_discards_a_partial_escape_and_partial_line():
    decoder = AnsiDecoder()
    decoder.feed(b"ok\x1b[3")
    decoder.feed(b"partial")
    decoder.reset()
    assert decoder.pending() == ""
    assert decoder.feed(b"1mred\n") == "1mred\n"


def test_coloured_progress_bar_collapses_to_the_last_value():
    # The CR is replayed after ANSI parsing: escape bytes must not count as
    # columns or the bar renders as "100%m50%".
    text = to_text("\x1b[32m50%\x1b[0m\r100%\n")
    assert text.plain == "100%\n"


def test_huge_line_without_a_newline_is_released():
    decoder = AnsiDecoder()
    produced = decoder.feed(b"x" * 1_000_010)
    assert produced
    assert decoder.pending() == ""


def test_malformed_escape_sequence_cannot_grow_without_bound():
    decoder = AnsiDecoder()
    assert decoder.feed(b"\x1b]" + b"x" * 20_000) == ""
    assert len(decoder.pending()) < 20_000


def test_to_text_of_empty_is_empty():
    assert to_text("").plain == ""
