"""
Format selection table widget using Textual DataTable.
"""

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import DataTable, Static
from textual.containers import Vertical
from textual.message import Message
from typing import Any, Dict, List, Optional

from ..controllers.downloader import build_format_table_data


class FormatTable(Widget):
    """
    Interactive format selection table.
    Displays available formats and allows user to select one.
    """

    DEFAULT_CSS = """
    FormatTable {
        height: auto;
        max-height: 60%;
    }
    FormatTable DataTable {
        height: auto;
        max-height: 24;
    }
    FormatTable #format-header {
        text-style: bold;
        color: $primary;
        padding: 0 1;
        height: 2;
    }
    """

    class FormatSelected(Message):
        def __init__(self, format_obj: Any) -> None:
            self.format_obj = format_obj
            super().__init__()

    def __init__(self, formats: Optional[List] = None, duration: Optional[int] = None, **kwargs):
        super().__init__(**kwargs)
        self._formats = formats or []
        self._duration = duration
        self._rows_data: List[Dict] = []

    def compose(self) -> ComposeResult:
        yield Static(id="format-header")
        yield DataTable(id="format-dt")

    def on_mount(self) -> None:
        if self._formats:
            self.load_formats(self._formats, self._duration)

    def load_formats(self, formats: List, duration: Optional[int] = None) -> None:
        """Load formats into the table."""
        self._formats = formats
        self._rows_data = build_format_table_data(formats, duration)

        try:
            header = self.query_one("#format-header", Static)
            header.update(f"Available Formats ({len(formats)})")
        except Exception:
            pass

        try:
            dt = self.query_one("#format-dt", DataTable)
            dt.clear(columns=True)

            dt.add_column("No", key="no", width=4)
            dt.add_column("ID", key="id", width=18)
            dt.add_column("Resolution", key="resolution", width=12)
            dt.add_column("Quality", key="quality", width=12)
            dt.add_column("Type", key="type", width=8)
            dt.add_column("Size", key="size", width=10)
            dt.add_column("Note", key="note")

            for row in self._rows_data:
                dt.add_row(
                    row["no"], row["id"], row["resolution"],
                    row["quality"], row["type"], row["size"], row["note"],
                    key=row["no"],
                )
        except Exception:
            pass

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """User selected a format row."""
        self._emit_selection_from_row_key(event.row_key)

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        """Handle click-to-select on any cell in the row."""
        row_key = getattr(getattr(event, "cell_key", None), "row_key", None)
        if row_key is not None:
            self._emit_selection_from_row_key(row_key)

    def _emit_selection_from_row_key(self, row_key: Any) -> None:
        """Resolve row key and emit selected format message."""
        try:
            key_value = getattr(row_key, "value", row_key)
            key_text = str(key_value)
            idx = int(key_text) - 1
            if 0 <= idx < len(self._rows_data):
                fmt = self._rows_data[idx].get("_format")
                if fmt:
                    self.post_message(self.FormatSelected(fmt))
        except (ValueError, IndexError):
            pass
