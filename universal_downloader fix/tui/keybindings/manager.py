"""
Keybinding manager — context-aware keyboard shortcut system.

Features:
- Global and context-specific bindings
- Priority system (context > mode > global)
- Dynamic registration/unregistration
- Conflict detection
- Help text generation
- Integration with Textual key events

Contexts:
- "global"      → always active
- "build_mode"  → node editor keybindings
- "run_mode"    → execution keybindings
- "monitor_mode"→ dashboard keybindings
- "focus_mode"  → minimal keybindings
- "palette"     → command palette active
- "modal"       → modal dialog active

Usage:
    from tui.keybindings import keybinding_manager

    keybinding_manager.register(
        key="ctrl+d",
        action="quick_download",
        description="Quick download URL",
        context="global",
    )

    # Check what action a key triggers
    action = keybinding_manager.resolve("ctrl+d", context="build_mode")
"""

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

from ..engine.events import event_bus

logger = logging.getLogger(__name__)


@dataclass
class Keybinding:
    """Single keybinding definition."""
    key: str                          # Textual key string: "ctrl+d", "f1", "escape"
    action: str                       # Action identifier: "quick_download"
    description: str = ""             # Human-readable description
    context: str = "global"           # Context where this binding is active
    priority: int = 0                 # Higher = takes precedence
    enabled: bool = True              # Can be toggled
    category: str = "General"         # For help grouping
    handler: Optional[Callable] = None  # Direct handler (optional)

    @property
    def display_key(self) -> str:
        """Human-readable key representation."""
        replacements = {
            "ctrl+": "Ctrl+",
            "alt+": "Alt+",
            "shift+": "Shift+",
            "escape": "Esc",
            "enter": "Enter",
            "space": "Space",
            "backspace": "Bksp",
            "delete": "Del",
            "tab": "Tab",
        }
        result = self.key
        for old, new in replacements.items():
            result = result.replace(old, new)
        return result


# ── Default Keybindings ────────────────────────────────────

DEFAULT_BINDINGS: List[dict] = [
    # Global
    {"key": "ctrl+p", "action": "command_palette", "description": "Open command palette",
     "context": "global", "category": "Navigation", "priority": 100},
    {"key": "ctrl+b", "action": "toggle_sidebar", "description": "Toggle sidebar",
     "context": "global", "category": "Navigation", "priority": 90},
    {"key": "ctrl+d", "action": "quick_download", "description": "Quick download URL",
     "context": "global", "category": "Download", "priority": 90},
    {"key": "ctrl+l", "action": "toggle_log", "description": "Toggle log panel",
     "context": "global", "category": "Navigation", "priority": 80},
    {"key": "ctrl+t", "action": "cycle_theme", "description": "Cycle theme",
     "context": "global", "category": "View", "priority": 70},
    {"key": "ctrl+c", "action": "quit", "description": "Quit application",
     "context": "global", "category": "System", "priority": 100},
    {"key": "ctrl+q", "action": "quit", "description": "Quit application",
     "context": "global", "category": "System", "priority": 100},
    {"key": "question_mark", "action": "show_help", "description": "Show keybindings help",
     "context": "global", "category": "Help", "priority": 60},

    # Mode switching
    {"key": "f1", "action": "mode_build", "description": "Switch to Build mode",
     "context": "global", "category": "Mode", "priority": 95},
    {"key": "f2", "action": "mode_run", "description": "Switch to Run mode",
     "context": "global", "category": "Mode", "priority": 95},
    {"key": "f3", "action": "mode_monitor", "description": "Switch to Monitor mode",
     "context": "global", "category": "Mode", "priority": 95},
    {"key": "f4", "action": "mode_focus", "description": "Switch to Focus mode",
     "context": "global", "category": "Mode", "priority": 95},

    # Build mode
    {"key": "n", "action": "new_node", "description": "Create new node",
     "context": "build_mode", "category": "Node Editor", "priority": 50},
    {"key": "delete", "action": "delete_node", "description": "Delete selected node",
     "context": "build_mode", "category": "Node Editor", "priority": 50},
    {"key": "c", "action": "connect_nodes", "description": "Connect nodes",
     "context": "build_mode", "category": "Node Editor", "priority": 50},
    {"key": "x", "action": "disconnect_nodes", "description": "Disconnect nodes",
     "context": "build_mode", "category": "Node Editor", "priority": 50},
    {"key": "s", "action": "save_workflow", "description": "Save workflow",
     "context": "build_mode", "category": "Workflow", "priority": 50},
    {"key": "l", "action": "load_workflow", "description": "Load workflow",
     "context": "build_mode", "category": "Workflow", "priority": 50},
    {"key": "r", "action": "run_workflow", "description": "Run current workflow",
     "context": "build_mode", "category": "Workflow", "priority": 50},
    {"key": "tab", "action": "next_node", "description": "Focus next node",
     "context": "build_mode", "category": "Node Editor", "priority": 40},
    {"key": "shift+tab", "action": "prev_node", "description": "Focus previous node",
     "context": "build_mode", "category": "Node Editor", "priority": 40},

    # Run mode
    {"key": "escape", "action": "cancel_workflow", "description": "Cancel running workflow",
     "context": "run_mode", "category": "Execution", "priority": 50},
    {"key": "p", "action": "pause_workflow", "description": "Pause/resume workflow",
     "context": "run_mode", "category": "Execution", "priority": 50},

    # Monitor mode
    {"key": "r", "action": "refresh_dashboard", "description": "Refresh dashboard",
     "context": "monitor_mode", "category": "Dashboard", "priority": 50},
    {"key": "c", "action": "clear_logs", "description": "Clear log panel",
     "context": "monitor_mode", "category": "Dashboard", "priority": 50},
]


