"""Generic HTML/media URL extraction strategy."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from models.media import StreamFormat, StreamType
from utils.parser import MediaURLExtractor, MetadataExtractor

from .base import ExtractionStrategy, StrategyResult


class HTMLParsingStrategy(ExtractionStrategy):
    NAME = "html"
    PRIORITY = 80
    REQUIRES_HTML = True
    STAGE = "content"

    async def execute(self, ctx: Any) -> Optional[StrategyResult]:
        if not ctx.html:
            ctx.html = await self.extractor._fetch_page_async(ctx)

        extractor = MediaURLExtractor(ctx.final_url or ctx.url)
        found_urls = extractor.extract_from_html(ctx.html or "")
        if not found_urls:
            return None

        metadata = MetadataExtractor(ctx.original_url).extract(ctx.html or "")
        formats = self._build_formats(found_urls, ctx.final_url or ctx.url)
        if not formats:
            return None

        return StrategyResult(
            strategy=self.NAME,
            formats=formats,
            metadata=metadata,
            confidence=0.55,
            debug={"found_urls": len(found_urls)},
        )

    def _build_formats(self, found_urls: List[Dict[str, Any]], page_url: str) -> List[StreamFormat]:
        formats: List[StreamFormat] = []
        seen = set()

        for idx, url_info in enumerate(found_urls):
            media_url = url_info.get('url')
            url_type = str(url_info.get('type') or '')
            if not media_url or url_type in {'thumbnail', 'embed', 'iframe'} or media_url in seen:
                continue
            seen.add(media_url)

            quality, width, height = self._parse_quality_from_url(media_url)
            stream_type = self.extractor._detect_stream_type(media_url)
            formats.append(StreamFormat(
                format_id=f"html-{idx}",
                url=media_url,
                ext=self._guess_extension(media_url, stream_type),
                quality=quality,
                width=width,
                height=height,
                stream_type=stream_type,
                headers={'Referer': page_url},
                is_video=url_type != 'audio',
                is_audio=True,
                label=url_info.get('source'),
            ))

        formats.sort(key=lambda item: item.quality_score, reverse=True)
        return formats

    @staticmethod
    def _guess_extension(url: str, stream_type: StreamType) -> str:
        lower = url.lower()
        if '.webm' in lower:
            return 'webm'
        if '.mp3' in lower or '.m4a' in lower or '.aac' in lower:
            return 'mp3'
        if stream_type in {StreamType.HLS, StreamType.DASH}:
            return 'mp4'
        return 'mp4'

    @staticmethod
    def _parse_quality_from_url(url: str) -> tuple[Optional[str], Optional[int], Optional[int]]:
        url_lower = url.lower()
        for pattern in (r'(\d{3,4})p', r'(\d{3,4})x(\d{3,4})'):
            match = re.search(pattern, url_lower)
            if not match:
                continue
            if len(match.groups()) == 2:
                width = int(match.group(1))
                height = int(match.group(2))
                return f"{height}p", width, height
            height = int(match.group(1))
            return f"{height}p", int(height * 16 / 9), height
        return None, None, None
