"""Live output view for a running script."""

from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.reactive import var
from textual.widgets import RichLog

#: Hard ceiling on retained lines; older output is dropped from the top.
MAX_LINES = 10_000


class OutputPane(RichLog):
    """Streams a child process' output, with scroll-follow that yields."""

    DEFAULT_CSS = """
    OutputPane {
        background: $surface;
        color: $text;
        padding: 0 1;
        scrollbar-color: $accent 20%;
        scrollbar-background: $panel;
    }
    """

    BINDINGS = [
        Binding("end", "follow", "Follow", show=False),
        Binding("ctrl+end", "follow", "Follow", show=False),
    ]

    #: True while the viewport is pinned to the newest line.
    follow = var(True)

    def on_mount(self) -> None:
        self.auto_scroll = True
        self.max_lines = MAX_LINES

    # -- writing --------------------------------------------------------

    def write_text(self, text: Text) -> None:
        """Append a chunk, preserving the follow state."""
        if not text.plain:
            return
        self.auto_scroll = self.follow
        self.write(text)

    def write_line(self, content: str, style: str = "") -> None:
        text = Text(content, style=style) if style else Text(content)
        self.write_text(text)

    def write_rule(self, content: str) -> None:
        self.write_text(Text(content, style="dim italic"))

    # -- follow behaviour ------------------------------------------------

    def watch_scroll_y(self, scroll_y: float) -> None:
        # Scrolling up detaches the view; returning to the bottom re-attaches.
        self.set_reactive(OutputPane.follow, self.is_vertical_scroll_end)

    def on_scroll_end(self) -> None:
        self.follow = True

    def on_scroll_home(self) -> None:
        self.follow = False

    def action_follow(self) -> None:
        self.follow = True
        self.scroll_end(animate=False)

    def jump_to_end(self) -> None:
        self.follow = True
        self.scroll_end(animate=False)

    def toggle_follow(self) -> None:
        self.follow = not self.follow
        if self.follow:
            self.scroll_end(animate=True)
