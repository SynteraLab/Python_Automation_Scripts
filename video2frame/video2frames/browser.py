"""
Interactive file / directory browser built on Rich.

Designed for maximum efficiency:
  • Paste or drag-drop any path and it is cleaned automatically
    (handles Finder quotes, backslash-escaped spaces, tilde, etc.)
  • Number-based selection for quick navigation
  • Partial name matching — type a few characters to jump
  • Video files highlighted in green for instant recognition
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Set

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich import box

from video2frames.utils import VIDEO_EXTENSIONS, format_size

console = Console()


# ═══════════════════════════════════════════════════════════════════════
#  Path helpers — usable everywhere in the project
# ═══════════════════════════════════════════════════════════════════════

def clean_path(raw: str) -> Path:
    """
    Sanitise a user-provided path string.

    Handles every common annoyance:
      • Surrounding single / double quotes  (Finder drag-and-drop)
      • Backslash-escaped spaces            (Terminal drag-and-drop)
      • Tilde expansion                     (~/ → /Users/…)
      • Leading / trailing whitespace
      • Resolves to absolute path
    """
    s = raw.strip()
    # Strip matching surrounding quotes
    for q in ("'", '"'):
        if len(s) >= 2 and s[0] == q and s[-1] == q:
            s = s[1:-1]
            break
    s = s.replace("\\ ", " ")
    s = os.path.expanduser(s)
    return Path(s).resolve()


def is_path_like(text: str) -> bool:
    """Heuristic: does *text* look like a filesystem path?"""
    t = text.strip().strip("'\"")
    if not t or len(t) < 2:
        return False
    return (
        t.startswith("/")
        or t.startswith("~")
        or t.startswith("./")
        or t.startswith("../")
        or (os.sep in t and len(t) > 3)
        or any(t.lower().endswith(ext) for ext in VIDEO_EXTENSIONS)
    )


def is_video_file(p: Path) -> bool:
    """True when *p* exists, is a regular file, and has a video extension."""
    return p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS


# ═══════════════════════════════════════════════════════════════════════
#  Interactive file browser
# ═══════════════════════════════════════════════════════════════════════

class FileBrowser:
    """
    Rich-based interactive filesystem navigator.

    Usage::

        browser = FileBrowser()
        chosen  = browser.browse()          # returns Path | None
        folder  = browser.browse(select_folder=True)
    """

    # ── public API ────────────────────────────────────────────────────
    def browse(
        self,
        start: Optional[Path] = None,
        select_folder: bool = False,
        title: str = "File Browser",
    ) -> Optional[Path]:
        """
        Open the browser and return the selected Path (or None).

        Parameters
        ----------
        start : Path, optional
            Initial directory.  Defaults to the current working directory.
        select_folder : bool
            If *True* the user can type **here** to select the current
            directory instead of an individual file.
        title : str
            Panel title shown at the top.
        """
        current = (Path(start) if start else Path.cwd()).resolve()

        while True:
            entries = self._list_entries(current)

            # ── header ────────────────────────────────────────────────
            console.print()
            console.print(Panel(
                f"[bold]{current}[/]",
                title=f"📂 {title}",
                subtitle=(
                    "[dim]  # = select  ·  .. = parent  ·  "
                    "/path = jump  ·  q = cancel  "
                    + ("·  here = pick folder  " if select_folder else "")
                    + "[/]"
                ),
                border_style="cyan",
                expand=True,
            ))

            # ── directory listing ─────────────────────────────────────
            tbl = Table(
                box=box.SIMPLE_HEAVY,
                show_edge=False,
                row_styles=["", "dim"],
                padding=(0, 1),
            )
            tbl.add_column("#", style="bold cyan", width=5, justify="right")
            tbl.add_column("Name", min_width=35, no_wrap=True)
            tbl.add_column("Size", justify="right", width=10)

            if not entries:
                console.print("  [dim italic](empty directory)[/]")
            for idx, (name, _path, is_dir, is_vid, sz) in enumerate(entries):
                num = str(idx)
                if is_dir:
                    label = f"[bold blue]📁  {name}/[/]"
                elif is_vid:
                    label = f"[bold green]🎬  {name}[/]"
                else:
                    label = f"[dim]    {name}[/]"
                tbl.add_row(num, label, "" if is_dir else sz)
            console.print(tbl)

            # ── prompt ────────────────────────────────────────────────
            try:
                choice = Prompt.ask("\n[bold cyan]▶ Navigate[/]").strip()
            except (KeyboardInterrupt, EOFError):
                return None

            if not choice or choice.lower() in ("q", "quit", "exit"):
                return None

            # parent
            if choice == "..":
                current = current.parent
                continue

            # select current folder
            if select_folder and choice.lower() == "here":
                return current

            # direct / absolute path
            if choice.startswith("/") or choice.startswith("~"):
                target = clean_path(choice)
                if target.is_dir():
                    current = target
                elif target.is_file():
                    return target
                else:
                    console.print(f"[red]Not found: {target}[/]")
                continue

            # numeric index
            try:
                idx = int(choice)
                if 0 <= idx < len(entries):
                    _, path, is_dir, _, _ = entries[idx]
                    if is_dir:
                        current = path
                    else:
                        return path
                else:
                    console.print("[red]Number out of range[/]")
                continue
            except ValueError:
                pass

            # relative name / partial match
            target = current / choice
            if target.is_dir():
                current = target
                continue
            if target.is_file():
                return target

            matches = [
                e for e in entries if choice.lower() in e[0].lower()
            ]
            if len(matches) == 1:
                _, path, is_dir, _, _ = matches[0]
                if is_dir:
                    current = path
                else:
                    return path
            elif len(matches) > 1:
                console.print(
                    "[yellow]Multiple matches: "
                    + ", ".join(m[0] for m in matches[:8])
                    + "[/]"
                )
            else:
                console.print(f"[red]Not found: {choice}[/]")

    # ── internal helpers ──────────────────────────────────────────────
    def _list_entries(self, directory: Path) -> List[tuple]:
        """
        Sorted directory contents — folders first, then files.

        Skips hidden entries (dotfiles).  Returns a list of
        ``(name, path, is_dir, is_video, size_str)`` tuples.
        """
        result: list = []
        try:
            items = sorted(
                directory.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except PermissionError:
            console.print("[red]⚠  Permission denied[/]")
            return result

        for item in items:
            if item.name.startswith("."):
                continue
            is_dir = item.is_dir()
            is_vid = (not is_dir) and item.suffix.lower() in VIDEO_EXTENSIONS
            sz = ""
            if not is_dir:
                try:
                    sz = format_size(item.stat().st_size)
                except OSError:
                    sz = "?"
            result.append((item.name, item, is_dir, is_vid, sz))

        return result