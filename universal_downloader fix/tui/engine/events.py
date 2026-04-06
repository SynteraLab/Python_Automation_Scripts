"""
Event bus system — pub/sub message passing for the entire TUI.

Design:
- Singleton event_bus for global messaging
- Type-safe Event dataclass
- Sync and async handler support
- Wildcard subscriptions (e.g. "download.*")
- Thread-safe for use with Textual workers

Usage:
    from tui.engine import event_bus, Event

    # Subscribe
    event_bus.on("download.completed", my_handler)

    # Publish
    event_bus.emit("download.completed", url="...", filepath="...")

    # Wildcard
    event_bus.on("download.*", log_all_download_events)
"""

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Union,
)

logger = logging.getLogger(__name__)


@dataclass
class Event:
    """Immutable event payload."""

    name: str
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    source: Optional[str] = None

    def get(self, key: str, default: Any = None) -> Any:
        """Get data value by key."""
        return self.data.get(key, default)

    def __getattr__(self, key: str) -> Any:
        """Allow attribute-style access to data dict."""
        if key.startswith("_") or key in ("name", "data", "timestamp", "source"):
            raise AttributeError(key)
        try:
            return self.data[key]
        except KeyError:
            raise AttributeError(f"Event '{self.name}' has no data key '{key}'")


# Handler type: can be sync or async callable
Handler = Callable[..., Any]


class EventBus:
    """
    Thread-safe publish/subscribe event bus.

    Features:
    - Exact match subscriptions: "download.completed"
    - Wildcard subscriptions: "download.*"
    - Global catch-all: "*"
    - Async handler support
    - Event history for replay/debugging
    - Handler priority ordering
    """

    def __init__(self, history_limit: int = 500):
        self._handlers: Dict[str, List[tuple]] = {}
        self._lock = threading.RLock()
        self._history: List[Event] = []
        self._history_limit = history_limit
        self._muted: Set[str] = set()
        self._paused = False

    def on(
        self,
        event_name: str,
        handler: Handler,
        priority: int = 0,
        once: bool = False,
    ) -> Callable:
        """
        Subscribe to an event.

        Args:
            event_name: Event name or pattern (supports "*" wildcard)
            handler: Callable(event: Event) — sync or async
            priority: Higher = called first (default 0)
            once: If True, auto-unsubscribe after first call

        Returns:
            The handler (for use as decorator)
        """
        with self._lock:
            if event_name not in self._handlers:
                self._handlers[event_name] = []
            self._handlers[event_name].append((priority, handler, once))
            # Sort by priority descending
            self._handlers[event_name].sort(key=lambda x: x[0], reverse=True)
        return handler

    def once(self, event_name: str, handler: Handler, priority: int = 0) -> Callable:
        """Subscribe to an event, auto-unsubscribe after first call."""
        return self.on(event_name, handler, priority=priority, once=True)

    def off(self, event_name: str, handler: Optional[Handler] = None) -> None:
        """
        Unsubscribe from an event.
        If handler is None, remove ALL handlers for that event.
        """
        with self._lock:
            if handler is None:
                self._handlers.pop(event_name, None)
            elif event_name in self._handlers:
                self._handlers[event_name] = [
                    (p, h, o)
                    for p, h, o in self._handlers[event_name]
                    if h is not handler
                ]

    def emit(self, event_name: str, source: Optional[str] = None, **data: Any) -> Event:
        """
        Publish an event synchronously.

        Args:
            event_name: Event name (e.g. "download.completed")
            source: Optional source identifier
            **data: Event payload as keyword arguments

        Returns:
            The Event object that was emitted
        """
        event = Event(name=event_name, data=data, source=source)
        self._dispatch(event)
        return event

    def emit_event(self, event: Event) -> None:
        """Publish a pre-built Event object."""
        self._dispatch(event)

    def _dispatch(self, event: Event) -> None:
        """Internal: route event to matching handlers."""
        if self._paused:
            return

        if event.name in self._muted:
            return

        # Record history
        with self._lock:
            self._history.append(event)
            if len(self._history) > self._history_limit:
                self._history = self._history[-self._history_limit:]

        # Collect matching handlers
        handlers_to_call = []
        remove_after: List[tuple] = []

        with self._lock:
            # Exact match
            for entry in self._handlers.get(event.name, []):
                handlers_to_call.append(entry)
                if entry[2]:  # once=True
                    remove_after.append((event.name, entry))

            # Wildcard match: "download.*" matches "download.completed"
            event_parts = event.name.split(".")
            for pattern, entries in self._handlers.items():
                if pattern == event.name:
                    continue  # already handled
                if self._matches_pattern(pattern, event_parts):
                    for entry in entries:
                        handlers_to_call.append(entry)
                        if entry[2]:
                            remove_after.append((pattern, entry))

            # Global catch-all
            if "*" in self._handlers and event.name != "*":
                for entry in self._handlers["*"]:
                    handlers_to_call.append(entry)

        # Sort all collected handlers by priority
        handlers_to_call.sort(key=lambda x: x[0], reverse=True)

        # Execute
        for priority, handler, once in handlers_to_call:
            try:
                result = handler(event)
                # If handler returns a coroutine, try to schedule it
                if asyncio.iscoroutine(result):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(result)
                    except RuntimeError:
                        # No running loop — run synchronously
                        asyncio.run(result)
            except Exception as e:
                logger.error(
                    f"Event handler error: {event.name} → {handler.__name__}: {e}",
                    exc_info=True,
                )

        # Remove once-handlers
        if remove_after:
            with self._lock:
                for event_name, entry in remove_after:
                    if event_name in self._handlers:
                        try:
                            self._handlers[event_name].remove(entry)
                        except ValueError:
                            pass

    @staticmethod
    def _matches_pattern(pattern: str, event_parts: List[str]) -> bool:
        """Check if a wildcard pattern matches event name parts."""
        pattern_parts = pattern.split(".")
        if len(pattern_parts) != len(event_parts):
            # Allow trailing wildcard: "download.*" matches "download.anything"
            if (
                len(pattern_parts) == 2
                and pattern_parts[-1] == "*"
                and len(event_parts) >= 2
                and pattern_parts[0] == event_parts[0]
            ):
                return True
            return False

        for p, e in zip(pattern_parts, event_parts):
            if p == "*":
                continue
            if p != e:
                return False
        return True

    def mute(self, event_name: str) -> None:
        """Suppress an event from being dispatched."""
        self._muted.add(event_name)

    def unmute(self, event_name: str) -> None:
        """Re-enable a muted event."""
        self._muted.discard(event_name)

    def pause(self) -> None:
        """Pause all event dispatching."""
        self._paused = True

    def resume(self) -> None:
        """Resume event dispatching."""
        self._paused = False

    @property
    def history(self) -> List[Event]:
        """Return copy of event history."""
        with self._lock:
            return list(self._history)

    def clear_history(self) -> None:
        """Clear event history."""
        with self._lock:
            self._history.clear()

    def get_handlers(self, event_name: str) -> List[Handler]:
        """Get all handlers registered for an event name."""
        with self._lock:
            return [h for _, h, _ in self._handlers.get(event_name, [])]

    def reset(self) -> None:
        """Remove all handlers and history. Used for testing."""
        with self._lock:
            self._handlers.clear()
            self._history.clear()
            self._muted.clear()
            self._paused = False


# ── Singleton ──────────────────────────────────────────────
event_bus = EventBus()