"""
Floating modal/dialog system.
Supports input, confirmation, selection modals.
"""

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static, Input, Button, Label, ListView, ListItem
from textual.containers import Vertical, Horizontal, Center
from textual.screen import ModalScreen
from textual.message import Message
from textual import events
from typing import Any, Callable, List, Optional, Tuple


class InputModal(ModalScreen[str]):
    """Modal dialog with text input."""

    DEFAULT_CSS = """
    InputModal {
        align: center middle;
    }
    InputModal #modal-container {
        background: $panel;
        border: double $primary;
        padding: 1 2;
        width: 60;
        height: auto;
        max-height: 12;
    }
    InputModal #modal-title {
        text-style: bold;
        color: $primary;
        text-align: center;
        margin-bottom: 1;
    }
    InputModal Input {
        margin: 1 0;
    }
    InputModal #modal-buttons {
        align: center middle;
        margin-top: 1;
    }
    """

    def __init__(
        self,
        title: str = "Input",
        prompt: str = "",
        default: str = "",
        placeholder: str = "",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.title_text = title
        self.prompt_text = prompt
        self.default_value = default
        self.placeholder_text = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            yield Static(self.title_text, id="modal-title")
            if self.prompt_text:
                yield Label(self.prompt_text)
            yield Input(
                value=self.default_value,
                placeholder=self.placeholder_text,
                id="modal-input",
            )
            with Horizontal(id="modal-buttons"):
                yield Button("OK", variant="primary", id="btn-ok")
                yield Button("Cancel", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-ok":
            inp = self.query_one("#modal-input", Input)
            self.dismiss(inp.value)
        else:
            self.dismiss("")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.dismiss("")
            event.stop()


class ConfirmModal(ModalScreen[bool]):
    """Modal dialog for yes/no confirmation."""

    DEFAULT_CSS = """
    ConfirmModal {
        align: center middle;
    }
    ConfirmModal #confirm-container {
        background: $panel;
        border: double $primary;
        padding: 1 2;
        width: 50;
        height: auto;
        max-height: 10;
    }
    ConfirmModal #confirm-title {
        text-style: bold;
        color: $primary;
        text-align: center;
        margin-bottom: 1;
    }
    ConfirmModal #confirm-buttons {
        align: center middle;
        margin-top: 1;
    }
    """

    def __init__(self, title: str = "Confirm", message: str = "Are you sure?", **kwargs):
        super().__init__(**kwargs)
        self.title_text = title
        self.message_text = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-container"):
            yield Static(self.title_text, id="confirm-title")
            yield Label(self.message_text)
            with Horizontal(id="confirm-buttons"):
                yield Button("Yes", variant="primary", id="btn-yes")
                yield Button("No", id="btn-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-yes")

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.dismiss(False)
            event.stop()
        elif event.key == "y":
            self.dismiss(True)
            event.stop()
        elif event.key == "n":
            self.dismiss(False)
            event.stop()


class SelectionModal(ModalScreen[str]):
    """Modal for selecting from a list of items."""

    DEFAULT_CSS = """
    SelectionModal {
        align: center middle;
    }
    SelectionModal #select-container {
        background: $panel;
        border: double $primary;
        padding: 1 2;
        width: 60;
        height: auto;
        max-height: 70%;
    }
    SelectionModal #select-title {
        text-style: bold;
        color: $primary;
        text-align: center;
        margin-bottom: 1;
    }
    SelectionModal ListView {
        height: auto;
        max-height: 20;
        margin: 1 0;
    }
    SelectionModal ListItem {
        padding: 0 1;
    }
    SelectionModal ListItem:hover {
        background: $primary 15%;
    }
    """

    def __init__(
        self,
        title: str = "Select",
        items: Optional[List[Tuple[str, str]]] = None,
        **kwargs,
    ) -> None:
        """
        Args:
            title: Modal title
            items: List of (value, display_text) tuples
        """
        super().__init__(**kwargs)
        self.title_text = title
        self.items = items or []

    def compose(self) -> ComposeResult:
        with Vertical(id="select-container"):
            yield Static(self.title_text, id="select-title")
            with ListView(id="select-list"):
                for value, display in self.items:
                    yield ListItem(Label(display), id=f"sel-{value}", name=value)
            with Horizontal():
                yield Button("Cancel", id="btn-cancel")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item and event.item.name:
            self.dismiss(event.item.name)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss("")

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.dismiss("")
            event.stop()