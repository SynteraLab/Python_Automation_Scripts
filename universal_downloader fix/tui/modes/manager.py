"""
Mode state machine.
Controls which UI layout and keybinding context is active.
"""

from enum import Enum, auto
from typing import Callable, Dict, List, Optional

from ..engine.events import event_bus
from ..keybindings import keybinding_manager


class AppMode(Enum):
    BUILD = "build_mode"
    RUN = "run_mode"
    MONITOR = "monitor_mode"
    FOCUS = "focus_mode"


MODE_DISPLAY = {
    AppMode.BUILD: ("🏗", "Build", "Node editor and workflow design"),
    AppMode.RUN: ("▶️", "Run", "Workflow execution and progress"),
    AppMode.MONITOR: ("📊", "Monitor", "Dashboard, logs, and metrics"),
    AppMode.FOCUS: ("🎯", "Focus", "Minimal distraction-free interface"),
}


class ModeManager:
    """
    Manages the active application mode.
    Switches keybinding contexts and emits events on change.
    """

    def __init__(self):
        self._current: AppMode = AppMode.BUILD
        self._history: List[AppMode] = []
        self._on_change_callbacks: List[Callable] = []

    @property
    def current(self) -> AppMode:
        return self._current

    @property
    def current_name(self) -> str:
        _, name, _ = MODE_DISPLAY.get(self._current, ("", "Unknown", ""))
        return name

    @property
    def current_icon(self) -> str:
        icon, _, _ = MODE_DISPLAY.get(self._current, ("", "", ""))
        return icon

    @property
    def current_description(self) -> str:
        _, _, desc = MODE_DISPLAY.get(self._current, ("", "", ""))
        return desc

    def switch(self, mode: AppMode) -> None:
        """Switch to a new mode."""
        if mode == self._current:
            return

        old = self._current
        self._history.append(old)
        if len(self._history) > 20:
            self._history = self._history[-20:]

        self._current = mode

        # Update keybinding context
        keybinding_manager.set_mode_context(mode.value)

        # Emit event
        event_bus.emit(
            "mode.changed",
            source="ModeManager",
            old_mode=old.value,
            mode=mode.value,
            mode_name=self.current_name,
        )

        # Notify callbacks
        for cb in self._on_change_callbacks:
            try:
                cb(mode)
            except Exception:
                pass

    def switch_by_name(self, name: str) -> bool:
        """Switch mode by string name. Returns True if successful."""
        name_map = {
            "build": AppMode.BUILD,
            "run": AppMode.RUN,
            "monitor": AppMode.MONITOR,
            "focus": AppMode.FOCUS,
        }
        mode = name_map.get(name.lower().replace("_mode", ""))
        if mode:
            self.switch(mode)
            return True
        return False

    def cycle(self) -> AppMode:
        """Cycle to next mode."""
        modes = list(AppMode)
        try:
            idx = modes.index(self._current)
            next_idx = (idx + 1) % len(modes)
        except ValueError:
            next_idx = 0
        self.switch(modes[next_idx])
        return modes[next_idx]

    def go_back(self) -> bool:
        """Switch to previous mode."""
        if self._history:
            prev = self._history.pop()
            old = self._current
            self._current = prev
            keybinding_manager.set_mode_context(prev.value)
            event_bus.emit(
                "mode.changed",
                source="ModeManager",
                old_mode=old.value,
                mode=prev.value,
                mode_name=self.current_name,
            )
            return True
        return False

    def on_change(self, callback: Callable) -> None:
        """Register a mode change callback."""
        self._on_change_callbacks.append(callback)

    def list_modes(self) -> List[dict]:
        """List all modes with metadata."""
        return [
            {
                "mode": mode.value,
                "icon": icon,
                "name": name,
                "description": desc,
                "active": mode == self._current,
            }
            for mode, (icon, name, desc) in MODE_DISPLAY.items()
        ]


# ── Singleton ──────────────────────────────────────────────
mode_manager = ModeManager()