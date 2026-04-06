"""Adaptive UI logic — auto-adjusts layout based on usage patterns."""

from typing import Dict, List, Optional
from .tracker import usage_tracker


class AdaptiveUI:
    """
    Adjusts UI elements based on user behavior.
    Currently controls sidebar ordering and panel visibility.
    """

    def get_sidebar_order(self) -> List[str]:
        """Return sidebar items ordered by usage frequency."""
        suggested = usage_tracker.get_suggested_order()
        all_items = [
            "download", "audio", "batch", "batch_file", "erome",
            "info", "extractors", "sites",
            "history", "dashboard",
            "settings", "workflows",
        ]
        ordered = [item for item in suggested if item in all_items]
        remaining = [item for item in all_items if item not in ordered]
        return ordered + remaining

    def should_show_suggestions(self) -> bool:
        """Show suggestions panel if user has enough usage history."""
        top = usage_tracker.get_top_commands(1)
        return bool(top and top[0][1] >= 3)

    def get_default_mode(self) -> str:
        """Suggest default mode based on usage patterns."""
        nav = usage_tracker.get_top_navigation(5)
        nav_items = [n for n, _ in nav]

        if "workflows" in nav_items or any("mode:build" in n for n in nav_items):
            return "build"
        if any("mode:monitor" in n for n in nav_items):
            return "monitor"
        return "build"


adaptive_ui = AdaptiveUI()