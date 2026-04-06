"""
HTML and JavaScript parsing utilities with JWPlayer detection.
"""

import re
import json
from typing import List, Dict, Optional, Any
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import logging

logger = logging.getLogger(__name__)


class MediaURLExtractor:
    """Extracts media URLs from HTML and JavaScript content."""

    VIDEO_PATTERNS = [
        r'https?://[^\s<>"\']+\.(?:mp4|m4v|webm|mkv|avi|mov|flv)(?:\?[^\s<>"\']*)?',
        r'https?://[^\s<>"\']+\.m3u8(?:\?[^\s<>"\']*)?',
        r'https?://[^\s<>"\']+\.mpd(?:\?[^\s<>"\']*)?',
        r'https?://[^\s<>"\']*(?:video|stream|media|cdn|content)[^\s<>"\']*\.(?:mp4|m3u8|mpd)',
    ]

    JS_URL_PATTERNS = [
        r'["\']?(https?://[^"\'<>\s]+(?:\.mp4|\.m3u8|\.mpd|/playlist|/manifest)[^"\'<>\s]*)["\']?',
        r'source\s*[=:]\s*["\']([^"\']+)["\']',
        r'file\s*[=:]\s*["\']([^"\']+)["\']',
        r'src\s*[=:]\s*["\']([^"\']+\.(?:mp4|m3u8|mpd|webm)[^"\']*)["\']',
        r'url\s*[=:]\s*["\']([^"\']+\.(?:mp4|m3u8|mpd|webm)[^"\']*)["\']',
        r'video_url\s*[=:]\s*["\']([^"\']+)["\']',
        r'videoUrl\s*[=:]\s*["\']([^"\']+)["\']',
        r'streamUrl\s*[=:]\s*["\']([^"\']+)["\']',
        r'hlsUrl\s*[=:]\s*["\']([^"\']+)["\']',
        r'dashUrl\s*[=:]\s*["\']([^"\']+)["\']',
    ]

    JSON_PATTERNS = [
        r'JSON\.parse\(["\']({[^"\']+})["\']',
        r'var\s+\w+\s*=\s*({[^;]+});',
        r'window\.\w+\s*=\s*({[^;]+});',
        r'data-video-config=["\']({[^"\']+})["\']',
        r'data-player-config=["\']({[^"\']+})["\']',
    ]

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.parsed_base = urlparse(base_url)

    def extract_from_html(self, html: str) -> List[Dict[str, Any]]:
        """Extract media URLs from HTML content."""
        soup = BeautifulSoup(html, 'html.parser')
        found_urls = []

        found_urls.extend(self._extract_from_video_elements(soup))
        found_urls.extend(self._extract_from_iframes(soup))
        found_urls.extend(self._extract_from_links(soup))
        found_urls.extend(self._extract_from_meta_tags(soup))
        found_urls.extend(self._extract_from_scripts(soup))
        found_urls.extend(self._extract_from_data_attributes(soup))
        found_urls.extend(self._extract_with_regex(html))

        return self._deduplicate_urls(found_urls)

    def _extract_from_video_elements(self, soup: BeautifulSoup) -> List[Dict]:
        """Extract URLs from video/source elements."""
        urls = []
        for video in soup.find_all('video'):
            if video.get('src'):
                urls.append({
                    'url': self._make_absolute(video['src']),
                    'type': 'direct', 'source': 'video_element'
                })
            if video.get('poster'):
                urls.append({
                    'url': self._make_absolute(video['poster']),
                    'type': 'thumbnail', 'source': 'video_poster'
                })
            for source in video.find_all('source'):
                if source.get('src'):
                    url = self._make_absolute(source['src'])
                    urls.append({
                        'url': url,
                        'type': self._detect_type_from_url(url),
                        'source': 'source_element',
                        'mime_type': source.get('type', '')
                    })
        return urls

    def _extract_from_iframes(self, soup: BeautifulSoup) -> List[Dict]:
        """Extract embed URLs from iframes."""
        urls = []
        embed_domains = [
            'youtube.com', 'youtu.be', 'vimeo.com', 'dailymotion.com',
            'player.vimeo.com', 'youtube-nocookie.com', 'streamable.com',
            'cdn.jwplayer.com', 'jwplatform.com',
        ]
        for iframe in soup.find_all('iframe'):
            src = iframe.get('src') or iframe.get('data-src')
            if src:
                url = self._make_absolute(src)
                is_embed = any(domain in url for domain in embed_domains)
                urls.append({
                    'url': url,
                    'type': 'embed' if is_embed else 'iframe',
                    'source': 'iframe'
                })
        return urls

    def _extract_from_links(self, soup: BeautifulSoup) -> List[Dict]:
        """Extract video URLs from anchor elements."""
        urls = []
        exts = ['.mp4', '.m4v', '.webm', '.mkv', '.avi', '.mov', '.flv']
        for link in soup.find_all('a', href=True):
            href = link['href']
            if any(ext in href.lower() for ext in exts):
                urls.append({
                    'url': self._make_absolute(href),
                    'type': 'direct', 'source': 'link'
                })
        return urls

    def _extract_from_meta_tags(self, soup: BeautifulSoup) -> List[Dict]:
        """Extract from Open Graph and Twitter meta tags."""
        urls = []
        meta_selectors = [
            ('meta', {'property': 'og:video'}),
            ('meta', {'property': 'og:video:url'}),
            ('meta', {'property': 'og:video:secure_url'}),
            ('meta', {'attrs': {'name': 'twitter:player:stream'}}),
        ]
        for tag, attrs in meta_selectors:
            meta = soup.find(tag, **attrs) if 'attrs' not in attrs else soup.find(tag, **attrs)
            if meta and meta.get('content'):
                urls.append({
                    'url': meta['content'],
                    'type': self._detect_type_from_url(meta['content']),
                    'source': 'meta_tag'
                })

        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            urls.append({
                'url': og_image['content'],
                'type': 'thumbnail', 'source': 'og_image'
            })
        return urls

    def _extract_from_scripts(self, soup: BeautifulSoup) -> List[Dict]:
        """Extract URLs embedded in script tags."""
        urls = []
        for script in soup.find_all('script'):
            if script.string:
                for pattern in self.JS_URL_PATTERNS:
                    for match in re.findall(pattern, script.string, re.IGNORECASE):
                        url = self._make_absolute(match)
                        if self._is_valid_media_url(url):
                            urls.append({
                                'url': url,
                                'type': self._detect_type_from_url(url),
                                'source': 'script'
                            })
                urls.extend(self._extract_from_json_in_script(script.string))
        return urls

    def _extract_from_json_in_script(self, script_content: str) -> List[Dict]:
        """Extract URLs from JSON embedded in scripts."""
        urls = []
        for pattern in self.JSON_PATTERNS:
            for match in re.findall(pattern, script_content, re.DOTALL):
                try:
                    json_str = match.replace('\\"', '"').replace("\\'", "'")
                    data = json.loads(json_str)
                    urls.extend(self._extract_urls_from_json(data))
                except (json.JSONDecodeError, ValueError):
                    continue
        return urls

    def _extract_urls_from_json(self, data: Any, depth: int = 0) -> List[Dict]:
        """Recursively extract URLs from JSON data."""
        if depth > 10:
            return []
        urls = []
        if isinstance(data, dict):
            for key, value in data.items():
                if key.lower() in ['url', 'src', 'source', 'file', 'stream',
                                    'video_url', 'videourl', 'hlsurl', 'dashurl',
                                    'manifest', 'playlist']:
                    if isinstance(value, str) and self._is_valid_media_url(value):
                        urls.append({
                            'url': self._make_absolute(value),
                            'type': self._detect_type_from_url(value),
                            'source': f'json_{key}'
                        })
                urls.extend(self._extract_urls_from_json(value, depth + 1))
        elif isinstance(data, list):
            for item in data:
                urls.extend(self._extract_urls_from_json(item, depth + 1))
        elif isinstance(data, str) and self._is_valid_media_url(data):
            urls.append({
                'url': self._make_absolute(data),
                'type': self._detect_type_from_url(data),
                'source': 'json_value'
            })
        return urls

    def _extract_from_data_attributes(self, soup: BeautifulSoup) -> List[Dict]:
        """Extract URLs from data-* attributes."""
        urls = []
        data_attrs = [
            'data-src', 'data-video', 'data-video-src', 'data-video-url',
            'data-stream', 'data-hls', 'data-dash', 'data-file',
            'data-jw-config', 'data-jw-source',
        ]
        for attr in data_attrs:
            for element in soup.find_all(attrs={attr: True}):
                url = element[attr]
                if self._is_valid_media_url(url):
                    urls.append({
                        'url': self._make_absolute(url),
                        'type': self._detect_type_from_url(url),
                        'source': f'data_attr_{attr}'
                    })
        return urls

    def _extract_with_regex(self, html: str) -> List[Dict]:
        """Extract URLs using regex patterns."""
        urls = []
        for pattern in self.VIDEO_PATTERNS:
            for match in re.findall(pattern, html, re.IGNORECASE):
                if self._is_valid_media_url(match):
                    urls.append({
                        'url': match,
                        'type': self._detect_type_from_url(match),
                        'source': 'regex'
                    })
        return urls

    def _make_absolute(self, url: str) -> str:
        """Convert relative URL to absolute."""
        if url.startswith('//'):
            return f"{self.parsed_base.scheme}:{url}"
        return urljoin(self.base_url, url)

    def _detect_type_from_url(self, url: str) -> str:
        """Detect media type from URL."""
        url_lower = url.lower()
        if '.m3u8' in url_lower:
            return 'hls'
        elif '.mpd' in url_lower:
            return 'dash'
        elif any(ext in url_lower for ext in ['.mp4', '.m4v', '.webm', '.mkv']):
            return 'direct'
        elif any(ext in url_lower for ext in ['.mp3', '.m4a', '.aac', '.ogg']):
            return 'audio'
        return 'unknown'

    def _is_valid_media_url(self, url: str) -> bool:
        """Check if URL looks like a valid media URL."""
        if not url or len(url) < 10:
            return False
        lowered = url.lower()
        invalid_fragments = [
            '${', '{{', '}}', 'javascript:', 'undefined', 'null',
            'encodeuricomponent(', 'videourl', 'videoid', 'placeholder'
        ]
        if any(fragment in lowered for fragment in invalid_fragments):
            return False
        if not url.startswith(('http://', 'https://', '/', './')):
            return False
        media_indicators = [
            '.mp4', '.m3u8', '.mpd', '.webm', '.mkv', '.avi',
            '.mp3', '.m4a', '.aac', '.flv', '.mov',
            '/video/', '/stream/', '/media/', '/hls/', '/dash/',
            'manifest', 'playlist', 'index.m3u8'
        ]
        return any(ind in lowered for ind in media_indicators)

    def _deduplicate_urls(self, urls: List[Dict]) -> List[Dict]:
        """Remove duplicate URLs while preserving order."""
        seen = set()
        unique = []
        for item in urls:
            url = item['url']
            if url not in seen:
                seen.add(url)
                unique.append(item)
        return unique


