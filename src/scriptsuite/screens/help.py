"""Modal help / keybinding reference."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "scripts",
        [
            ("↑ ↓  j k", "move through the list"),
            ("enter", "run the highlighted script"),
            ("/", "filter the list"),
            ("escape", "clear the filter / leave a field"),
            ("f5", "rescan the scripts folder"),
            ("ctrl+a", "run every visible script in sequence"),
            ("ctrl+shift+k", "cancel a run-all sequence"),
        ],
    ),
    (
        "run control",
        [
            ("ctrl+r", "re-run the current script"),
            ("ctrl+k", "stop the run (SIGTERM, then SIGKILL)"),
            ("ctrl+c", "interrupt the run (SIGINT)"),
            ("ctrl+l", "clear the output"),
        ],
    ),
    (
        "stdin",
        [
            ("f2", "jump to the input bar"),
            ("enter", "send the line to the script"),
            ("↑ ↓", "recall previous input"),
            ("f6", "raw mode: forward keys verbatim"),
            ("f6 again", "leave raw mode (escape itself is forwarded)"),
            ("escape", "give focus back to the list in line mode"),
            ("…", "focus jumps here on its own at a prompt"),
        ],
    ),
    (
        "arguments",
        [
            ("f3", "jump to the first argument control"),
            ("tab", "next argument; switches are checkboxes"),
            ("space", "toggle a switch"),
            ("ctrl+u", "clear every argument"),
            ("escape", "hand focus back to the list"),
            ("↑ ↓", "the pane follows the highlighted script"),
        ],
    ),
    (
        "layout",
        [
            ("f7", "expand / restore the source pane"),
            ("f9", "toggle the right pane"),
            ("f10", "toggle the bottom pane"),
            ("f11", "toggle the sidebar"),
            ("mouse wheel", "scroll the source pane"),
        ],
    ),
    (
        "output",
        [
            ("↑ ↓  page up/down", "scroll the output"),
            ("end", "jump to the newest line and re-follow"),
        ],
    ),
    (
        "general",
        [
            ("ctrl+p", "command palette"),
            ("f1", "this help"),
            ("ctrl+q", "quit"),
        ],
    ),
]


class HelpScreen(ModalScreen[None]):
    """Keybinding reference overlay."""

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
        background: $background 60%;
    }
    HelpScreen > HelpPanel {
        width: 76;
        max-width: 96%;
        height: auto;
        max-height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    HelpPanel .heading {
        width: 1fr;
        height: 1;
        text-style: bold;
        margin-bottom: 1;
    }
    HelpPanel .body {
        height: auto;
    }
    HelpPanel .group-name {
        width: 1fr;
        height: 1;
        color: $accent;
        text-style: bold;
        margin-top: 1;
    }
    HelpPanel .row {
        width: 1fr;
        height: 1;
    }
    HelpPanel .row .key {
        color: #7aa2f7;
    }
    HelpPanel .row .label {
        color: $text-muted;
    }
    HelpPanel .footer {
        width: 1fr;
        height: 1;
        margin-top: 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close", priority=True),
        Binding("q", "dismiss", "Close", show=False),
        Binding("f1", "dismiss", "Close", show=False, priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(classes="HelpPanel"):
            yield Static(Text("scriptsuite — keys", style="bold"), classes="heading")
            with VerticalScroll(classes="body"):
                for name, rows in SECTIONS:
                    yield Static(Text(name.upper(), style="bold"), classes="group-name")
                    for key, label in rows:
                        yield Static(self._row(key, label), classes="row")
            yield Static(Text("dismiss with escape", style="italic"), classes="footer")

    def _row(self, key: str, label: str) -> Text:
        text = Text()
        text.append(f"{key:<18}", style="#7aa2f7")
        text.append(label, style="grey70")
        return text

    def action_dismiss(self) -> None:
        self.dismiss(None)
