"""Demonstrates the arguments pane: this script declares real options.

The right-hand panel reads them out of this file, so moving the selection
onto it shows what is available.  Type into ARGUMENTS and they are appended to
the command on the next run.
"""

from __future__ import annotations

import argparse
import sys
import time


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="with_options.py",
        description="Report how a payload should be delivered.",
    )
    parser.add_argument("target", help="host or address to deliver to")
    parser.add_argument("-v", "--verbose", action="store_true", help="log every step")
    parser.add_argument("-r", "--retries", type=int, default=3, help="attempts before giving up")
    parser.add_argument("--mode", choices=["fast", "safe", "audit"], default="safe",
                        help="trade speed for caution")
    parser.add_argument("--tag", action="append", help="label the run; repeatable")
    parser.add_argument("--out", metavar="FILE", help="write the report here")
    parser.add_argument("--dry-run", action="store_true", required=False,
                        help="show what would happen, change nothing")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    print(f"target : {args.target}")
    print(f"mode   : {args.mode}")
    print(f"retries: {args.retries}")

    if args.tag:
        print(f"tags   : {', '.join(args.tag)}")
    if args.out:
        print(f"report : would write to {args.out}")
    if args.dry_run:
        print("dry run: nothing was delivered")

    if args.verbose:
        for step in range(1, 4):
            print(f"  step {step}: preparing payload for {args.target}")
            time.sleep(0.15)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())