class MetadataExtractor:
    """Extracts metadata (title, description, etc.) from HTML."""

    def __init__(self, base_url: str):
        self.base_url = base_url

    def extract(self, html: str) -> Dict[str, Any]:
        """Extract all available metadata."""
        soup = BeautifulSoup(html, 'html.parser')
        return {
            'title': self._extract_title(soup),
            'description': self._extract_description(soup),
            'thumbnail': self._extract_thumbnail(soup),
            'duration': self._extract_duration(soup),
            'upload_date': self._extract_date(soup),
            'uploader': self._extract_uploader(soup),
        }

    def _extract_title(self, soup: BeautifulSoup) -> Optional[str]:
        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            return og_title['content']
        twitter_title = soup.find('meta', attrs={'name': 'twitter:title'})
        if twitter_title and twitter_title.get('content'):
            return twitter_title['content']
        title_tag = soup.find('title')
        if title_tag:
            return title_tag.get_text().strip()
        return None

    def _extract_description(self, soup: BeautifulSoup) -> Optional[str]:
        og_desc = soup.find('meta', property='og:description')
        if og_desc and og_desc.get('content'):
            return og_desc['content']
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc and meta_desc.get('content'):
            return meta_desc['content']
        return None

    def _extract_thumbnail(self, soup: BeautifulSoup) -> Optional[str]:
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            return og_image['content']
        twitter_image = soup.find('meta', attrs={'name': 'twitter:image'})
        if twitter_image and twitter_image.get('content'):
            return twitter_image['content']
        return None

    def _extract_duration(self, soup: BeautifulSoup) -> Optional[int]:
        duration = soup.find('meta', attrs={'itemprop': 'duration'})
        if duration and duration.get('content'):
            return self._parse_duration(duration['content'])
        og_duration = soup.find('meta', property='video:duration')
        if og_duration and og_duration.get('content'):
            try:
                return int(og_duration['content'])
            except ValueError:
                pass
        return None

    def _extract_date(self, soup: BeautifulSoup) -> Optional[str]:
        date_meta = soup.find('meta', attrs={'itemprop': 'uploadDate'})
        if date_meta and date_meta.get('content'):
            return date_meta['content']
        og_date = soup.find('meta', property='article:published_time')
        if og_date and og_date.get('content'):
            return og_date['content']
        return None

    def _extract_uploader(self, soup: BeautifulSoup) -> Optional[str]:
        author = soup.find('meta', attrs={'name': 'author'})
        if author and author.get('content'):
            return author['content']
        og_author = soup.find('meta', property='article:author')
        if og_author and og_author.get('content'):
            return og_author['content']
        return None

    def _parse_duration(self, duration_str: str) -> Optional[int]:
        """Parse ISO 8601 duration to seconds."""
        match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
        if match:
            h = int(match.group(1) or 0)
            m = int(match.group(2) or 0)
            s = int(match.group(3) or 0)
            return h * 3600 + m * 60 + s
        try:
            return int(duration_str)
        except ValueError:
            return None
