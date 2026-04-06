"""
Core infrastructure engine.
Provides event bus, lifecycle management, config hot-reload, and error boundaries.

All other TUI modules depend on this layer.
No internal TUI dependencies — this is Level 0.
"""

from .events import EventBus, Event, event_bus
from .errors import SafeExecutor, ErrorBoundary, TUIError, ErrorSeverity
from .lifecycle import LifecycleManager
from .config_manager import ConfigManager

__all__ = [
    "EventBus",
    "Event",
    "event_bus",
    "SafeExecutor",
    "ErrorBoundary",
    "TUIError",
    "ErrorSeverity",
    "LifecycleManager",
    "ConfigManager",
]
