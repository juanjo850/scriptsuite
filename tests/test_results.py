"""Unit tests for the test-output parser."""

from __future__ import annotations

import pytest

from scriptsuite.results import ResultParser

PYTEST_OUTPUT = """\
============================= test session starts ==============================
collected 7 items

script/test_suite_sample.py ..F.E.s                                          [100%]

=================================== FAILURES ===================================
_____________________________ test_deliberate_failure ______________________________
    def test_deliberate_failure():
>       assert expected == actual
E       AssertionError: assert '42' == '43'

_______________________________ test_deliberate_error _______________________________
E       RuntimeError: something blew up

=========================== short test summary info ============================
FAILED script/test_suite_sample.py::test_deliberate_failure - AssertionError
FAILED script/test_suite_sample.py::test_deliberate_error - RuntimeError
=================== 3 passed, 3 failed, 1 skipped in 0.30s =======================
"""

UNITTEST_OUTPUT = """\
.....
======================================================================
Ran 5 tests in 0.031s

FAILED (failures=2, errors=1)
"""

UNITTEST_OK_OUTPUT = """\
.....
----------------------------------------------------------------------
Ran 5 tests in 0.031s

OK
"""

TAP_OUTPUT = """\
TAP version 13
1..5
ok 1 - first
ok 2 - second
not ok 3 - third
ok 4 - fourth
ok 5 - fifth
"""


def parse(output: str, exit_code: int | None = 0) -> object:
    parser = ResultParser()
    parser.feed(output)
    return parser.finalize(exit_code)


# -- pytest -----------------------------------------------------------------


def test_pytest_counts_and_duration():
    summary = parse(PYTEST_OUTPUT)
    assert summary.framework == "pytest"
    assert summary.passed == 3
    assert summary.failed == 3
    assert summary.skipped == 1
    assert summary.duration == pytest.approx(0.30)
    assert summary.total == 7


def test_pytest_failure_names():
    summary = parse(PYTEST_OUTPUT, exit_code=1)
    assert any("test_deliberate_failure" in name for name in summary.failures)
    assert any("test_deliberate_error" in name for name in summary.failures)


def test_pytest_verdict_overrides_exit_code():
    # The suite reported failures even though the process exited 0.
    summary = parse(PYTEST_OUTPUT, exit_code=0)
    assert summary.ok is False


def test_pytest_summary_line_reads_naturally():
    summary = parse(PYTEST_OUTPUT)
    assert "3 passed" in summary.summary_line()
    assert "3 failed" in summary.summary_line()


def test_last_summary_line_wins():
    parser = ResultParser()
    parser.feed("===== 1 passed in 0.1s =====\n")
    parser.feed("===== 5 passed, 2 failed in 0.9s =====\n")
    summary = parser.finalize(1)
    assert summary.passed == 5
    assert summary.failed == 2


def test_pytest_split_across_chunks():
    parser = ResultParser()
    for index in range(0, len(PYTEST_OUTPUT), 37):
        parser.feed(PYTEST_OUTPUT[index : index + 37])
    summary = parser.finalize(1)
    assert summary.passed == 3
    assert summary.failed == 3


# -- unittest ---------------------------------------------------------------


def test_unittest_failure_mix():
    summary = parse(UNITTEST_OUTPUT, exit_code=1)
    assert summary.framework == "unittest"
    assert summary.total == 5
    assert summary.failed == 2
    assert summary.errors == 1
    assert summary.duration == pytest.approx(0.031)
    assert summary.ok is False


def test_unittest_ok():
    summary = parse(UNITTEST_OK_OUTPUT)
    assert summary.passed == 5
    assert summary.ok is True


# -- TAP --------------------------------------------------------------------


def test_tap_counts():
    summary = parse(TAP_OUTPUT)
    assert summary.framework == "tap"
    assert summary.passed == 4
    assert summary.failed == 1
    assert summary.total == 5
    assert summary.ok is False


# -- fallbacks --------------------------------------------------------------


def test_no_output_and_zero_exit_invents_no_counts():
    summary = parse("", exit_code=0)
    assert summary.framework == "exit-code"
    assert not summary.has_data
    assert summary.summary_line() == ""
    assert summary.ok is None


def test_reset_clears_state():
    parser = ResultParser()
    parser.feed(PYTEST_OUTPUT)
    parser.reset()
    assert parser.summary.passed is None
    assert parser.summary.failures == []


def test_counts_omits_unset_values():
    summary = parse("===== 2 passed in 0.1s =====\n")
    assert summary.counts() == [("passed", 2)]


def test_ok_is_none_without_data():
    assert parse("", exit_code=None).ok is None
