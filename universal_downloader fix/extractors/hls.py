"""
HLS (HTTP Live Streaming) extractor and parser.
"""

import re
from typing import List, Dict, Optional
from urllib.parse import urljoin
import logging

from .base import ExtractorBase, register_extractor, ExtractionError
from models.media import MediaInfo, StreamFormat, MediaType, StreamType
from .strategies.hls import HLSStrategy

logger = logging.getLogger(__name__)


class HLSParser:
    """Parser for M3U8 HLS playlists."""

    def __init__(self, base_url: str, content: str):
        self.base_url = base_url
        self.content = content
        self.lines = content.strip().split('\n')

    def is_master_playlist(self) -> bool:
        return '#EXT-X-STREAM-INF' in self.content

    def parse_master_playlist(self) -> List[Dict]:
        variants = []
        current_info = {}

        for line in self.lines:
            line = line.strip()
            if line.startswith('#EXT-X-STREAM-INF:'):
                current_info = self._parse_stream_info(line)
            elif line and not line.startswith('#'):
                if current_info:
                    current_info['url'] = urljoin(self.base_url, line)
                    variants.append(current_info)
                    current_info = {}
            elif line.startswith('#EXT-X-MEDIA:'):
                media_info = self._parse_media_tag(line)
                if media_info:
                    variants.append(media_info)
        return variants

    def parse_media_playlist(self) -> Dict:
        segments = []
        current_duration = 0
        total_duration = 0
        encryption = None

        for line in self.lines:
            line = line.strip()
            if line.startswith('#EXTINF:'):
                match = re.match(r'#EXTINF:([\d.]+)', line)
                if match:
                    current_duration = float(match.group(1))
            elif line.startswith('#EXT-X-KEY:'):
                encryption = self._parse_encryption(line)
            elif line and not line.startswith('#'):
                segments.append({
                    'url': urljoin(self.base_url, line),
                    'duration': current_duration
                })
                total_duration += current_duration
                current_duration = 0

        return {
            'segments': segments,
            'total_duration': total_duration,
            'encryption': encryption
        }

    def _parse_stream_info(self, line: str) -> Dict:
        info = {'type': 'video', 'is_video': True, 'is_audio': True}
        attr_string = line.replace('#EXT-X-STREAM-INF:', '')

        bw = re.search(r'BANDWIDTH=(\d+)', attr_string)
        if bw:
            info['bitrate'] = int(bw.group(1)) // 1000

        res = re.search(r'RESOLUTION=(\d+)x(\d+)', attr_string)
        if res:
            info['width'] = int(res.group(1))
            info['height'] = int(res.group(2))
            info['quality'] = f"{info['height']}p"

        codecs = re.search(r'CODECS="([^"]+)"', attr_string)
        if codecs:
            info['codecs'] = codecs.group(1)
            for codec in codecs.group(1).split(','):
                codec = codec.strip()
                if codec.startswith(('avc', 'hvc', 'vp', 'av0')):
                    info['vcodec'] = codec
                elif codec.startswith(('mp4a', 'ac-3', 'ec-3', 'opus')):
                    info['acodec'] = codec

        fps = re.search(r'FRAME-RATE=([\d.]+)', attr_string)
        if fps:
            info['fps'] = int(float(fps.group(1)))

        return info

    def _parse_media_tag(self, line: str) -> Optional[Dict]:
        info = {}
        type_match = re.search(r'TYPE=(\w+)', line)
        if type_match:
            info['type'] = type_match.group(1).lower()
        uri_match = re.search(r'URI="([^"]+)"', line)
        if uri_match:
            info['url'] = urljoin(self.base_url, uri_match.group(1))
        name_match = re.search(r'NAME="([^"]+)"', line)
        if name_match:
            info['name'] = name_match.group(1)
        lang_match = re.search(r'LANGUAGE="([^"]+)"', line)
        if lang_match:
            info['language'] = lang_match.group(1)

        if info.get('type') == 'audio':
            info['is_video'] = False
            info['is_audio'] = True
        elif info.get('type') == 'subtitles':
            info['is_video'] = False
            info['is_audio'] = False

        return info if 'url' in info else None

    def _parse_encryption(self, line: str) -> Optional[Dict]:
        encryption = {}
        method = re.search(r'METHOD=(\w+)', line)
        if method:
            encryption['method'] = method.group(1)
        uri = re.search(r'URI="([^"]+)"', line)
        if uri:
            encryption['key_url'] = urljoin(self.base_url, uri.group(1))
        iv = re.search(r'IV=0x([0-9a-fA-F]+)', line)
        if iv:
            encryption['iv'] = iv.group(1)
        return encryption if encryption else None


@register_extractor()
class HLSExtractor(ExtractorBase):
    """Extractor for HLS (M3U8) streams."""

    EXTRACTOR_NAME = "hls"
    EXTRACTOR_DESCRIPTION = "HLS M3U8 stream extractor"

    URL_PATTERNS = [
        r'https?://[^\s]+\.m3u8(?:\?[^\s]*)?$',
    ]

    DEFAULT_HINTS = {
        'strategy_order': ['hls'],
    }

    STRATEGIES = [HLSStrategy]

    def extract(self, url: str) -> MediaInfo:
        logger.info(f"HLS extraction for: {url}")
        return self.extract_with_strategies(url)

    def _process_master_playlist(self, parser: HLSParser) -> List[StreamFormat]:
        variants = parser.parse_master_playlist()
        formats = []
        for idx, variant in enumerate(variants):
            if variant.get('type') not in ['video', 'audio', None]:
                continue
            formats.append(StreamFormat(
                format_id=f"hls-{idx}",
                url=variant['url'], ext='mp4',
                quality=variant.get('quality'),
                width=variant.get('width'),
                height=variant.get('height'),
                fps=variant.get('fps'),
                vcodec=variant.get('vcodec'),
                acodec=variant.get('acodec'),
                bitrate=variant.get('bitrate'),
                stream_type=StreamType.HLS,
                is_video=variant.get('is_video', True),
                is_audio=variant.get('is_audio', True)
            ))
        formats.sort(key=lambda f: f.quality_score, reverse=True)
        return formats

    def _create_single_format(self, url: str, parser: HLSParser) -> StreamFormat:
        return StreamFormat(
            format_id="hls-0", url=url, ext='mp4',
            stream_type=StreamType.HLS
        )

    def _extract_title_from_url(self, url: str) -> str:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        path_parts = parsed.path.split('/')
        for part in reversed(path_parts):
            if part and not part.endswith('.m3u8'):
                return part.replace('_', ' ').replace('-', ' ').title()
        return "HLS Stream"
