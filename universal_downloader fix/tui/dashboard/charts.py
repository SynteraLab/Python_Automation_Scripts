# pyright: reportOptionalMemberAccess=false
"""
Custom Rich renderables for ASCII-based charts and visualizations.
All classes implement the Rich console protocol for native rendering.
"""

from typing import List, Tuple, Optional
from ..console import console, RICH_AVAILABLE

if RICH_AVAILABLE:
    from rich.text import Text
    from rich.table import Table
    from rich.panel import Panel
    from rich import box


# Unicode block characters for sparklines (ascending height)
SPARK_BLOCKS = " ▁▂▃▄▅▆▇█"
BAR_FULL = "█"
BAR_HALF = "▌"
BAR_EMPTY = "░"


class BarChart:
    """
    Horizontal ASCII bar chart renderable for Rich.

    Usage:
        chart = BarChart(title="Downloads by Extractor")
        chart.add("yt-dlp", 42, color="green")
        chart.add("erome", 15, color="cyan")
        console.print(chart)
    """

    def __init__(self, title: str = "", bar_width: int = 30, show_values: bool = True):
        self.title = title
        self.bar_width = bar_width
        self.show_values = show_values
        self._items: List[Tuple[str, float, str]] = []

    def add(self, label: str, value: float, color: str = "cyan") -> "BarChart":
        """Add a data point. Returns self for chaining."""
        self._items.append((label, value, color))
        return self

    def _build_bar(self, value: float, max_value: float, color: str) -> Text:
        """Build a single bar as Rich Text."""
        if max_value <= 0:
            return Text(BAR_EMPTY * self.bar_width, style="dim")

        ratio = value / max_value
        filled = int(ratio * self.bar_width)
        # Add a half block if the fractional part is significant
        remainder = (ratio * self.bar_width) - filled
        has_half = remainder >= 0.4 and filled < self.bar_width

        bar_text = Text()
        bar_text.append(BAR_FULL * filled, style=f"bold {color}")
        if has_half:
            bar_text.append(BAR_HALF, style=color)
            empty_count = self.bar_width - filled - 1
        else:
            empty_count = self.bar_width - filled
        if empty_count > 0:
            bar_text.append(BAR_EMPTY * empty_count, style="dim")

        return bar_text

    def __rich_console__(self, console, options):
        if not self._items:
            yield Text("(no data)", style="dim")
            return

        max_value = max(v for _, v, _ in self._items)
        max_label_len = max(len(label) for label, _, _ in self._items)

        table = Table(
            show_header=False,
            box=None,
            padding=(0, 1),
            expand=False,
            title=self.title if self.title else None,
            title_style="bold",
        )
        table.add_column("Label", width=max_label_len + 1, style="bold white", no_wrap=True)
        table.add_column("Bar", width=self.bar_width + 2, no_wrap=True)
        if self.show_values:
            table.add_column("Value", width=8, justify="right", style="dim")

        for label, value, color in self._items:
            bar = self._build_bar(value, max_value, color)
            row = [label, bar]
            if self.show_values:
                row.append(str(int(value)) if value == int(value) else f"{value:.1f}")
            table.add_row(*row)

        yield table


class Sparkline:
    """
    Inline sparkline renderable using Unicode block characters.

    Usage:
        spark = Sparkline([1, 4, 2, 8, 5, 3, 7], color="green")
        console.print(spark)
    """

    def __init__(self, values: List[float], color: str = "cyan",
                 label: Optional[str] = None, width: Optional[int] = None):
        self.values = values
        self.color = color
        self.label = label
        self.width = width

    def _normalize(self) -> List[int]:
        """Normalize values to sparkline block indices (0-8)."""
        if not self.values:
            return []

        vals = list(self.values)
        # Optionally resample to fit width
        if self.width and len(vals) > self.width:
            step = len(vals) / self.width
            resampled = []
            for i in range(self.width):
                start = int(i * step)
                end = int((i + 1) * step)
                chunk = vals[start:end]
                resampled.append(sum(chunk) / len(chunk) if chunk else 0)
            vals = resampled

        min_val = min(vals)
        max_val = max(vals)
        value_range = max_val - min_val

        if value_range == 0:
            return [4] * len(vals)  # mid-height for flat data

        return [int(((v - min_val) / value_range) * 8) for v in vals]

    def __rich_console__(self, console, options):
        indices = self._normalize()
        if not indices:
            yield Text("(no data)", style="dim")
            return

        spark_text = Text()
        if self.label:
            spark_text.append(f"{self.label} ", style="bold white")

        spark_chars = "".join(SPARK_BLOCKS[i] for i in indices)
        spark_text.append(spark_chars, style=self.color)

        # Append min/max
        if self.values:
            spark_text.append(
                f"  min={min(self.values):.0f} max={max(self.values):.0f}",
                style="dim",
            )

        yield spark_text


class RatioBar:
    """
    Success/fail ratio bar visualization.

    Usage:
        bar = RatioBar(success=42, failed=8, width=40)
        console.print(bar)
    """

    def __init__(self, success: int = 0, failed: int = 0, width: int = 40,
                 success_color: str = "green", fail_color: str = "red",
                 label: Optional[str] = None):
        self.success = success
        self.failed = failed
        self.width = width
        self.success_color = success_color
        self.fail_color = fail_color
        self.label = label

    def __rich_console__(self, console, options):
        total = self.success + self.failed
        if total == 0:
            yield Text("(no data)", style="dim")
            return

        success_ratio = self.success / total
        success_blocks = int(success_ratio * self.width)
        fail_blocks = self.width - success_blocks

        bar = Text()
        if self.label:
            bar.append(f"{self.label}  ", style="bold white")

        bar.append(BAR_FULL * success_blocks, style=f"bold {self.success_color}")
        bar.append(BAR_FULL * fail_blocks, style=f"bold {self.fail_color}")

        pct = success_ratio * 100
        bar.append(f"  {pct:.0f}% ", style="bold white")
        bar.append(f"({self.success}✓ {self.failed}✗)", style="dim")

        yield bar


class MiniTable:
    """
    Compact key-value display for dashboard panels.

    Usage:
        t = MiniTable(title="Overview")
        t.add("Total", "156")
        t.add("Success", "142", color="green")
        console.print(t)
    """

    def __init__(self, title: Optional[str] = None):
        self.title = title
        self._rows: List[Tuple[str, str, str]] = []

    def add(self, key: str, value: str, color: str = "white") -> "MiniTable":
        self._rows.append((key, value, color))
        return self

    def __rich_console__(self, console, options):
        table = Table(
            show_header=False,
            box=box.SIMPLE,
            padding=(0, 1),
            expand=True,
            title=self.title,
            title_style="bold",
        )
        table.add_column("Key", style="dim", no_wrap=True)
        table.add_column("Value", justify="right", no_wrap=True)

        for key, value, color in self._rows:
            table.add_row(key, f"[{color}]{value}[/{color}]")

        yield table