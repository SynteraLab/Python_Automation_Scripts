"""
Navigation sidebar widget.
Collapsible, keyboard-navigable, mode-aware.
"""

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static, Button
from textual.containers import Vertical, ScrollableContainer
from textual.reactive import reactive
from textual.message import Message
from typing import List, Tuple


class NavItem(Static):
    """Single navigation item in sidebar."""

    class Selected(Message):
        def __init__(self, item_id: str, label: str) -> None:
            self.item_id = item_id
            self.label = label
            super().__init__()

    DEFAULT_CSS = """
    NavItem {
        padding: 0 1;
        height: 3;
        content-align: left middle;
    }
    NavItem:hover {
        background: $surface;
    }
    NavItem.--active {
        background: $primary 15%;
        color: $primary;
        text-style: bold;
    }
    """

    is_active: reactive[bool] = reactive(False)

    def __init__(self, item_id: str, icon: str, label: str, **kwargs) -> None:
        super().__init__(f" {icon}  {label}", **kwargs)
        self.item_id = item_id
        self.icon = icon
        self.label_text = label

    def watch_is_active(self, value: bool) -> None:
        self.set_class(value, "--active")

    def on_click(self) -> None:
        self.post_message(self.Selected(self.item_id, self.label_text))


class SidebarSection(Static):
    """Section header in sidebar."""

    DEFAULT_CSS = """
    SidebarSection {
        padding: 1 1 0 1;
        color: $text-primary;
        text-style: bold;
        height: 2;
    }
    """


class Sidebar(Widget):
    """
    Navigation sidebar with sections and items.
    Supports keyboard navigation and dynamic content.
    """

    DEFAULT_CSS = """
    Sidebar {
        width: 28;
        background: $surface;
        border-right: solid $border;
    }
    Sidebar.hidden {
        display: none;
    }
    Sidebar #sidebar-title {
        text-align: center;
        text-style: bold;
        color: $primary;
        padding: 1 0;
        height: 3;
        border-bottom: solid $border;
    }
    """

    class ItemSelected(Message):
        def __init__(self, item_id: str) -> None:
            self.item_id = item_id
            super().__init__()

    visible: reactive[bool] = reactive(True)
    active_item: reactive[str] = reactive("")

    # Default navigation items
    NAV_ITEMS: List[Tuple[str, str, str, str]] = [
        # (section, id, icon, label)
        ("Download", "download", "🎯", "Smart Download"),
        ("Download", "audio", "🎵", "Audio Only"),
        ("Download", "batch", "📦", "Batch Download"),
        ("Download", "batch_file", "📄", "From File"),
        ("Download", "erome", "📸", "EroMe Album"),
        ("Tools", "info", "ℹ️", "Video Info"),
        ("Tools", "extractors", "🔧", "Extractors"),
        ("Tools", "sites", "🌐", "Supported Sites"),
        ("View", "history", "📊", "History"),
        ("View", "dashboard", "📈", "Dashboard"),
        ("System", "settings", "⚙️", "Settings"),
        ("System", "workflows", "🔄", "Workflows"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._items: dict = {}

    def compose(self) -> ComposeResult:
        yield Static("🎬 Ultra DL", id="sidebar-title")
        with ScrollableContainer(id="sidebar-scroll"):
            current_section = ""
            for section, item_id, icon, label in self.NAV_ITEMS:
                if section != current_section:
                    current_section = section
                    yield SidebarSection(f"  {section}")
                item = NavItem(item_id, icon, label, id=f"nav-{item_id}")
                self._items[item_id] = item
                yield item

    def on_nav_item_selected(self, event: NavItem.Selected) -> None:
        self.active_item = event.item_id
        self.post_message(self.ItemSelected(event.item_id))

    def watch_active_item(self, value: str) -> None:
        for item_id, item in self._items.items():
            item.is_active = item_id == value

    def watch_visible(self, value: bool) -> None:
        self.set_class(not value, "hidden")

    def toggle(self) -> None:
        self.visible = not self.visible

    def select_item(self, item_id: str) -> None:
        self.active_item = item_id
        self.post_message(self.ItemSelected(item_id))
