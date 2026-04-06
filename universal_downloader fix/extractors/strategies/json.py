"""JSON-focused extraction strategy."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from models.media import StreamFormat
from utils.parser import MediaURLExtractor, MetadataExtractor

from .base import ExtractionStrategy, StrategyResult


class JSONStrategy(ExtractionStrategy):
    NAME = "json"
    PRIORITY = 70
    REQUIRES_HTML = True
    STAGE = "content"

    async def execute(self, ctx: Any) -> Optional[StrategyResult]:
        if not ctx.html:
            ctx.html = await self.extractor._fetch_page_async(ctx)

        parser = MediaURLExtractor(ctx.final_url or ctx.url)
        json_hits = self._extract_json_hits(parser, ctx.html or "")
        if not json_hits:
            return None

        metadata = MetadataExtractor(ctx.original_url).extract(ctx.html or "")
        formats: List[StreamFormat] = []
        seen = set()
        for idx, hit in enumerate(json_hits):
            media_url = hit.get('url')
            if not media_url or media_url in seen or hit.get('type') == 'thumbnail':
                continue
            seen.add(media_url)
            formats.append(self.extractor._create_format(
                media_url,
                format_id=f"json-{idx}",
                ext='mp4',
                stream_type=self.extractor._detect_stream_type(media_url),
                headers={'Referer': ctx.final_url or ctx.url},
                label=str(hit.get('source') or 'json'),
            ))

        if not formats:
            return None

        return StrategyResult(
            strategy=self.NAME,
            formats=formats,
            metadata=metadata,
            confidence=0.62,
            debug={'json_hits': len(json_hits)},
        )

    @staticmethod
    def _extract_json_hits(parser: MediaURLExtractor, html: str) -> List[Dict[str, Any]]:
        hits: List[Dict[str, Any]] = []
        for script_text in re.findall(r'<script[^>]*>(.*?)</script>', html, re.IGNORECASE | re.DOTALL):
            hits.extend(parser._extract_from_json_in_script(script_text))
        return parser._deduplicate_urls(hits)
