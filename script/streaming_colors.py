"""Streams coloured output and a progress bar for a few seconds.

Shows that ANSI colour and carriage-return repainting survive the pty round
trip.  Ctrl-C stops it early.
"""

from __future__ import annotations

import sys
import time

RESET = "\x1b[0m"
DIM = "\x1b[38;5;240m"
BLUE = "\x1b[38;5;111m"
GREEN = "\x1b[38;5;114m"

STEPS = 40


def main() -> int:
    print(f"{BLUE}starting streaming sample{RESET}")
    time.sleep(0.3)

    for step in range(1, STEPS + 1):
        filled = int(20 * step / STEPS)
        bar = "█" * filled + "░" * (20 - filled)
        percent = step * 100 // STEPS
        sys.stdout.write(f"\r{DIM}progress{RESET} [{GREEN}{bar}{RESET}] {percent:3d}%")
        sys.stdout.flush()
        time.sleep(0.05)

    print(f"\r{GREEN}✔ done{RESET} — streamed {STEPS} updates")
    for index, colour in enumerate([BLUE, GREEN, DIM], start=1):
        print(f"  sample line {index} {colour}with colour{RESET}")
        time.sleep(0.1)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())