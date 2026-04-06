"""
JWPlayer Universal Extractor.

Extracts video from any website using JWPlayer 7/8.
Supports JW Platform API, embedded configs, and common JWPlayer patterns.
"""

import re
import json
from typing import List, Dict, Optional, Any
from urllib.parse import urljoin, urlparse, unquote
import logging

from .base import ExtractorBase, register_extractor, ExtractionError
from models.media import MediaInfo, StreamFormat, MediaType, StreamType
from .strategies.html import HTMLParsingStrategy
from .strategies.jwplayer import JWPlayerStrategy

logger = logging.getLogger(__name__)


@register_extractor()
class JWPlayerExtractor(ExtractorBase):
    """
    Universal extractor for sites using JWPlayer.

    Detects JWPlayer configurations in HTML/JavaScript and extracts
    all available video sources and quality options.

    Works with:
    - jwplayer().setup({...}) calls
    - JW Platform hosted videos (cdn.jwplayer.com)
    - playerInstance.setup({...})
    - Inline JSON configs
    - JWPlayer 7.x and 8.x
    - JW Platform v2 API
    """

    EXTRACTOR_NAME = "jwplayer"
    EXTRACTOR_DESCRIPTION = "JWPlayer universal extractor (auto-detects JWPlayer on any site)"

    URL_PATTERNS = [
        r'https?://(?:content|cdn|videos-cloudflare)\.jwplatform\.com/.+',
        r'https?://cdn\.jwplayer\.com/.+',
    ]

    DEFAULT_HINTS = {
        'strategy_order': ['jwplayer', 'html'],
    }

    STRATEGIES = [JWPlayerStrategy, HTMLParsingStrategy]

    # Regex patterns to find jwplayer setup calls
    SETUP_PATTERNS = [
        # jwplayer("id").setup({...})
        r'jwplayer\s*\(\s*["\'][^"\']*["\']\s*\)\s*\.setup\s*\(\s*(\{.+?\})\s*\)\s*[;,]',
        # jwplayer().setup({...})
        r'jwplayer\s*\(\s*\)\s*\.setup\s*\(\s*(\{.+?\})\s*\)\s*[;,]',
        # playerInstance.setup({...})
        r'(?:playerInstance|jwInstance|player|videoPlayer)\s*\.setup\s*\(\s*(\{.+?\})\s*\)\s*[;,]',
        # var config = {...}; used for jwplayer
        r'(?:var|let|const)\s+(?:jwConfig|playerConfig|jwSetup|playerSetup|videoConfig|config)\s*=\s*(\{.+?\})\s*;',
    ]

    # Broader patterns (may produce false positives, used as fallback)
    BROAD_SETUP_PATTERNS = [
        r'jwplayer[^.]*\.setup\s*\(\s*(\{.+?\})\s*\)',
        r'\.setup\s*\(\s*(\{"[^"]*(?:file|sources|playlist)[^}]+\})\s*\)',
    ]

    # JW Platform media ID patterns
    PLATFORM_ID_PATTERNS = [
        r'(?:content|cdn|videos-cloudflare)\.jwplatform\.com/(?:manifests|videos|previews)/([A-Za-z0-9]{8})',
        r'cdn\.jwplayer\.com/(?:manifests|videos|previews|v2/media)/([A-Za-z0-9]{8})',
        r'player\.jwplatform\.com/players/([A-Za-z0-9]{8})',
        r'"mediaid"\s*:\s*"([A-Za-z0-9]{8})"',
        r'"media_id"\s*:\s*"([A-Za-z0-9]{8})"',
        r'data-media-id=["\']([A-Za-z0-9]{8})["\']',
        r'data-video-jw-id=["\']([A-Za-z0-9]{8})["\']',
        r'data-jw-media=["\']([A-Za-z0-9]{8})["\']',
        r'/players/([A-Za-z0-9]{8})-[A-Za-z0-9]{8}\.js',
    ]

    # Source URL patterns in JW config
    SOURCE_URL_PATTERNS = [
        r'"file"\s*:\s*"(https?://[^"]+)"',
        r"'file'\s*:\s*'(https?://[^']+)'",
        r'"src"\s*:\s*"(https?://[^"]+)"',
        r'"source"\s*:\s*"(https?://[^"]+)"',
        r'"hls"\s*:\s*"(https?://[^"]+)"',
        r'"dash"\s*:\s*"(https?://[^"]+)"',
    ]

    JW_PLATFORM_API = "https://cdn.jwplayer.com/v2/media/{media_id}"
    JW_PLATFORM_MANIFEST = "https://cdn.jwplayer.com/manifests/{media_id}.m3u8"

    def extract(self, url: str) -> MediaInfo:
        """Extract media information from a page using reusable JWPlayer strategies."""
        logger.info(f"JWPlayer extraction for: {url}")
        return self.extract_with_strategies(url)

    @classmethod
    def can_handle(cls, html: str) -> bool:
        """Check if HTML page contains JWPlayer (for auto-detection)."""
        indicators = [
            'jwplayer(', 'jwplayer.', 'jw-video',
            'cdn.jwplayer.com', 'jwplatform.com',
            'jw-player', 'jwplayer/', 'data-jw-',
        ]
        html_lower = html.lower()
        return any(ind in html_lower for ind in indicators)

    # ===== JW Platform API =====

    def _extract_platform_id_from_url(self, url: str) -> Optional[str]:
        for pattern in self.PLATFORM_ID_PATTERNS[:3]:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def _find_platform_id_in_html(self, html: str) -> Optional[str]:
        for pattern in self.PLATFORM_ID_PATTERNS:
            match = re.search(pattern, html)
            if match:
                media_id = match.group(1)
                if re.match(r'^[A-Za-z0-9]{8}$', media_id):
                    return media_id
        return None

    def _extract_from_platform_api(self, media_id: str, original_url: str) -> MediaInfo:
        """Extract using JW Platform v2 API."""
        api_url = self.JW_PLATFORM_API.format(media_id=media_id)

        try:
            data = self._fetch_json(api_url)
        except Exception:
            # Fallback to direct m3u8 manifest
            manifest_url = self.JW_PLATFORM_MANIFEST.format(media_id=media_id)
            return MediaInfo(
                id=media_id,
                title=f"JW Platform Video {media_id}",
                url=original_url,
                formats=[StreamFormat(
                    format_id="jw-hls-0",
                    url=manifest_url,
                    ext='mp4',
                    stream_type=StreamType.HLS,
                )],
                media_type=MediaType.VIDEO,
                extractor=self.EXTRACTOR_NAME,
            )

        # Parse API response
        playlist = data.get('playlist', [])
        if not playlist:
            raise ExtractionError("JW Platform API returned empty playlist")

        item = playlist[0]
        formats = []

        for idx, source in enumerate(item.get('sources', [])):
            src_url = source.get('file', '')
            if not src_url:
                continue

            stream_type = self._detect_stream_type(src_url)
            ext = 'mp4'
            if stream_type == StreamType.HLS:
                ext = 'mp4'  # Will be transcoded
            elif '.webm' in src_url.lower():
                ext = 'webm'

            formats.append(StreamFormat(
                format_id=f"jw-{idx}",
                url=src_url,
                ext=ext,
                quality=source.get('label'),
                width=source.get('width'),
                height=source.get('height'),
                stream_type=stream_type,
                label=source.get('label'),
                bitrate=source.get('bitrate'),
            ))

        # Sort by quality
        formats.sort(key=lambda f: f.quality_score, reverse=True)

        thumbnail = None
        if item.get('image'):
            thumbnail = item['image']
        elif item.get('images'):
            images = item['images']
            if isinstance(images, list) and images:
                thumbnail = images[0].get('src')

        return MediaInfo(
            id=media_id,
            title=item.get('title', f'JW Video {media_id}'),
            url=original_url,
            formats=formats,
            media_type=MediaType.VIDEO,
            extractor=self.EXTRACTOR_NAME,
            description=item.get('description'),
            thumbnail=thumbnail,
            duration=int(item.get('duration', 0)) or None,
        )

    # ===== Config Parsing =====

    def _extract_setup_configs(self, html: str) -> List[Dict]:
        """Extract jwplayer setup config objects from HTML."""
        configs = []

        all_patterns = self.SETUP_PATTERNS + self.BROAD_SETUP_PATTERNS

        for pattern in all_patterns:
            for match in re.finditer(pattern, html, re.DOTALL | re.IGNORECASE):
                raw = match.group(1)
                config = self._parse_js_object(raw)
                if config and self._is_valid_jw_config(config):
                    configs.append(config)

        # Also look for JSON configs in script tags or data attributes
        # Pattern: <div data-jw-config='{"file":"..."}'> etc.
        for match in re.finditer(r'data-(?:jw-config|player-config|video-config)=["\']({.+?})["\']', html):
            try:
                config = json.loads(match.group(1))
                if self._is_valid_jw_config(config):
                    configs.append(config)
            except (json.JSONDecodeError, ValueError):
                pass

        return configs

    def _parse_js_object(self, raw: str) -> Optional[Dict]:
        """Parse a JavaScript object literal to Python dict."""
        # Clean up common JS patterns that aren't valid JSON
        cleaned = raw.strip()

        # Remove trailing commas before } or ]
        cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)

        # Replace single quotes with double quotes (simple approach)
        # This is imperfect but works for most JWPlayer configs
        cleaned = re.sub(r"(?<![\\])'", '"', cleaned)

        # Remove JS comments
        cleaned = re.sub(r'//[^\n]*', '', cleaned)
        cleaned = re.sub(r'/\*.*?\*/', '', cleaned, flags=re.DOTALL)

        # Try to handle unquoted keys: key: "value" -> "key": "value"
        cleaned = re.sub(r'(\{|,)\s*(\w+)\s*:', r'\1"\2":', cleaned)

        # Fix double-quoted keys that got re-quoted
        cleaned = re.sub(r'""(\w+)""', r'"\1"', cleaned)

        try:
            return json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            pass

        # More aggressive cleanup attempt
        try:
            # Extract just the essential parts
            cleaned2 = re.sub(r'[a-zA-Z_]\w*\s*\(.*?\)', '""', raw)
            cleaned2 = re.sub(r',\s*([}\]])', r'\1', cleaned2)
            cleaned2 = re.sub(r"(?<![\\])'", '"', cleaned2)
            cleaned2 = re.sub(r'(\{|,)\s*(\w+)\s*:', r'\1"\2":', cleaned2)
            cleaned2 = re.sub(r'""(\w+)""', r'"\1"', cleaned2)
            return json.loads(cleaned2)
        except (json.JSONDecodeError, ValueError):
            logger.debug(f"Failed to parse JS object: {raw[:200]}")
            return None

    def _is_valid_jw_config(self, config: Dict) -> bool:
        """Check if a parsed config looks like a valid JWPlayer config."""
        jw_keys = ['file', 'sources', 'playlist', 'mediaid', 'image',
                    'title', 'aspectratio', 'width', 'height', 'autostart',
                    'primary', 'hlshtml', 'androidhls']
        return any(key in config for key in jw_keys)

    def _process_configs(self, configs: List[Dict], url: str, html: str) -> MediaInfo:
        """Process JWPlayer configs into MediaInfo."""
        all_formats = []
        title = None
        thumbnail = None
        duration = None

        for config in configs:
            formats, meta = self._extract_from_config(config, url)
            all_formats.extend(formats)

            if not title and meta.get('title'):
                title = meta['title']
            if not thumbnail and meta.get('thumbnail'):
                thumbnail = meta['thumbnail']
            if not duration and meta.get('duration'):
                duration = meta['duration']

        if not all_formats:
            raise ExtractionError("JWPlayer config found but no playable sources extracted")

        # Deduplicate
        all_formats = self._deduplicate_formats(all_formats)
        all_formats.sort(key=lambda f: f.quality_score, reverse=True)

        # Get title from page if not in config
        if not title:
            from utils.parser import MetadataExtractor
            meta = MetadataExtractor(url).extract(html)
            title = meta.get('title') or self._extract_title_from_url(url)

        return MediaInfo(
            id=self._generate_id(url),
            title=title,
            url=url,
            formats=all_formats,
            media_type=MediaType.VIDEO,
            extractor=self.EXTRACTOR_NAME,
            thumbnail=thumbnail,
            duration=duration,
        )

    def _extract_from_config(self, config: Dict, base_url: str) -> tuple:
        """Extract formats and metadata from a single JW config."""
        formats = []
        meta = {}

        meta['title'] = config.get('title')
        meta['thumbnail'] = config.get('image')
        meta['duration'] = config.get('duration')

        # Direct file
        if 'file' in config:
            fmt = self._source_to_format(
                {'file': config['file'], 'label': config.get('label')},
                base_url, 0
            )
            if fmt:
                formats.append(fmt)

        # Sources array
        for idx, source in enumerate(config.get('sources', [])):
            fmt = self._source_to_format(source, base_url, idx)
            if fmt:
                formats.append(fmt)

        # Playlist
        playlist = config.get('playlist', [])
        if isinstance(playlist, list):
            for item in playlist:
                if isinstance(item, dict):
                    if 'file' in item:
                        fmt = self._source_to_format(item, base_url, len(formats))
                        if fmt:
                            formats.append(fmt)
                    for idx2, source in enumerate(item.get('sources', [])):
                        fmt = self._source_to_format(source, base_url, len(formats))
                        if fmt:
                            formats.append(fmt)
                    if not meta['title'] and item.get('title'):
                        meta['title'] = item['title']
                    if not meta['thumbnail'] and item.get('image'):
                        meta['thumbnail'] = item['image']
                    if not meta['duration'] and item.get('duration'):
                        meta['duration'] = int(item['duration'])
        elif isinstance(playlist, str):
            # Playlist is a URL (RSS/JSON feed)
            try:
                playlist_data = self._fetch_json(playlist)
                if isinstance(playlist_data, list):
                    for item in playlist_data:
                        if not isinstance(item, dict):
                            continue
                        for source in item.get('sources', []):
                            fmt = self._source_to_format(source, base_url, len(formats))
                            if fmt:
                                formats.append(fmt)
            except Exception:
                # Playlist URL itself might be a video
                fmt = self._source_to_format({'file': playlist}, base_url, len(formats))
                if fmt:
                    formats.append(fmt)

        return formats, meta

    def _source_to_format(self, source: Any, base_url: str, idx: int) -> Optional[StreamFormat]:
        """Convert a JWPlayer source dict to StreamFormat."""
        if isinstance(source, str):
            source = {'file': source}

        file_url = source.get('file') or source.get('src') or source.get('source')
        if not file_url:
            return None

        # Make absolute
        if not file_url.startswith(('http://', 'https://')):
            file_url = urljoin(base_url, file_url)

        # Skip non-video
        url_lower = file_url.lower()
        if any(ext in url_lower for ext in ['.vtt', '.srt', '.jpg', '.png', '.gif']):
            return None

        stream_type = self._detect_stream_type(file_url)

        # Determine extension
        ext = 'mp4'
        if '.webm' in url_lower:
            ext = 'webm'
        elif '.m4a' in url_lower or '.mp3' in url_lower:
            ext = 'mp3'

        # Parse quality from label
        label = source.get('label', '')
        width = source.get('width')
        height = source.get('height')

        if label and not height:
            height = self._parse_quality(label)
            if height and not width:
                width = int(height * 16 / 9)

        return StreamFormat(
            format_id=f"jw-{idx}",
            url=file_url,
            ext=ext,
            quality=label or None,
            width=width,
            height=height,
            stream_type=stream_type,
            is_video=True,
            is_audio=True,
            label=label or None,
            bitrate=source.get('bitrate'),
            headers={'Referer': base_url},
        )

    # ===== Fallback HTML Parsing =====

    def _extract_source_urls_from_html(self, html: str, base_url: str) -> List[StreamFormat]:
        """Find JWPlayer source URLs directly in HTML/JS."""
        formats = []
        seen = set()

        for pattern in self.SOURCE_URL_PATTERNS:
            for match in re.finditer(pattern, html, re.IGNORECASE):
                url = match.group(1)
                if url in seen:
                    continue
                seen.add(url)

                # Skip non-video
                if any(ext in url.lower() for ext in ['.vtt', '.srt', '.jpg', '.png', '.css', '.js']):
                    continue

                fmt = self._source_to_format(
                    {'file': url}, base_url, len(formats)
                )
                if fmt:
                    formats.append(fmt)

        return formats

    def _build_media_info(self, formats: List[StreamFormat], url: str, html: str) -> MediaInfo:
        """Build MediaInfo from found formats."""
        from utils.parser import MetadataExtractor
        meta = MetadataExtractor(url).extract(html)

        formats = self._deduplicate_formats(formats)
        formats.sort(key=lambda f: f.quality_score, reverse=True)

        return MediaInfo(
            id=self._generate_id(url),
            title=meta.get('title') or self._extract_title_from_url(url),
            url=url,
            formats=formats,
            media_type=MediaType.VIDEO,
            extractor=self.EXTRACTOR_NAME,
            thumbnail=meta.get('thumbnail'),
        )

    def _extract_title_from_url(self, url: str) -> str:
        parsed = urlparse(url)
        path_parts = parsed.path.strip('/').split('/')
        for part in reversed(path_parts):
            if part and not part.endswith(('.m3u8', '.mpd', '.js')):
                return unquote(part).replace('-', ' ').replace('_', ' ').title()
        return parsed.netloc

    def _deduplicate_formats(self, formats: List[StreamFormat]) -> List[StreamFormat]:
        """Remove duplicate formats by URL."""
        seen = set()
        unique = []
        for fmt in formats:
            if fmt.url not in seen:
                seen.add(fmt.url)
                unique.append(fmt)
        return unique
