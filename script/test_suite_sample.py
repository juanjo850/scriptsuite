"""Pytest suite exercising the runner, the output parser and stdout streaming.

Two tests fail on purpose so the failure path — red badge, counts in the
header, failure names — is easy to see. Run it directly or point pytest at it.
"""

from __future__ import annotations

import os
import sys
import time

import pytest


def test_math_is_stable() -> None:
    assert sum(range(100)) == 4950


def test_string_helpers() -> None:
    assert "scriptsuite".upper() == "SCRIPTSUITE"
    assert ",".join(["a", "b"]) == "a,b"


def test_running_on_a_real_terminal() -> None:
    """scriptsuite launches scripts on a pty, so isatty() holds."""
    assert sys.stdout.isatty(), "expected a pty, got a pipe"


def test_deliberate_failure() -> None:
    """Meant to fail: shows how a failure is reported."""
    expected, actual = "42", "43"
    assert expected == actual, f"expected {expected}, got {actual}"


def test_deliberate_error() -> None:
    """Meant to error out, distinct from an assertion failure."""
    raise RuntimeError("something blew up on purpose")


def test_skipped_example() -> None:
    pytest.skip("not implemented yet")


def test_streams_output(capsys: pytest.CaptureFixture[str]) -> None:
    time.sleep(0.2)
    print("a line printed from inside a test")
    captured = capsys.readouterr()
    assert "a line printed" in captured.out


if __name__ == "__main__":
    # Runnable both as `pytest script/...` and directly as `python script/...`.
    raise SystemExit(pytest.main([__file__, "-v", "--no-header"]))