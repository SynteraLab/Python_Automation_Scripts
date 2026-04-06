"""Packed-JavaScript player extraction strategy."""

from __future__ import annotations

from typing import Any, Optional

from utils.parser import MetadataExtractor

from .base import ExtractionStrategy, StrategyResult


class PackedPlayerStrategy(ExtractionStrategy):
    NAME = "packed_player"
    PRIORITY = 28
    STAGE = "player"

    async def applies(self, ctx: Any) -> bool:
        if not ctx.html:
            ctx.html = await self.extractor._fetch_page_async(ctx)
        return 'eval(function' in (ctx.html or '') or 'file:' in (ctx.html or '')

    async def execute(self, ctx: Any) -> Optional[StrategyResult]:
        from extractors.supjav_legacy import SupJavExtractor

        if not ctx.html:
            ctx.html = await self.extractor._fetch_page_async(ctx)

        helper = SupJavExtractor(self.extractor.session, self.extractor.config)
        helper._duration_hint = helper._extract_duration_from_text(ctx.html or '')
        formats = helper._extract_from_player_html(ctx.html or '', ctx.final_url or ctx.url, server_name='PackedPlayer')
        if not formats:
            return None

        metadata = MetadataExtractor(ctx.original_url).extract(ctx.state.get('outer_html') or ctx.html or '')
        if helper._duration_hint and not metadata.get('duration'):
            metadata['duration'] = helper._duration_hint

        return StrategyResult(
            strategy=self.NAME,
            formats=formats,
            metadata=metadata,
            confidence=0.84,
            stop_fallback=True,
        )
