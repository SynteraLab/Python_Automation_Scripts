"""
Application lifecycle manager.

Handles:
- Ordered startup sequence
- Graceful shutdown with resource cleanup
- Service registration
- Health checks

Usage:
    lifecycle = LifecycleManager()
    lifecycle.register_startup("plugins", load_plugins, priority=10)
    lifecycle.register_shutdown("sessions", close_sessions, priority=100)

    await lifecycle.startup()
    ...
    await lifecycle.shutdown()
"""

import asyncio
import inspect
import logging
import signal
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Union

from .events import event_bus
from .errors import ErrorBoundary, ErrorSeverity

logger = logging.getLogger(__name__)


class AppState(Enum):
    """Application lifecycle states."""
    CREATED = auto()
    STARTING = auto()
    RUNNING = auto()
    STOPPING = auto()
    STOPPED = auto()
    ERROR = auto()


@dataclass
class LifecycleHook:
    """A registered startup or shutdown hook."""
    name: str
    callback: Callable
    priority: int = 0       # Higher = runs first
    is_async: bool = False
    timeout: float = 30.0   # Seconds before timeout
    required: bool = True   # If True, failure prevents startup


class LifecycleManager:
    """
    Manages application lifecycle: startup, running, shutdown.

    Features:
    - Priority-ordered startup/shutdown hooks
    - Async and sync hook support
    - Timeout enforcement
    - Resource registry for cleanup
    - State tracking
    - Event emission at each phase
    """

    def __init__(self):
        self._state: AppState = AppState.CREATED
        self._startup_hooks: List[LifecycleHook] = []
        self._shutdown_hooks: List[LifecycleHook] = []
        self._resources: Dict[str, Any] = {}
        self._start_time: Optional[float] = None
        self._services: Dict[str, Any] = {}

    @property
    def state(self) -> AppState:
        return self._state

    @property
    def uptime(self) -> float:
        """Seconds since startup."""
        if self._start_time is None:
            return 0.0
        return time.time() - self._start_time

    @property
    def is_running(self) -> bool:
        return self._state == AppState.RUNNING

    # ── Service Registry ───────────────────────────────────

    def register_service(self, name: str, service: Any) -> None:
        """Register a service for dependency injection."""
        self._services[name] = service
        logger.debug(f"Service registered: {name}")

    def get_service(self, name: str) -> Optional[Any]:
        """Get a registered service by name."""
        return self._services.get(name)

    def require_service(self, name: str) -> Any:
        """Get a registered service or raise."""
        svc = self._services.get(name)
        if svc is None:
            raise RuntimeError(f"Required service not found: {name}")
        return svc

    # ── Resource Registry ──────────────────────────────────

    def register_resource(self, name: str, resource: Any, cleanup: Optional[Callable] = None) -> None:
        """Register a resource that needs cleanup on shutdown."""
        self._resources[name] = {"resource": resource, "cleanup": cleanup}
        if cleanup:
            self.register_shutdown(
                f"cleanup_{name}",
                cleanup,
                priority=50,
                required=False,
            )

    def get_resource(self, name: str) -> Optional[Any]:
        """Get a registered resource."""
        entry = self._resources.get(name)
        return entry["resource"] if entry else None

    # ── Hook Registration ──────────────────────────────────

    def register_startup(
        self,
        name: str,
        callback: Callable,
        priority: int = 0,
        is_async: bool = False,
        timeout: float = 30.0,
        required: bool = True,
    ) -> None:
        """Register a startup hook."""
        hook = LifecycleHook(
            name=name,
            callback=callback,
            priority=priority,
            is_async=is_async or inspect.iscoroutinefunction(callback),
            timeout=timeout,
            required=required,
        )
        self._startup_hooks.append(hook)
        logger.debug(f"Startup hook registered: {name} (priority={priority})")

    def register_shutdown(
        self,
        name: str,
        callback: Callable,
        priority: int = 0,
        is_async: bool = False,
        timeout: float = 10.0,
        required: bool = False,
    ) -> None:
        """Register a shutdown hook."""
        hook = LifecycleHook(
            name=name,
            callback=callback,
            priority=priority,
            is_async=is_async or inspect.iscoroutinefunction(callback),
            timeout=timeout,
            required=required,
        )
        self._shutdown_hooks.append(hook)
        logger.debug(f"Shutdown hook registered: {name} (priority={priority})")

    # ── Startup ────────────────────────────────────────────

    async def startup(self) -> bool:
        """
        Run all startup hooks in priority order.
        Returns True if all required hooks succeeded.
        """
        if self._state != AppState.CREATED:
            logger.warning(f"Cannot start: current state is {self._state.name}")
            return False

        self._state = AppState.STARTING
        self._start_time = time.time()
        event_bus.emit("app.starting", source="lifecycle")

        # Sort by priority descending (higher = first)
        hooks = sorted(self._startup_hooks, key=lambda h: h.priority, reverse=True)

        for hook in hooks:
            success = await self._execute_hook(hook, phase="startup")
            if not success and hook.required:
                logger.error(f"Required startup hook failed: {hook.name}")
                self._state = AppState.ERROR
                event_bus.emit(
                    "app.startup.failed",
                    source="lifecycle",
                    hook=hook.name,
                )
                return False

        self._state = AppState.RUNNING
        elapsed = time.time() - self._start_time
        event_bus.emit(
            "app.started",
            source="lifecycle",
            elapsed=elapsed,
            services=list(self._services.keys()),
        )
        logger.info(f"Application started in {elapsed:.2f}s")
        return True

    # ── Shutdown ───────────────────────────────────────────

    async def shutdown(self) -> None:
        """Run all shutdown hooks in priority order."""
        if self._state in (AppState.STOPPING, AppState.STOPPED):
            return

        self._state = AppState.STOPPING
        event_bus.emit("app.stopping", source="lifecycle")
        logger.info("Application shutting down...")

        # Sort by priority descending
        hooks = sorted(self._shutdown_hooks, key=lambda h: h.priority, reverse=True)

        for hook in hooks:
            await self._execute_hook(hook, phase="shutdown")

        self._state = AppState.STOPPED
        event_bus.emit("app.stopped", source="lifecycle", uptime=self.uptime)
        logger.info(f"Application stopped (uptime: {self.uptime:.1f}s)")

    # ── Hook Execution ─────────────────────────────────────

    async def _execute_hook(self, hook: LifecycleHook, phase: str) -> bool:
        """Execute a single lifecycle hook with error boundary."""
        with ErrorBoundary(
            context=f"{phase}:{hook.name}",
            severity=ErrorSeverity.HIGH if hook.required else ErrorSeverity.LOW,
            reraise=False,
        ) as boundary:
            logger.debug(f"Running {phase} hook: {hook.name}")

            if hook.is_async:
                try:
                    await asyncio.wait_for(
                        hook.callback(),
                        timeout=hook.timeout,
                    )
                except asyncio.TimeoutError:
                    logger.error(
                        f"{phase} hook timed out after {hook.timeout}s: {hook.name}"
                    )
                    return False
            else:
                result = hook.callback()
                if inspect.isawaitable(result):
                    try:
                        await asyncio.wait_for(result, timeout=hook.timeout)
                    except asyncio.TimeoutError:
                        logger.error(
                            f"{phase} hook timed out after {hook.timeout}s: {hook.name}"
                        )
                        return False

        return boundary.error is None

    # ── Signal Handling ────────────────────────────────────

    def install_signal_handlers(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """Install SIGINT/SIGTERM handlers for graceful shutdown."""
        import sys

        if sys.platform == "win32":
            # Windows doesn't support loop.add_signal_handler
            return

        target_loop = loop
        if target_loop is None:
            try:
                target_loop = asyncio.get_running_loop()
            except RuntimeError:
                return

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                target_loop.add_signal_handler(
                    sig,
                    lambda s=sig: asyncio.create_task(self._handle_signal(s)),
                )
            except (NotImplementedError, RuntimeError):
                pass

    async def _handle_signal(self, sig: signal.Signals) -> None:
        """Handle OS signal for graceful shutdown."""
        logger.info(f"Received signal {sig.name}, initiating shutdown...")
        event_bus.emit("app.signal", source="lifecycle", signal=sig.name)
        await self.shutdown()

    # ── Health Check ───────────────────────────────────────

    def health_check(self) -> Dict[str, Any]:
        """Return application health status."""
        return {
            "state": self._state.name,
            "uptime": self.uptime,
            "services": list(self._services.keys()),
            "resources": list(self._resources.keys()),
            "startup_hooks": len(self._startup_hooks),
            "shutdown_hooks": len(self._shutdown_hooks),
        }
