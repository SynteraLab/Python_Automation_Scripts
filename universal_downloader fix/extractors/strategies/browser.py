"""Browser-rendered fallback strategy."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from .base import ExtractionStrategy, StrategyResult


class BrowserNetworkStrategy(ExtractionStrategy):
    NAME = "browser"
    PRIORITY = 200
    STAGE = "browser"

    async def applies(self, ctx: Any) -> bool:
        return bool(ctx.hints.get('allow_browser_fallback', True))

    async def execute(self, ctx: Any) -> Optional[StrategyResult]:
        from extractors.advanced import AdvancedExtractor, PLAYWRIGHT_AVAILABLE

        if not PLAYWRIGHT_AVAILABLE:
            return None

        helper = AdvancedExtractor(self.extractor.session, self.extractor.config)
        media_info = await asyncio.to_thread(helper.extract, ctx.original_url)
        if not media_info or not media_info.formats:
            return None

        return StrategyResult(
            strategy=self.NAME,
            formats=media_info.formats,
            metadata={
                'title': media_info.title,
                'thumbnail': media_info.thumbnail,
                'description': media_info.description,
                'duration': media_info.duration,
            },
            confidence=0.78,
        )
