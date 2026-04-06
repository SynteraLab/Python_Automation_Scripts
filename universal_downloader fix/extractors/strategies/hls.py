"""Reusable HLS extraction strategy."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from models.media import StreamFormat, StreamType

from .base import ExtractionStrategy, StrategyResult


class HLSStrategy(ExtractionStrategy):
    NAME = "hls"
    PRIORITY = 35
    STAGE = "player"

    async def applies(self, ctx: Any) -> bool:
        url_lower = ctx.url.lower()
        if '.m3u8' in url_lower:
            return True
        if ctx.response_headers.get('Content-Type', '').lower().find('mpegurl') >= 0:
            return True
        if ctx.html and '#EXTM3U' in ctx.html:
            return True
        return False

    async def execute(self, ctx: Any) -> Optional[StrategyResult]:
        from extractors.hls import HLSParser

        if not ctx.html or '#EXTM3U' not in (ctx.html or ''):
            ctx.html = await self.extractor._fetch_page_async(ctx)
        if '#EXTM3U' not in (ctx.html or ''):
            return None

        parser = HLSParser(ctx.final_url or ctx.url, ctx.html or '')
        metadata: Dict[str, Any] = {}
        formats: List[StreamFormat] = []

        if parser.is_master_playlist():
            variants = parser.parse_master_playlist()
            audio_tracks = []
            subtitles: Dict[str, List[Dict[str, Any]]] = {}

            for idx, item in enumerate(variants):
                item_type = item.get('type')
                if item_type == 'audio':
                    audio_tracks.append(item)
                    formats.append(self._audio_format(item, idx, ctx))
                    continue
                if item_type == 'subtitles':
                    language = str(item.get('language') or item.get('name') or 'und')
                    subtitles.setdefault(language, []).append({
                        'url': item['url'],
                        'name': item.get('name'),
                        'ext': 'vtt',
                    })
                    continue
                formats.append(self._video_format(item, idx, ctx))

            if subtitles:
                metadata['subtitles'] = subtitles
            if audio_tracks:
                metadata['audio_tracks'] = audio_tracks
            confidence = 0.93
        else:
            media_info = parser.parse_media_playlist()
            if media_info.get('total_duration'):
                metadata['duration'] = int(media_info['total_duration'])
            formats = [StreamFormat(
                format_id='hls-0',
                url=ctx.final_url or ctx.url,
                ext='mp4',
                stream_type=StreamType.HLS,
                headers={'Referer': ctx.state.get('embedded_from') or ctx.original_url},
            )]
            confidence = 0.82

        if not formats:
            return None

        return StrategyResult(
            strategy=self.NAME,
            formats=formats,
            metadata=metadata,
            confidence=confidence,
            stop_fallback=True,
        )

    @staticmethod
    def _video_format(item: Dict[str, Any], idx: int, ctx: Any) -> StreamFormat:
        return StreamFormat(
            format_id=f"hls-{idx}",
            url=item['url'],
            ext='mp4',
            quality=item.get('quality'),
            width=item.get('width'),
            height=item.get('height'),
            fps=item.get('fps'),
            vcodec=item.get('vcodec'),
            acodec=item.get('acodec'),
            bitrate=item.get('bitrate'),
            stream_type=StreamType.HLS,
            is_video=item.get('is_video', True),
            is_audio=item.get('is_audio', True),
            headers={'Referer': ctx.state.get('embedded_from') or ctx.original_url},
            label=item.get('name') or item.get('quality'),
        )

    @staticmethod
    def _audio_format(item: Dict[str, Any], idx: int, ctx: Any) -> StreamFormat:
        return StreamFormat(
            format_id=f"hls-audio-{idx}",
            url=item['url'],
            ext='m4a',
            quality=item.get('name') or item.get('language'),
            stream_type=StreamType.HLS,
            is_video=False,
            is_audio=True,
            headers={'Referer': ctx.state.get('embedded_from') or ctx.original_url},
            label=item.get('name') or item.get('language'),
        )
