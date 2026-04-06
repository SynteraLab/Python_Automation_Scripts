"""Safe execution wrapper for plugin code."""

import logging
from typing import Any, Callable

from ..engine.errors import SafeExecutor, ErrorBoundary, ErrorSeverity

logger = logging.getLogger(__name__)


def safe_plugin_call(func: Callable, *args: Any, plugin_name: str = "unknown", **kwargs: Any) -> Any:
    """Execute plugin code within an error boundary."""
    result = SafeExecutor.run(
        func, *args,
        context=f"plugin:{plugin_name}",
        fallback=None,
        emit_event=True,
        **kwargs,
    )
    if result.failed:
        logger.warning(f"Plugin '{plugin_name}' call failed: {result.error}")
    return result.value