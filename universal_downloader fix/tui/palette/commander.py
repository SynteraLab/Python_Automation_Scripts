"""
Command palette with fuzzy matching.
Integrates with Textual as a modal overlay.
"""

import logging
from typing import Callable, Dict, List, Optional, Tuple

from ..engine.events import event_bus
from ..intelligence.tracker import usage_tracker

logger = logging.getLogger(__name__)


def _fuzzy_score(query: str, text: str) -> int:
    """Simple fuzzy matching score. Higher = better match."""
    query = query.lower()
    text = text.lower()

    if query == text:
        return 1000
    if text.startswith(query):
        return 900 + (100 - len(text))
    if query in text:
        return 500 + (100 - text.index(query))

    # Character-by-character match
    score = 0
    qi = 0
    for ci, ch in enumerate(text):
        if qi < len(query) and ch == query[qi]:
            score += 10
            if ci == 0 or text[ci - 1] in " _-":
                score += 5  # Bonus for word boundary
            qi += 1

    if qi == len(query):
        return score
    return 0


class PaletteCommand:
    """Single command in the palette."""

    def __init__(
        self,
        name: str,
        handler: Callable,
        description: str = "",
        category: str = "General",
        aliases: Optional[List[str]] = None,
        icon: str = "",
    ):
        self.name = name
        self.handler = handler
        self.description = description
        self.category = category
        self.aliases = aliases or []
        self.icon = icon
        self.usage_count = 0

    @property
    def display_text(self) -> str:
        icon = f"{self.icon} " if self.icon else ""
        return f"{icon}{self.name}"

    @property
    def search_text(self) -> str:
        parts = [self.name, self.description] + self.aliases
        return " ".join(parts).lower()


class CommandPalette:
    """
    Fuzzy-searchable command palette.
    Registers commands and provides search/execute.
    """

    def __init__(self):
        self._commands: Dict[str, PaletteCommand] = {}

    def register(
        self,
        name: str,
        handler: Callable,
        description: str = "",
        category: str = "General",
        aliases: Optional[List[str]] = None,
        icon: str = "",
    ) -> None:
        """Register a palette command."""
        cmd = PaletteCommand(
            name=name,
            handler=handler,
            description=description,
            category=category,
            aliases=aliases,
            icon=icon,
        )
        self._commands[name] = cmd
        for alias in (aliases or []):
            self._commands[alias] = cmd

    def search(self, query: str, limit: int = 15) -> List[PaletteCommand]:
        """Search commands with fuzzy matching. Returns sorted results."""
        if not query.strip():
            # Return all, sorted by usage
            cmds = list({id(c): c for c in self._commands.values()}.values())
            cmds.sort(key=lambda c: c.usage_count, reverse=True)
            return cmds[:limit]

        scored: List[Tuple[int, PaletteCommand]] = []
        seen = set()

        for cmd in self._commands.values():
            if id(cmd) in seen:
                continue
            seen.add(id(cmd))

            score = _fuzzy_score(query, cmd.search_text)
            if score > 0:
                # Boost by usage
                score += cmd.usage_count * 2
                scored.append((score, cmd))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [cmd for _, cmd in scored[:limit]]

    def execute(self, name: str, **kwargs) -> bool:
        """Execute a command by name."""
        cmd = self._commands.get(name)
        if not cmd:
            return False

        cmd.usage_count += 1
        usage_tracker.track_command(name)

        try:
            cmd.handler(**kwargs)
        except TypeError:
            try:
                cmd.handler()
            except Exception as e:
                logger.error(f"Command '{name}' failed: {e}")
                return False
        except Exception as e:
            logger.error(f"Command '{name}' failed: {e}")
            return False

        return True

    def get_all(self) -> List[PaletteCommand]:
        """Get all unique commands."""
        seen = set()
        result = []
        for cmd in self._commands.values():
            if id(cmd) not in seen:
                seen.add(id(cmd))
                result.append(cmd)
        return sorted(result, key=lambda c: (c.category, c.name))


# ── Singleton ──────────────────────────────────────────────
command_palette = CommandPalette()