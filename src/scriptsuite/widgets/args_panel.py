"""Right-hand pane: arguments for the highlighted script, and its options.

Every declared argument gets its own control -- a text field for values, a
checkbox for switches -- and positionals are emitted in order.  Values are
remembered per script.  A free-form box underneath takes anything the script
declares nowhere.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Checkbox, Input, Label, Static

from ..discovery import ScriptInfo
from ..options import Field, ProgramInfo

#: Text is clipped so one wordy flag cannot dominate the pane.
_TEXT_WIDTH = 34


class FieldRow(Vertical):
    """One argument: its label, a control, and a hint line."""

    DEFAULT_CSS = """
    FieldRow {
        width: 1fr;
        height: 3;
        layout: vertical;
    }
    FieldRow.--flag {
        height: 2;
    }
    FieldRow > .label {
        width: 1fr;
        height: 1;
        text-style: bold;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    FieldRow > .hint {
        width: 1fr;
        height: 1;
        color: $text-muted;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    FieldRow > Input {
        width: 1fr;
        height: 1;
        border: none;
        border-bottom: solid $border;
        background: $surface;
        padding: 0 1;
    }
    FieldRow > Input:focus {
        border-bottom: solid $accent;
        background: $boost;
    }
    /* A switch needs no row of its own: the box shares the label's line. */
    FieldRow > .flagline {
        width: 1fr;
        height: 1;
        layout: horizontal;
    }
    FieldRow > .flagline > Checkbox {
        width: 1fr;
        min-width: 4;
        height: 1;
        border: none;
        background: transparent;
        padding: 0;
        margin-right: 1;
        text-overflow: ellipsis;
    }
    FieldRow > .flagline > Checkbox:focus {
        background: $boost;
    }
    FieldRow > .flagline > .label {
        width: 1fr;
    }
    """

    class Changed(Message):
        """A control in this row changed."""

        def __init__(
            self, script_key: str | None, field_id: str, value: str | bool
        ) -> None:
            super().__init__()
            self.script_key = script_key
            self.field_id = field_id
            self.value = value

    def __init__(
        self,
        field: Field,
        value: str | bool = "",
        script_key: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.field = field
        self._value = value
        self.script_key = script_key
        #: Values pushed in programmatically.  The matching change message is
        #: still delivered asynchronously, so it cannot be suppressed with a
        #: simple "currently applying" flag.
        self._silent_input: str | None = None
        self._silent_checkbox: bool | None = None

    def compose(self) -> ComposeResult:
        label = Static(self._label(), classes="label")
        if self.field.takes_value:
            yield label
            yield Input(
                placeholder=self.field.placeholder or "value…",
                id=f"arg-{self.field.id}",
                compact=True,
            )
        else:
            self.add_class("--flag")
            with Horizontal(classes="flagline"):
                # The checkbox carries the label so screen readers have a name
                # for the control instead of an unnamed box.
                yield Checkbox(self.field.label, id=f"arg-{self.field.id}")
        yield Static(Text(self.field.help, style="grey58"), classes="hint")

    def on_mount(self) -> None:
        # The control exists only once this row is composed, so the stored
        # value is applied here rather than by the caller.
        self.apply(self._value)

    def _label(self) -> Text:
        text = Text(self.field.label, style="bold #7dcfff")
        if self.field.positional:
            text.append("  positional", style="grey42")
        return text

    # -- values ---------------------------------------------------------

    def apply(self, value: str | bool) -> None:
        """Set the control without echoing a change back to the app."""
        if self.field.takes_value:
            widget = self.query_one(Input)
            text = str(value or "")
            if widget.value == text:
                self._silent_input = None
                return
            self._silent_input = text
            widget.value = text
        else:
            widget = self.query_one(Checkbox)
            checked = bool(value)
            if bool(widget.value) == checked:
                self._silent_checkbox = None
                return
            self._silent_checkbox = checked
            widget.value = checked

    def current(self) -> str | bool:
        if self.field.takes_value:
            return self.query_one(Input).value
        return bool(self.query_one(Checkbox).value)

    def _on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        if self._silent_input is not None and event.value == self._silent_input:
            self._silent_input = None
            return
        self.post_message(self.Changed(self.script_key, self.field.id, event.value))

    def _on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        event.stop()
        if (
            self._silent_checkbox is not None
            and bool(event.value) == self._silent_checkbox
        ):
            self._silent_checkbox = None
            return
        self.post_message(self.Changed(self.script_key, self.field.id, bool(event.value)))


class ArgsPanel(Vertical):
    """Per-script arguments, one control each, plus a free-form escape hatch."""

    DEFAULT_CSS = """
    ArgsPanel {
        width: 38;
        height: 1fr;
        border: none;
        border-left: solid $border;
        padding: 0 1;
        layout: vertical;
    }
    ArgsPanel:focus-within {
        border: none;
        border-left: solid $accent;
    }
    ArgsPanel > .head {
        width: 1fr;
        height: 1;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    ArgsPanel > .section {
        width: 1fr;
        height: 1;
        color: $accent;
        text-style: bold;
        margin-top: 1;
    }
    ArgsPanel > #args-input {
        width: 1fr;
        height: 3;
        border: none;
        border-bottom: solid $border;
        background: $surface;
        color: $text;
        padding: 0 1;
    }
    ArgsPanel > #args-input:focus {
        border-bottom: solid $accent;
        background: $boost;
    }
    ArgsPanel > #args-fields {
        width: 1fr;
        height: 1fr;
        margin-top: 0;
        padding: 0;
        scrollbar-size-vertical: 1;
    }
    ArgsPanel .note {
        width: 1fr;
        height: 1;
        color: $text-muted;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    ArgsPanel .note.summary {
        color: $text;
    }
    ArgsPanel .note.empty {
        color: $text-muted;
        text-style: italic;
    }
    """

    BINDINGS = [
        Binding("escape", "release", "Back to scripts", show=False, priority=True),
    ]

    class Dismiss(Message):
        """Escape was pressed; the app should return focus to the list."""

    class ValueChanged(Message):
        """One of the field controls changed."""

        def __init__(
            self, script_key: str | None, field_id: str, value: str | bool
        ) -> None:
            super().__init__()
            self.script_key = script_key
            self.field_id = field_id
            self.value = value

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.key: str | None = None
        self._program = ProgramInfo(language="")
        #: Summary shown above the fields, used to notice when a rebuild is
        #: needed even though the field set is unchanged.
        self._summary_text = ""
        #: Set while values are being pushed in, so echoing them back as
        #: change events would be noise.
        self._muted = False

    def compose(self) -> ComposeResult:
        yield Static(Text(""), classes="head", id="args-head")
        yield Label("ARGUMENTS", classes="section")
        yield VerticalScroll(classes="fields", id="args-fields")
        yield Label("EXTRA", classes="section")
        yield Input(placeholder="anything else…", id="args-input", compact=True)

    # -- content --------------------------------------------------------

    def show(
        self,
        info: ScriptInfo | None,
        values: dict[str, str | bool],
        extra: str,
        program: ProgramInfo,
    ) -> None:
        """Point the pane at ``info`` and draw its controls."""
        self.key = info.key if info else None
        self._program = program

        head = self.query("#args-head")
        if head:
            head[0].update(self._head(info))

        self._render_fields(info, values)

        extra_field = self.query("#args-input")
        if not extra_field:
            return
        field = extra_field[0]
        self._muted = True
        try:
            if field.value != extra:
                field.value = extra
        finally:
            self._muted = False
        field.disabled = info is None

    def _head(self, info: ScriptInfo | None) -> Text:
        text = Text()
        if info is None:
            text.append("no script selected", style="grey58 italic")
            return text
        text.append(info.rel_path, style="bold")
        text.append(f"  {info.language}", style="grey42")
        text.truncate(_TEXT_WIDTH, overflow="ellipsis")
        return text

    def _render_fields(
        self, info: ScriptInfo | None, values: dict[str, str | bool]
    ) -> None:
        container = self.query("#args-fields")
        if not container:
            return
        box = container[0]
        fields = self._program.fields() if info is not None else ()
        summary = self._program.summary if info is not None else ""
        existing = list(box.query(FieldRow))
        if (
            info is not None
            and existing
            and self._summary_text == summary
            and [row.field for row in existing] == list(fields)
        ):
            # Same script interface: update values in place so focus, cursor
            # position and scroll offset survive a highlight change.
            for row in existing:
                row.script_key = self.key
                row.apply(
                    values.get(row.field.id, "" if row.field.takes_value else False)
                )
            return
        self._summary_text = summary
        box.remove_children()
        box.mount(*self._field_widgets(info, values))

    def _field_widgets(
        self, info: ScriptInfo | None, values: dict[str, str | bool]
    ) -> list[Any]:
        if info is None:
            return [
                Static(Text("no script selected", style="grey58 italic"), classes="note")
            ]

        program = self._program
        widgets: list[Any] = []

        if program.summary:
            widgets.append(
                Static(
                    Text(self._clip(program.summary), style="grey70"),
                    classes="note summary",
                )
            )

        fields = program.fields()
        if fields:
            for field in fields:
                default = values.get(field.id, "" if field.takes_value else False)
                widgets.append(FieldRow(field, default, script_key=self.key))
        else:
            hint = program.positional_hint or "no options declared"
            widgets.append(
                Static(
                    Text(self._clip(hint), style="grey58 italic"), classes="note empty"
                )
            )

        return widgets

    def apply_values(self, values: dict[str, str | bool]) -> None:
        """Push stored values into the controls without firing events."""
        rows = list(self.query(FieldRow))
        if not rows:
            return
        self._muted = True
        try:
            for row in rows:
                if not row.is_mounted:
                    continue
                row.apply(
                    values.get(row.field.id, "" if row.field.takes_value else False)
                )
        finally:
            self._muted = False

    def current_values(self) -> dict[str, str | bool]:
        return {row.field.id: row.current() for row in self.query(FieldRow)}

    @staticmethod
    def _clip(text: str) -> str:
        return text[:_TEXT_WIDTH]

    # -- focus ----------------------------------------------------------

    def action_release(self) -> None:
        """Give focus back so Enter means "run" again."""
        for widget in self.query("Input, Checkbox"):
            if widget.has_focus:
                widget.blur()
        self.post_message(self.Dismiss())

    def on_field_row_changed(self, event: FieldRow.Changed) -> None:
        event.stop()
        if self._muted:
            return
        self.post_message(
            self.ValueChanged(event.script_key, event.field_id, event.value)
        )
