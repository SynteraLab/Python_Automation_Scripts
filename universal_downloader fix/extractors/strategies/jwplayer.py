"""Reusable JWPlayer extraction strategy."""

from __future__ import annotations

from typing import Any, Optional

from .base import ExtractionStrategy, StrategyResult


class JWPlayerStrategy(ExtractionStrategy):
    NAME = "jwplayer"
    PRIORITY = 30
    STAGE = "player"

    async def applies(self, ctx: Any) -> bool:
        from extractors.jwplayer import JWPlayerExtractor

        if JWPlayerExtractor.suitable(ctx.url):
            return True
        if not ctx.html:
            ctx.html = await self.extractor._fetch_page_async(ctx)
        return JWPlayerExtractor.can_handle(ctx.html or '')

    async def execute(self, ctx: Any) -> Optional[StrategyResult]:
        from extractors.jwplayer import JWPlayerExtractor

        helper = JWPlayerExtractor(self.extractor.session, self.extractor.config)

        jw_id = helper._extract_platform_id_from_url(ctx.url)
        if not jw_id and ctx.html:
            jw_id = helper._find_platform_id_in_html(ctx.html)
        if jw_id:
            media_info = helper._extract_from_platform_api(jw_id, ctx.original_url)
            return StrategyResult(
                strategy=self.NAME,
                formats=media_info.formats,
                metadata={
                    'title': media_info.title,
                    'thumbnail': media_info.thumbnail,
                    'duration': media_info.duration,
                    'description': media_info.description,
                },
                confidence=0.96,
                stop_fallback=True,
            )

        if not ctx.html:
            ctx.html = await self.extractor._fetch_page_async(ctx)

        configs = helper._extract_setup_configs(ctx.html or '')
        if configs:
            all_formats = []
            metadata = {}
            for config in configs:
                formats, meta = helper._extract_from_config(config, ctx.final_url or ctx.url)
                all_formats.extend(formats)
                for key, value in meta.items():
                    if value and not metadata.get(key):
                        metadata[key] = value
            if all_formats:
                return StrategyResult(
                    strategy=self.NAME,
                    formats=helper._deduplicate_formats(all_formats),
                    metadata=metadata,
                    confidence=0.9,
                    stop_fallback=True,
                )

        source_formats = helper._extract_source_urls_from_html(ctx.html or '', ctx.final_url or ctx.url)
        if not source_formats:
            return None

        return StrategyResult(
            strategy=self.NAME,
            formats=source_formats,
            metadata={},
            confidence=0.75,
        )
