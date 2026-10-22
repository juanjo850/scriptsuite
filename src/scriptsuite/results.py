"""Incremental parsing of common test-runner output into a summary."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# pytest summary line: "=== 3 passed, 1 failed in 0.42s ==="
_PYTEST_COUNT = re.compile(
    r"\b(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed)\b"
)
_PYTEST_SUMMARY = re.compile(
    r"=+(?!=)\s*"
    r"(?P<counts>(?:\d+\s+(?:passed|failed|error|errors|skipped|xfailed|xpassed)\b\s*,?\s*)+)"
    r"(?:\s*in\s+(?P<duration>\d+(?:\.\d+)?)s)?"
    r"\s*=+"
)
_PYTEST_TAIL = re.compile(r"\bin\s+(\d+(?:\.\d+)?)s\b")

# unittest: "Ran 12 tests in 0.031s" followed by "OK" / "FAILED (errors=1)"
_UNITTEST_RAN = re.compile(r"^Ran\s+(\d+)\s+tests?\s+in\s+(\d+(?:\.\d+)?)s", re.MULTILINE)
_UNITTEST_VERDICT = re.compile(r"^(OK|FAILED)\b.*$", re.MULTILINE)
_UNITTEST_ERRORS = re.compile(r"errors=(\d+)")
_UNITTEST_FAILURES = re.compile(r"failures=(\d+)")

# TAP / bats: "ok 1 - name", "not ok 2 - name"
_TAP_OK = re.compile(r"^\s*ok\s+\d+", re.MULTILINE)
_TAP_NOT_OK = re.compile(r"^\s*not ok\s+\d+", re.MULTILINE)
_TAP_PLAN = re.compile(r"^(\d+)\.\.(\d+)\s*$", re.MULTILINE)

# Generic "12 tests passed" phrasing used by assorted runners.
_GENERIC_TOTAL = re.compile(r"\b(\d+)\s+tests?\b", re.IGNORECASE)

# A failure header emitted by pytest / unittest.
_FAILURE_HEAD = re.compile(
    r"^(?:_{3,}|={3,})\s*(FAILURES|ERRORS)\s*(?:={3,}|_{3,})?", re.MULTILINE
)
# Failure names come in three shapes depending on the runner and verbosity:
#   _____ test_thing _____        (pytest default)
#   FAILED path::test_thing - ... (pytest short summary)
#   FAIL: test_thing              (unittest / pytest -rf)
_FAILURE_CASE = re.compile(
    r"^(?:_{2,}\s*(?P<name1>.+?)\s*_{2,}"
    r"|(?:FAIL|ERROR)(?:ED)?:\s*(?P<name2>\S.*?)"
    r"|(?:FAILED|ERROR)\s+(?P<name3>\S+))"
    r"\s*(?:-.*)?$",
    re.MULTILINE,
)

_COUNT_KEYS = {
    "passed": "passed",
    "failed": "failed",
    "error": "errors",
    "errors": "errors",
    "skipped": "skipped",
    "xfailed": "xfailed",
    "xpassed": "xpassed",
}


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


@dataclass
class TestSummary:
    """Rolling summary of a single run's results."""

    framework: str | None = None
    passed: int | None = None
    failed: int | None = None
    errors: int | None = None
    skipped: int | None = None
    xfailed: int | None = None
    xpassed: int | None = None
    total: int | None = None
    duration: float | None = None
    failures: list[str] = field(default_factory=list)

    @property
    def has_data(self) -> bool:
        return any(
            value is not None
            for value in (
                self.passed,
                self.failed,
                self.errors,
                self.skipped,
                self.xfailed,
                self.xpassed,
            )
        )

    @property
    def ok(self) -> bool | None:
        """``True`` all good, ``False`` something failed, ``None`` unknown."""
        if not self.has_data:
            return None
        return not (self.failed or self.errors)

    def counts(self) -> list[tuple[str, int]]:
        pairs = (
            ("passed", self.passed),
            ("failed", self.failed),
            ("errors", self.errors),
            ("skipped", self.skipped),
            ("xfailed", self.xfailed),
            ("xpassed", self.xpassed),
        )
        return [(label, value) for label, value in pairs if value]

    def summary_line(self) -> str:
        if not self.has_data:
            return ""
        parts = [f"{value} {label}" for label, value in self.counts()]
        if self.total and not parts:
            parts.append(f"{self.total} tests")
        if self.duration is not None:
            parts.append(f"in {self.duration:.2f}s")
        return ", ".join(parts)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _first_int(pattern: re.Pattern[str], text: str) -> int:
    match = pattern.search(text)
    return int(match.group(1)) if match else 0


