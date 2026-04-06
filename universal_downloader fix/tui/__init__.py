"""
TUI package for Universal Media Downloader.

Exposes run_tui() as the main entry point.
Compatible with main.py: `from tui import run_tui`

This is a Textual-based TUI with:
- Visual node editor
- Workflow automation
- Real-time dashboard
- Command palette with fuzzy search
- Multi-mode interface (Build/Run/Monitor/Focus)
- Plugin system
- Adaptive UI
"""


def run_tui():
    """
    Launch the interactive TUI.
    Synchronous entry point — blocks until user exits.

    Called by main.py:
        from tui import run_tui
        run_tui()
    """
    # Check dependencies
    try:
        import textual  # noqa: F401
    except ImportError:
        # Fallback: try Rich-only mode
        try:
            from rich.console import Console
            console = Console()
            console.print("[red]Textual library required for TUI mode.[/red]")
            console.print("Install: [cyan]pip install textual[/cyan]")
            console.print("Or use CLI: [cyan]python main.py download \"URL\"[/cyan]")
        except ImportError:
            print("TUI requires 'textual' library.")
            print("Install: pip install textual")
            print("Or use CLI: python main.py download \"URL\"")
        import sys
        sys.exit(1)

    from .app import UltraApp

    app = UltraApp()
    app.run()


__all__ = ["run_tui"]