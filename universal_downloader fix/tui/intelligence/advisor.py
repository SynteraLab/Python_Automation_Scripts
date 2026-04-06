"""
Rule-based recommendation engine.
Suggests actions based on usage patterns.
"""

from typing import List, Optional
from .tracker import usage_tracker
from ..engine.events import event_bus


class Suggestion:
    def __init__(self, text: str, action: str = "", priority: int = 0):
        self.text = text
        self.action = action
        self.priority = priority


class Advisor:
    """
    Provides suggestions based on usage patterns.
    Rule-based — designed for future LLM integration.
    """

    def get_suggestions(self, context: str = "") -> List[Suggestion]:
        suggestions = []

        top_cmds = usage_tracker.get_top_commands(3)
        top_nav = usage_tracker.get_top_navigation(3)

        # Suggest most-used feature
        if top_nav:
            most_used = top_nav[0][0]
            suggestions.append(
                Suggestion(
                    f"Quick access: Your most used feature is '{most_used}'",
                    action=most_used,
                    priority=10,
                )
            )

        # Suggest workflow if repeated downloads
        top_ext = usage_tracker.get_top_extractors(1)
        if top_ext and top_ext[0][1] >= 5:
            ext_name = top_ext[0][0]
            suggestions.append(
                Suggestion(
                    f"Create a workflow for {ext_name} downloads (used {top_ext[0][1]}x)",
                    action="workflows",
                    priority=8,
                )
            )

        # Suggest batch if multiple single downloads
        dl_count = sum(c for _, c in usage_tracker.get_top_commands(100) if "download" in _)
        if dl_count >= 3:
            suggestions.append(
                Suggestion(
                    "Try batch download for multiple URLs at once",
                    action="batch",
                    priority=5,
                )
            )

        # Suggest dashboard
        if not any(n == "dashboard" for n, _ in top_nav):
            suggestions.append(
                Suggestion(
                    "Check the dashboard for download statistics",
                    action="dashboard",
                    priority=3,
                )
            )

        suggestions.sort(key=lambda s: s.priority, reverse=True)
        return suggestions

    def get_node_suggestions(self, current_node_type: str) -> List[str]:
        """Suggest next node types based on current node."""
        suggestions_map = {
            "url_input": ["smart_download", "extract_info", "audio_download"],
            "url_list_input": ["smart_download", "filter"],
            "file_input": ["smart_download", "filter"],
            "smart_download": ["log_output", "conditional"],
            "audio_download": ["log_output"],
            "extract_info": ["conditional", "log_output"],
            "conditional": ["smart_download", "log_output", "delay"],
            "filter": ["smart_download", "counter"],
        }
        return suggestions_map.get(current_node_type, ["log_output"])


# ── Singleton ──────────────────────────────────────────────
advisor = Advisor()