class ResultParser:
    """Feed it text as it streams in; query the summary at any point.

    A small overlap window is retained between feeds so that a marker split
    across two chunks is still recognised.
    """

    #: Characters of previous output kept for boundary-spanning matches.
    OVERLAP = 8192

    def __init__(self) -> None:
        self._window = ""
        self.summary = TestSummary()
        #: TAP is counted cumulatively: lines are parsed once as they arrive,
        #: so a long stream cannot lose counts when it scrolls out of the
        #: overlap window.
        self._tap_passed = 0
        self._tap_failed = 0
        self._tap_carry = ""
        self._tap_seen = False

    def reset(self) -> None:
        self._window = ""
        self.summary = TestSummary()
        self._tap_passed = 0
        self._tap_failed = 0
        self._tap_carry = ""
        self._tap_seen = False

    # ------------------------------------------------------------------

    def feed(self, text: str) -> None:
        if not text:
            return
        combined = self._window + text
        self._consume(combined)
        self._feed_tap(text)
        self._window = combined[-self.OVERLAP :]

    def finalize(self, exit_code: int | None = None) -> TestSummary:
        """Fold the exit code in, without inventing counts it cannot know.

        A script that prints nothing and exits 0 did not "pass one test"; it
        simply succeeded.  Counts stay unset so the UI can say nothing rather
        than something misleading.
        """
        summary = self.summary
        # A final TAP line may lack its newline.
        if self._tap_carry:
            self._feed_tap("\n")
        if not summary.has_data:
            if exit_code is not None:
                summary.framework = summary.framework or "exit-code"
            return summary

        if summary.total is None:
            known = sum(
                value
                for value in (
                    summary.passed,
                    summary.failed,
                    summary.errors,
                    summary.skipped,
                    summary.xfailed,
                    summary.xpassed,
                )
                if value
            )
            summary.total = known or None
        return summary

    # ------------------------------------------------------------------

    def _consume(self, text: str) -> None:
        self._parse_failures(text)
        if self._parse_pytest(text):
            return
        if self._parse_unittest(text):
            return
        self._parse_generic(text)

    def _parse_failures(self, text: str) -> None:
        if not _FAILURE_HEAD.search(text):
            return
        known = self.summary.failures
        for match in _FAILURE_CASE.finditer(text):
            name = (
                match.group("name1") or match.group("name2") or match.group("name3") or ""
            ).strip()
            # Keep the bare test name: the same failure appears both as
            # "___ test_x ___" and as "path/to/file.py::test_x" in the short
            # summary, and the module path adds nothing in the UI.
            name = name.split()[-1].split("::")[-1] if name else name
            if name and name not in known:
                known.append(name)
        del known[20:]

    def _parse_pytest(self, text: str) -> bool:
        summary = self.summary

        # Preferred path: the delimited "=== 3 passed, 1 failed in 0.42s ==="
        # summary line, which is unambiguous even when earlier lines mention
        # test counts in passing.
        strict = list(_PYTEST_SUMMARY.finditer(text))
        if strict:
            summary.framework = "pytest"
            # The latest summary replaces the previous one; otherwise a runner
            # that prints two summaries reports the union of both.
            for attribute in _COUNT_KEYS.values():
                setattr(summary, attribute, None)
            self._apply_counts(strict[-1].group("counts"), summary)
            if strict[-1].group("duration"):
                summary.duration = float(strict[-1].group("duration"))
            return True

        # Fallback: an undecorated "N passed, M failed in Xs" line.
        matches = list(_PYTEST_COUNT.finditer(text))
        if not matches:
            return False

        summary.framework = "pytest"
        start = text.rfind("\n", 0, matches[-1].start()) + 1
        on_last_line = [match for match in matches if match.start() >= start] or matches[
            -1:
        ]
        for attribute in _COUNT_KEYS.values():
            setattr(summary, attribute, None)
        self._apply_counts(" ".join(match.group(0) for match in on_last_line), summary)

        tail = _PYTEST_TAIL.search(text[start:])
        if tail:
            summary.duration = float(tail.group(1))
        return True

    @staticmethod
    def _apply_counts(fragment: str, summary: TestSummary) -> None:
        for match in _PYTEST_COUNT.finditer(fragment):
            attribute = _COUNT_KEYS.get(match.group(2))
            if attribute:
                setattr(summary, attribute, int(match.group(1)))

    def _parse_unittest(self, text: str) -> bool:
        ran = _UNITTEST_RAN.search(text)
        if not ran and "unittest" not in text.lower():
            return False

        summary = self.summary
        if ran:
            summary.framework = summary.framework or "unittest"
            summary.total = int(ran.group(1))
            summary.duration = float(ran.group(2))

        verdict = _UNITTEST_VERDICT.search(text)
        if not verdict:
            return ran is not None

        summary.framework = summary.framework or "unittest"
        if verdict.group(1) == "OK":
            summary.passed = summary.total if summary.total is not None else 1
            summary.failed = summary.errors = 0
            return True

        errors = _first_int(_UNITTEST_ERRORS, verdict.group(0))
        failures = _first_int(_UNITTEST_FAILURES, verdict.group(0))
        summary.errors = errors or None
        summary.failed = failures or (0 if errors else 1)
        summary.passed = None
        return True

    def _parse_tap(self, text: str) -> bool:
        """Count TAP lines in a complete chunk.

        Kept for callers that want a one-shot scan; :meth:`feed` uses the
        cumulative :meth:`_feed_tap` instead.
        """
        ok = len(_TAP_OK.findall(text))
        not_ok = len(_TAP_NOT_OK.findall(text))
        if not (ok or not_ok):
            return False

        self._tap_passed += ok
        self._tap_failed += not_ok
        self._tap_seen = True
        self._publish_tap()
        plan = _TAP_PLAN.search(text)
        if plan:
            self.summary.total = int(plan.group(2))
        return True

    def _feed_tap(self, text: str) -> None:
        """Parse only newly arrived TAP lines, carrying the last partial line."""
        data = self._tap_carry + text
        lines = data.split("\n")
        self._tap_carry = lines.pop()
        # A TAP line is short; a huge unterminated line is not TAP and must not
        # become an unbounded buffer.
        if len(self._tap_carry) > 4096:
            self._tap_carry = self._tap_carry[-4096:]
        for line in lines:
            if _TAP_NOT_OK.match(line):
                self._tap_failed += 1
            elif _TAP_OK.match(line):
                self._tap_passed += 1
            else:
                plan = _TAP_PLAN.match(line)
                if plan:
                    self.summary.total = int(plan.group(2))
                    self._tap_seen = True
        if self._tap_passed or self._tap_failed:
            self._tap_seen = True
        if self._tap_seen:
            self._publish_tap()

    def _publish_tap(self) -> None:
        self.summary.framework = self.summary.framework or "tap"
        self.summary.passed = self._tap_passed
        self.summary.failed = self._tap_failed

    def _parse_generic(self, text: str) -> None:
        summary = self.summary
        total = _GENERIC_TOTAL.search(text)
        if total and summary.total is None:
            summary.total = int(total.group(1))
        if "passed" in text and summary.passed is None:
            match = re.search(r"\b(\d+)\s+passed\b", text)
            if match:
                summary.framework = summary.framework or "generic"
                summary.passed = int(match.group(1))
