"""
Error boundaries and safe execution utilities.

Design:
- TUIError hierarchy for categorized errors
- SafeExecutor: wrap any callable with error handling
- ErrorBoundary: context manager for UI-safe blocks
- Emits events on errors for dashboard/logging integration

Usage:
    from tui.engine import SafeExecutor, ErrorBoundary

    # As context manager
    with ErrorBoundary("downloading video"):
        risky_operation()

    # As function wrapper
    result = SafeExecutor.run(risky_function, arg1, arg2,
                              fallback="default_value")
"""

import logging
import traceback
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable, Optional, TypeVar, Generic

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ErrorSeverity(Enum):
    """Error severity levels."""
    LOW = auto()       # Cosmetic / non-blocking
    MEDIUM = auto()    # Feature degraded but app works
    HIGH = auto()      # Feature broken
    CRITICAL = auto()  # App may need restart


class TUIError(Exception):
    """Base exception for all TUI errors."""

    def __init__(
        self,
        message: str,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        context: Optional[str] = None,
        cause: Optional[Exception] = None,
    ):
        self.severity = severity
        self.context = context
        self.cause = cause
        super().__init__(message)

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.context:
            parts.append(f"[context: {self.context}]")
        if self.cause:
            parts.append(f"[caused by: {self.cause}]")
        return " ".join(parts)


class ExtractorError(TUIError):
    """Error during media extraction."""
    pass


class DownloadError(TUIError):
    """Error during download."""
    pass


class WorkflowError(TUIError):
    """Error in workflow execution."""
    pass


class PluginError(TUIError):
    """Error in plugin loading/execution."""
    pass


class UIError(TUIError):
    """Error in UI rendering."""
    pass


@dataclass
class ErrorResult(Generic[T]):
    """
    Result wrapper that captures success or failure.

    Usage:
        result = SafeExecutor.run(func, arg)
        if result.ok:
            use(result.value)
        else:
            handle(result.error)
    """
    ok: bool
    value: Optional[T] = None
    error: Optional[Exception] = None
    traceback_str: Optional[str] = None

    @property
    def failed(self) -> bool:
        return not self.ok


class SafeExecutor:
    """
    Execute callables with automatic error handling.

    Features:
    - Returns ErrorResult instead of raising
    - Optional fallback value
    - Emits error events via event bus
    - Logs errors with context
    """

    @staticmethod
    def run(
        func: Callable[..., T],
        *args: Any,
        fallback: Optional[T] = None,
        context: str = "",
        emit_event: bool = True,
        reraise: bool = False,
        **kwargs: Any,
    ) -> ErrorResult[T]:
        """
        Safely execute a callable.

        Args:
            func: Callable to execute
            *args: Positional arguments
            fallback: Value to use on failure
            context: Description for error logging
            emit_event: Whether to emit error event
            reraise: If True, re-raise after handling
            **kwargs: Keyword arguments

        Returns:
            ErrorResult with value or error
        """
        try:
            result = func(*args, **kwargs)
            return ErrorResult(ok=True, value=result)
        except KeyboardInterrupt:
            # Never swallow Ctrl+C
            raise
        except Exception as e:
            tb = traceback.format_exc()
            ctx = context or func.__name__
            logger.error(f"SafeExecutor error in {ctx}: {e}")
            logger.debug(tb)

            if emit_event:
                try:
                    from .events import event_bus
                    event_bus.emit(
                        "error.caught",
                        source="SafeExecutor",
                        context=ctx,
                        error=str(e),
                        error_type=type(e).__name__,
                        traceback=tb,
                    )
                except Exception:
                    pass  # Don't let event emission cause another error

            if reraise:
                raise

            return ErrorResult(
                ok=False,
                value=fallback,
                error=e,
                traceback_str=tb,
            )

    @staticmethod
    async def run_async(
        func: Callable[..., Any],
        *args: Any,
        fallback: Any = None,
        context: str = "",
        emit_event: bool = True,
        **kwargs: Any,
    ) -> ErrorResult:
        """Async version of run()."""
        try:
            result = await func(*args, **kwargs)
            return ErrorResult(ok=True, value=result)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            tb = traceback.format_exc()
            ctx = context or getattr(func, "__name__", "async_task")
            logger.error(f"SafeExecutor async error in {ctx}: {e}")
            logger.debug(tb)

            if emit_event:
                try:
                    from .events import event_bus
                    event_bus.emit(
                        "error.caught",
                        source="SafeExecutor.async",
                        context=ctx,
                        error=str(e),
                        error_type=type(e).__name__,
                        traceback=tb,
                    )
                except Exception:
                    pass

            return ErrorResult(
                ok=False,
                value=fallback,
                error=e,
                traceback_str=tb,
            )


class ErrorBoundary:
    """
    Context manager that catches and handles errors gracefully.

    Usage:
        with ErrorBoundary("loading plugins", severity=ErrorSeverity.LOW):
            load_all_plugins()

        # UI never crashes, error is logged and emitted
    """

    def __init__(
        self,
        context: str = "",
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        fallback_callback: Optional[Callable[[Exception], None]] = None,
        reraise: bool = False,
        emit_event: bool = True,
    ):
        self.context = context
        self.severity = severity
        self.fallback_callback = fallback_callback
        self.reraise = reraise
        self.emit_event = emit_event
        self.error: Optional[Exception] = None

    def __enter__(self) -> "ErrorBoundary":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is None:
            return False

        if exc_type is KeyboardInterrupt:
            return False  # Never swallow Ctrl+C

        self.error = exc_val
        tb = traceback.format_exc()
        logger.error(f"ErrorBoundary [{self.context}]: {exc_val}")
        logger.debug(tb)

        if self.emit_event:
            try:
                from .events import event_bus
                event_bus.emit(
                    "error.boundary",
                    source="ErrorBoundary",
                    context=self.context,
                    severity=self.severity.name,
                    error=str(exc_val),
                    error_type=exc_type.__name__,
                    traceback=tb,
                )
            except Exception:
                pass

        if self.fallback_callback:
            try:
                self.fallback_callback(exc_val)
            except Exception as cb_err:
                logger.error(f"ErrorBoundary fallback callback failed: {cb_err}")

        if self.reraise:
            return False  # Let exception propagate

        return True  # Swallow exception


class RetryExecutor:
    """
    Execute with automatic retry on failure.

    Usage:
        result = RetryExecutor.run(
            flaky_function, arg1,
            max_retries=3,
            delay=1.0,
            context="fetching data"
        )
    """

    @staticmethod
    def run(
        func: Callable[..., T],
        *args: Any,
        max_retries: int = 3,
        delay: float = 1.0,
        backoff: float = 2.0,
        context: str = "",
        **kwargs: Any,
    ) -> ErrorResult[T]:
        """
        Execute with retries.

        Args:
            func: Callable to execute
            max_retries: Max retry attempts
            delay: Initial delay between retries (seconds)
            backoff: Multiplier for delay on each retry
            context: Description for logging
        """
        import time as _time

        last_error = None
        current_delay = delay

        for attempt in range(max_retries + 1):
            try:
                result = func(*args, **kwargs)
                return ErrorResult(ok=True, value=result)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                last_error = e
                ctx = context or func.__name__
                if attempt < max_retries:
                    logger.warning(
                        f"Retry {attempt + 1}/{max_retries} for {ctx}: {e}"
                    )
                    _time.sleep(current_delay)
                    current_delay *= backoff
                else:
                    logger.error(f"All {max_retries} retries failed for {ctx}: {e}")

        return ErrorResult(
            ok=False,
            error=last_error,
            traceback_str=traceback.format_exc(),
        )