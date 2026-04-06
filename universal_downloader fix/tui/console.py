# pyright: reportOptionalMemberAccess=false, reportOptionalCall=false
"""
Shared Rich console instance and availability checking for the TUI package.
All TUI modules should import console and Rich components from here.
"""

import sys
from typing import Any

# Pre-initialize to None for graceful fallback when Rich is not installed
Console = None
Table = None
Panel = None
Prompt = None
Confirm = None
Rule = None
box = None
rprint = None

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm
    from rich.rule import Rule
    from rich import box
    from rich import print as rprint
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


def check_rich():
    """Check if Rich library is available. Exit with instructions if not."""
    if not RICH_AVAILABLE:
        print("Interactive mode requires 'rich' library.")
        print("Install: pip install rich")
        print("Or use CLI mode: python main.py download \"URL\"")
        sys.exit(1)


console: Any = Console() if RICH_AVAILABLE else None