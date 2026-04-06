# pyright: reportOptionalMemberAccess=false, reportOptionalCall=false
"""
Panel and menu rendering components for the TUI main menu.
Updated: Added menu option 11 (Dashboard) and command palette hint.
"""

from ..console import console, Panel, Table, box


def render_main_menu_table() -> None:
    """Render a cleaner and more aesthetic main menu layout."""
    header = Panel.fit(
        "[bold white]Choose an operation[/bold white]\n"
        "[dim]Fast workflow for download, extract, and manage tasks[/dim]",
        border_style="bright_cyan",
        box=box.ROUNDED,
        padding=(0, 2),
        title="[bold cyan]Main Menu[/bold cyan]",
        title_align="center",
    )
    console.print(header)

    table = Table(
        show_header=True,
        header_style="bold white on dark_blue",
        border_style="bright_blue",
        box=box.ROUNDED,
        expand=True,
        pad_edge=True,
        row_styles=["none", "grey23"],
    )
    table.add_column("No", width=4, justify="right", style="bold yellow")
    table.add_column("Feature", min_width=28, no_wrap=True, overflow="ellipsis", style="bold white")
    table.add_column("Description", min_width=38, style="grey70", overflow="fold")

    groups = [
        [
            ("1", "🎯", "Smart Download", "Auto-detect best method and download"),
            ("2", "🎵", "Audio Only", "Extract audio (MP3) from any video"),
            ("3", "📦", "Batch Download", "Download multiple URLs at once"),
            ("4", "📄", "Batch from File", "Import URLs from text file"),
            ("5", "ℹ️", "Video Info", "View metadata and available formats"),
        ],
        [
            ("6", "📊", "History and Stats", "Review download history and totals"),
            ("7", "🌐", "Supported Sites", "List all supported websites"),
            ("8", "🔧", "Extractors", "Show available extractors"),
            ("9", "📸", "EroMe Download", "Auto: photos parallel, videos use aria2"),
        ],
        [
            ("10", "⚙️", "Settings", "Configure download options"),
            ("11", "📈", "Dashboard", "Visual stats, charts, and activity"),
            ("0", "🚪", "Exit", "Quit application"),
        ],
    ]

    for group_idx, rows in enumerate(groups):
        for key, icon, name, desc in rows:
            feature = f"[bright_cyan]{icon}[/bright_cyan]  {name}"
            table.add_row(key, feature, desc)
        if group_idx < len(groups) - 1:
            table.add_section()

    console.print(table)

    console.print(
        Panel(
            "[dim]Tip:[/dim] [cyan]Ketik nomor menu[/cyan] [dim](0-11) |[/dim] "
            "[cyan]:command[/cyan] [dim]= palette |[/dim] "
            "[cyan]Enter[/cyan] [dim]= 1 |[/dim] [cyan]:help[/cyan] [dim]= commands[/dim]",
            border_style="grey35",
            box=box.SQUARE,
            padding=(0, 1),
        )
    )