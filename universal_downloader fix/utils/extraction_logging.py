"""Structured logging helpers for extractor and strategy execution."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional


class ExtractionLogger(logging.LoggerAdapter):
    """Small logger adapter for extractor-scoped log events."""

    def bind(self, **extra: Any) -> "ExtractionLogger":
        merged = dict(self.extra)
        merged.update(extra)
        return ExtractionLogger(self.logger, merged)

    def event(
        self,
        status: str,
        message: str,
        *,
        details: Optional[Dict[str, Any]] = None,
        level: int = logging.INFO,
    ) -> None:
        prefix = f"[Extractor:{self.extra.get('extractor', '?')}]"
        strategy = self.extra.get('strategy')
        if strategy:
            prefix += f"[Strategy:{strategy}]"
        prefix += f"[{status}]"
        self.log(level, "%s %s", prefix, message, extra={"details": details or {}})


def get_extraction_logger(name: str, extractor: str) -> ExtractionLogger:
    return ExtractionLogger(logging.getLogger(name), {"extractor": extractor})