class KeybindingManager:
    """
    Context-aware keybinding registry.

    Resolution order:
    1. Modal context (if active)
    2. Current mode context
    3. Global context
    """

    def __init__(self):
        self._bindings: Dict[str, List[Keybinding]] = {}  # context → bindings
        self._active_contexts: List[str] = ["global"]
        self._conflict_warnings: List[str] = []

        # Load defaults
        self._load_defaults()

    def _load_defaults(self) -> None:
        """Load default keybindings."""
        for binding_dict in DEFAULT_BINDINGS:
            self.register(**binding_dict)

    # ── Registration ───────────────────────────────────────

    def register(
        self,
        key: str,
        action: str,
        description: str = "",
        context: str = "global",
        priority: int = 0,
        category: str = "General",
        handler: Optional[Callable] = None,
        **kwargs,
    ) -> Keybinding:
        """Register a keybinding."""
        binding = Keybinding(
            key=key,
            action=action,
            description=description,
            context=context,
            priority=priority,
            category=category,
            handler=handler,
        )

        if context not in self._bindings:
            self._bindings[context] = []

        # Check for conflicts within same context
        for existing in self._bindings[context]:
            if existing.key == key and existing.enabled:
                self._conflict_warnings.append(
                    f"Key '{key}' conflict in context '{context}': "
                    f"'{existing.action}' vs '{action}'"
                )
                logger.warning(self._conflict_warnings[-1])

        self._bindings[context].append(binding)
        return binding

    def unregister(self, key: str, context: str = "global") -> bool:
        """Remove a keybinding."""
        if context not in self._bindings:
            return False

        before = len(self._bindings[context])
        self._bindings[context] = [
            b for b in self._bindings[context]
            if not (b.key == key)
        ]
        return len(self._bindings[context]) < before

    # ── Context Management ─────────────────────────────────

    def push_context(self, context: str) -> None:
        """Add a context to the active stack."""
        if context not in self._active_contexts:
            self._active_contexts.append(context)
        event_bus.emit("keybinding.context.pushed", source="KeybindingManager", context=context)

    def pop_context(self, context: str = "") -> None:
        """Remove a context from the active stack."""
        target = context or (self._active_contexts[-1] if len(self._active_contexts) > 1 else "")
        if target and target != "global" and target in self._active_contexts:
            self._active_contexts.remove(target)
            event_bus.emit("keybinding.context.popped", source="KeybindingManager", context=target)

    def set_mode_context(self, mode: str) -> None:
        """Replace mode context (keeps global, removes old mode)."""
        # Remove all non-global contexts except persistent ones
        persistent = {"global", "modal", "palette"}
        self._active_contexts = [
            c for c in self._active_contexts
            if c in persistent
        ]
        self._active_contexts.append(mode)

    @property
    def active_contexts(self) -> List[str]:
        return list(self._active_contexts)

    # ── Resolution ─────────────────────────────────────────

    def resolve(self, key: str, context: Optional[str] = None) -> Optional[Keybinding]:
        """
        Resolve a key press to a binding.
        Searches contexts from most specific to global.
        """
        search_contexts = (
            [context] if context
            else list(reversed(self._active_contexts))
        )

        best: Optional[Keybinding] = None

        for ctx in search_contexts:
            for binding in self._bindings.get(ctx, []):
                if binding.key == key and binding.enabled:
                    if best is None or binding.priority > best.priority:
                        best = binding

        return best

    def get_action(self, key: str) -> Optional[str]:
        """Get action name for a key press. Returns None if unbound."""
        binding = self.resolve(key)
        return binding.action if binding else None

    # ── Query ──────────────────────────────────────────────

    def get_bindings(
        self,
        context: Optional[str] = None,
        category: Optional[str] = None,
    ) -> List[Keybinding]:
        """Get all bindings, optionally filtered."""
        if context:
            bindings = list(self._bindings.get(context, []))
        else:
            bindings = []
            for ctx_bindings in self._bindings.values():
                bindings.extend(ctx_bindings)

        if category:
            bindings = [b for b in bindings if b.category == category]

        return sorted(bindings, key=lambda b: (b.category, b.key))

    def get_active_bindings(self) -> List[Keybinding]:
        """Get all bindings active in current context stack."""
        result = []
        seen_keys: Set[str] = set()

        for ctx in reversed(self._active_contexts):
            for binding in self._bindings.get(ctx, []):
                if binding.enabled and binding.key not in seen_keys:
                    result.append(binding)
                    seen_keys.add(binding.key)

        return sorted(result, key=lambda b: (b.category, b.key))

    def get_categories(self) -> List[str]:
        """Get all unique categories."""
        cats = set()
        for bindings in self._bindings.values():
            for b in bindings:
                cats.add(b.category)
        return sorted(cats)

    def generate_help_text(self) -> str:
        """Generate formatted help text for all active bindings."""
        bindings = self.get_active_bindings()
        if not bindings:
            return "No keybindings active"

        lines = ["[bold]Keybindings[/bold]\n"]
        current_category = ""

        for b in bindings:
            if b.category != current_category:
                current_category = b.category
                lines.append(f"\n[bold cyan]{current_category}[/bold cyan]")

            lines.append(
                f"  [bold]{b.display_key:<16}[/bold] {b.description}"
            )

        return "\n".join(lines)

    def get_textual_bindings(self) -> List[Tuple[str, str, str]]:
        """
        Convert active bindings to Textual's Binding format.
        Returns list of (key, action, description) tuples.
        """
        result = []
        for binding in self.get_active_bindings():
            result.append((binding.key, binding.action, binding.description))
        return result


# ── Singleton ──────────────────────────────────────────────
keybinding_manager = KeybindingManager()
