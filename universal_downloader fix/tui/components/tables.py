# pyright: reportOptionalMemberAccess=false, reportOptionalCall=false
"""
Table rendering components for format selection and EroMe album display.
"""

from typing import Optional

from ..console import console, Table
from ..utils import resolve_duration_for_table, guess_bitrate_kbps, format_size_cell


def show_format_table(formats, duration_seconds: Optional[int] = None) -> None:
    """Render available formats table for interactive selection."""
    resolved_duration = resolve_duration_for_table(formats, duration_seconds)

    bitrate_overrides = {}
    for fmt in formats:
        guessed = guess_bitrate_kbps(fmt)
        if guessed and guessed > 0:
            bitrate_overrides[fmt.format_id] = guessed

    table = Table(title=f"Available Formats ({len(formats)})", show_header=True, border_style="blue")
    table.add_column("No", width=4, justify="right", style="bold yellow")
    table.add_column("ID", width=18, style="cyan")
    table.add_column("Resolution", width=12)
    table.add_column("Quality", width=12)
    table.add_column("Type", width=8)
    table.add_column("Size", width=10)
    table.add_column("Note", overflow="fold")

    for idx, fmt in enumerate(formats, 1):
        bitrate_guess = bitrate_overrides.get(fmt.format_id)
        size = format_size_cell(fmt, resolved_duration, bitrate_kbps=bitrate_guess)
        resolution = fmt.resolution
        if resolution == "unknown" and (not fmt.is_video and fmt.is_audio):
            resolution = "audio"

        table.add_row(
            str(idx),
            fmt.format_id,
            resolution,
            fmt.quality or "",
            fmt.stream_type.value,
            size,
            fmt.label or "",
        )

    console.print(table)


def print_erome_items_table(items) -> None:
    """Render EroMe album item table."""
    table = Table(title=f"EroMe Album Items ({len(items)})", show_header=True, border_style="blue")
    table.add_column("No", width=4, justify="right", style="bold yellow")
    table.add_column("Type", width=8)
    table.add_column("Title", overflow="fold")
    table.add_column("Quality", width=10)
    table.add_column("Ext", width=6)

    for display_idx, item in enumerate(items, 1):
        fmt = item.format
        media_type = item.media_type.upper()
        quality = fmt.quality or "-"
        ext = fmt.ext or "bin"
        type_color = "cyan" if item.media_type == 'video' else "magenta"
        table.add_row(str(display_idx), f"[{type_color}]{media_type}[/{type_color}]", item.title, quality, ext)

    console.print(table)