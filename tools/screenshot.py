"""Render the workspace headlessly and export it as SVG.

Used to keep the README's screenshot in step with the real interface.  Run it
with no arguments to refresh ``docs/screenshot.svg``::

    python tools/screenshot.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from scriptsuite.app import ScriptSuiteApp  # noqa: E402

SCRIPTS = ROOT / "script"
OUTPUT = ROOT / "docs" / "screenshot.svg"

WIDTH, HEIGHT = 132, 46
TARGET = "with_options"

#: A run that shows a pass, some flags and a real command line.
ARGUMENTS = {
    "pos0": "db-01",
    "--mode": "audit",
    "--out": "report.txt",
    "--verbose": True,
}


async def shoot() -> str:
    app = ScriptSuiteApp(scripts_dir=SCRIPTS)
    async with app.run_test(size=(WIDTH, HEIGHT)) as pilot:
        for _ in range(60):
            await pilot.pause()
            if app.query_one("#sidebar-list").rows():
                break
        await pilot.pause()

        target = next(info for info in app.scripts if info.name == TARGET)
        app.arguments[target.key] = ARGUMENTS
        app.query_one("#sidebar-list").focus_script(target.key)
        await pilot.pause()

        app.action_rerun()
        for _ in range(200):
            await pilot.pause(0.05)
            if app.active is None:
                break
        await pilot.pause(0.3)
        # No rescan here: it would clear the output pane we just filled.
        return app.export_screenshot(title="scriptsuite")


def main() -> int:
    svg = asyncio.run(shoot())
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(svg, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}  ({len(svg):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
