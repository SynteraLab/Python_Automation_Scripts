"""API probing strategy driven by extractor hints."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.parser import MediaURLExtractor

from .base import ExtractionStrategy, StrategyResult


class APIExtractionStrategy(ExtractionStrategy):
    NAME = "api"
    PRIORITY = 65
    STAGE = "discovery"

    async def applies(self, ctx: Any) -> bool:
        return bool(ctx.hints.get('api_endpoints'))

    async def execute(self, ctx: Any) -> Optional[StrategyResult]:
        endpoints = list(ctx.hints.get('api_endpoints') or [])
        if not endpoints:
            return None

        parser = MediaURLExtractor(ctx.final_url or ctx.url)
        formats = []
        metadata: Dict[str, Any] = {}

        for idx, endpoint in enumerate(endpoints):
            document = await ctx.request.get_json(endpoint, headers=ctx.hints.get('api_headers'))
            urls = parser._extract_urls_from_json(document.json_data)
            for url_idx, item in enumerate(urls):
                media_url = item.get('url')
                if not media_url:
                    continue
                formats.append(self.extractor._create_format(
                    media_url,
                    format_id=f"api-{idx}-{url_idx}",
                    ext='mp4',
                    stream_type=self.extractor._detect_stream_type(media_url),
                    headers={'Referer': ctx.final_url or ctx.url},
                    label='api',
                ))

            if isinstance(document.json_data, dict):
                metadata.update({
                    'title': document.json_data.get('title') or metadata.get('title'),
                    'thumbnail': document.json_data.get('thumbnail') or metadata.get('thumbnail'),
                    'description': document.json_data.get('description') or metadata.get('description'),
                })

        if not formats:
            return None

        return StrategyResult(
            strategy=self.NAME,
            formats=formats,
            metadata=metadata,
            confidence=0.74,
            debug={'endpoints': len(endpoints)},
        )
