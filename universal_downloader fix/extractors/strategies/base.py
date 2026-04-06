"""Base classes for reusable extraction strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from models.media import StreamFormat


class StrategyError(Exception):
    """Raised when a strategy fails during execution."""

    def __init__(self, message: str, *, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


@dataclass(slots=True)
class StrategyResult:
    """Structured strategy output used by the extraction engine."""

    strategy: str
    formats: List[StreamFormat] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    score: float = 0.0
    warnings: List[str] = field(default_factory=list)
    debug: Dict[str, Any] = field(default_factory=dict)
    stop_fallback: bool = False


class ExtractionStrategy(ABC):
    """Base strategy interface."""

    NAME = "base"
    PRIORITY = 100
    REQUIRES_HTML = False
    STAGE = "content"

    def __init__(self, extractor: Any):
        self.extractor = extractor

    async def applies(self, ctx: Any) -> bool:
        return True

    @abstractmethod
    async def execute(self, ctx: Any) -> Optional[StrategyResult]:
        raise NotImplementedError
