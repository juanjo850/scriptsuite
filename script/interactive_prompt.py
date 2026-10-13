"""Reads from stdin so you can exercise the input bar and raw mode.

Prompts, echoes, and validates; type values then press enter, or hit F6 and
send keystrokes verbatim.
"""

from __future__ import annotations

import sys


def prompt(message: str) -> str:
    sys.stdout.write(message)
    sys.stdout.flush()
    return sys.stdin.readline().strip()


def main() -> int:
    print("interactive sample — type answers below, Ctrl-C or 'quit' to exit")
    total = 0.0
    turn = 1

    while True:
        answer = prompt(f"[{turn}] number > ")
        if answer.lower() in {"quit", "exit", "q"}:
            print("\nbye")
            return 0
        if not answer:
            print("  (skipped)")
            turn += 1
            continue

        try:
            value = float(answer)
        except ValueError:
            print(f"  not a number: {answer!r}")
            turn += 1
            continue

        total += value
        print(f"  running total: {total:g}")
        turn += 1


if __name__ == "__main__":
    raise SystemExit(main())