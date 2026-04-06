"""
PubJav extractor - handles AJAX-loaded players with multiple servers.

Full flow:
1. Fetch page → extract filmId + server list (episode IDs)
2. POST /ajax/player for each server → get <video> or <iframe>
3. For <video>: extract direct MP4 URL
4. For <iframe>: follow redirect chain → land on VidHide/embed page
5. Unpack obfuscated JavaScript (eval packer) → extract m3u8/mp4 URL
6. Return all found formats
"""

import re
import json

from typing import List, Dict, Optional, Any
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import logging

from .base import ExtractorBase, register_extractor, ExtractionError
from models.media import MediaInfo, StreamFormat, MediaType, StreamType

logger = logging.getLogger(__name__)


def unpack_js(packed_code: str) -> Optional[str]:
    """
    Unpack JavaScript packed with Dean Edwards' packer.
    Format: eval(function(p,a,c,k,e,d){while(c--)...}('code',base,count,'words'.split('|')))
    """
    pattern = (
        r"eval\(function\(p,a,c,k,e,d\)\{.*?\}\("
        r"'(.*?)',"
        r"(\d+),"
        r"(\d+),"
        r"'(.*?)'\.split"
    )
    match = re.search(pattern, packed_code, re.DOTALL)
    if not match:
        return None

    p_code = match.group(1)
    base = int(match.group(2))
    count = int(match.group(3))
    words = match.group(4).split('|')

    def base_n(num: int, radix: int) -> str:
        chars = "0123456789abcdefghijklmnopqrstuvwxyz"
        if num < radix:
            return chars[num]
        return base_n(num // radix, radix) + chars[num % radix]

    # Build replacement dict
    replacements = {}
    for i in range(count - 1, -1, -1):
        key = base_n(i, base)
        if i < len(words) and words[i]:
            replacements[key] = words[i]

    # Apply replacements (longest keys first)
    result = p_code
    for key in sorted(replacements.keys(), key=len, reverse=True):
        result = re.sub(r'\b' + re.escape(key) + r'\b', replacements[key], result)

    return result


@register_extractor()
class PubJavExtractor(ExtractorBase):
    """
    Extractor for pubjav.com and similar sites.

    Supports:
    - JWPlayer loaded from jwpcdn.com
    - AJAX endpoint /ajax/player to load video per server
    - Multiple servers (FL, DD, US, PP, TB, SW, ST)
    - VidHide embeds with obfuscated JS (Dean Edwards packer)
    - HLS m3u8 streams from CDN
    """

    EXTRACTOR_NAME = "pubjav"
    EXTRACTOR_DESCRIPTION = "PubJav.com extractor (multi-server, VidHide, HLS)"

    URL_PATTERNS = [
        r'https?://(?:www\.)?pubjav\.com/play/.+',
        r'https?://(?:www\.)?pubjav\.com/watch/.+',
    ]

    AJAX_ENDPOINT = "/ajax/player"

    def __init__(self, session, config: Optional[Dict[str, Any]] = None):
        super().__init__(session, config)
        self._duration_hint: Optional[int] = None

    def _make_headers(self, referer: Optional[str] = None) -> Dict[str, str]:
        h = {
            'User-Agent': self.session.user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Encoding': 'gzip, deflate',
        }
        if referer:
            h['Referer'] = referer
        return h

    def _media_headers(self, referer: Optional[str]) -> Dict[str, str]:
        headers = {
            'User-Agent': self.session.user_agent,
            'Accept': '*/*',
        }
        if referer:
            headers['Referer'] = referer
            parsed = urlparse(referer)
            if parsed.scheme and parsed.netloc:
                headers['Origin'] = f"{parsed.scheme}://{parsed.netloc}"
        return headers

    def _get(self, url: str, referer: Optional[str] = None, follow_redirects: bool = True):
        return self.session.get(
            url,
            headers=self._make_headers(referer),
            allow_redirects=follow_redirects,
        )

    def _post_ajax(self, url: str, data: Dict, referer: Optional[str] = None) -> Dict:
        h = self._make_headers(referer)
        h['Accept'] = 'application/json, text/javascript, */*; q=0.01'
        h['X-Requested-With'] = 'XMLHttpRequest'
        h['Content-Type'] = 'application/x-www-form-urlencoded'
        if referer:
            parsed = urlparse(referer)
            if parsed.scheme and parsed.netloc:
                h['Origin'] = f"{parsed.scheme}://{parsed.netloc}"
        resp = self.session.post(
            url,
            data=data,
            headers=h,
        )
        try:
            return resp.json()
        except Exception as e:
            text = resp.text.strip()
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except Exception:
                    pass
            raise ExtractionError(f"Invalid AJAX response from PubJav: {e}")

    def extract(self, url: str) -> MediaInfo:
        """Extract video from PubJav page."""
        logger.info(f"PubJav extraction for: {url}")
        self._duration_hint = None

        resp = self._get(url)
        html = resp.text
        soup = BeautifulSoup(html, 'html.parser')

        title = self._get_title(soup)
        thumbnail = self._get_thumbnail(soup)
        film_id = self._get_film_id(html)
        self._duration_hint = self._get_duration(soup)

        if not film_id:
            raise ExtractionError("Could not find filmId on page")

        servers = self._get_servers(soup, film_id)
        base_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        formats = []

        logger.info(f"Found {len(servers)} server(s), collecting fallback sources...")

        # Try filmId request first (usually returns the primary iframe source)
        try:
            logger.info("Checking iframe player...")
            iframe_formats = self._fetch_filmid_player(base_url, url, film_id)
            formats.extend(iframe_formats)
            if iframe_formats:
                logger.info(f"Found {len(iframe_formats)} format(s) from iframe")
            else:
                logger.info("Iframe player returned no video (Cloudflare may be blocking)")
        except Exception as e:
            logger.warning(f"Iframe player failed: {str(e)[:60]}")
            logger.debug(f"filmId request failed: {e}")

        # Always collect server formats as fallbacks so the downloader can
        # automatically switch if the first CDN source times out.
        for server in servers:
            try:
                logger.info(f"Checking server {server['name']}...")
                server_formats = self._fetch_server(base_url, url, film_id, server)
                formats.extend(server_formats)
                if server_formats:
                    labels = [f.label or 'unknown' for f in server_formats]
                    logger.info(f"Server {server['name']}: {', '.join(labels)}")
            except Exception as e:
                logger.debug(f"Server {server['name']} failed: {e}")

        if not formats:
            raise ExtractionError(
                "No video URLs found. All servers may be returning previews only, "
                "or the embed player's JavaScript could not be decoded."
            )

        formats = self._deduplicate(formats)

        # Separate real formats from DMM previews
        real_formats = [f for f in formats if 'preview' not in (f.label or '').lower()]
        preview_formats = [f for f in formats if 'preview' in (f.label or '').lower()]

        if real_formats:
            # Prefer real formats (HLS streams etc.)
            real_formats.sort(key=lambda f: f.quality_score, reverse=True)
            formats = real_formats
            logger.info(f"Found {len(real_formats)} full video format(s)")
        elif preview_formats:
            # Only previews available - warn user
            formats = preview_formats
            logger.warning(f"Only preview/trailer found ({len(preview_formats)}). Full video may not be available.")
        else:
            formats.sort(key=lambda f: f.quality_score, reverse=True)

        return MediaInfo(
            id=str(film_id),
            title=title,
            url=url,
            formats=formats,
            media_type=MediaType.VIDEO,
            extractor=self.EXTRACTOR_NAME,
            thumbnail=thumbnail,
            duration=self._duration_hint,
        )

    # ===== Page Parsing =====

    def _get_title(self, soup: BeautifulSoup) -> str:
        h1 = soup.find('h1')
        if h1:
            return h1.get_text().strip()
        og = soup.find('meta', property='og:title')
        if og:
            content = og.get('content')
            if isinstance(content, str) and content:
                return content.strip()
        return "Unknown Video"

    def _get_thumbnail(self, soup: BeautifulSoup) -> Optional[str]:
        img = soup.select_one('#clickfakeplayer img.cover')
        if img:
            src = img.get('src')
            if isinstance(src, str) and src:
                return src
        og = soup.find('meta', property='og:image')
        if og:
            content = og.get('content')
            if isinstance(content, str) and content:
                return content
        return None

    def _get_duration(self, soup: BeautifulSoup) -> Optional[int]:
        text = soup.get_text(" ", strip=True)
        match = re.search(r'Runtime:\s*(\d+)\s*minute', text, re.IGNORECASE)
        if match:
            return int(match.group(1)) * 60
        return None

    def _get_film_id(self, html: str) -> Optional[str]:
        match = re.search(r'filmId\s*=\s*(\d+)', html)
        if match:
            return match.group(1)
        match = re.search(r'data-source="(\d+)"', html)
        if match:
            return match.group(1)
        return None

    def _get_servers(self, soup: BeautifulSoup, film_id: str) -> List[Dict]:
        servers = []
        for item in soup.select('.switch-source'):
            source = item.get('data-source')
            if not isinstance(source, str) or not source:
                source = film_id

            server = {
                'name': item.get_text().strip(),
                'source': source,
            }
            data_id = item.get('data-id')
            data_episode = item.get('data-episode')

            if isinstance(data_id, str) and data_id:
                server['episode_id'] = data_id
            elif isinstance(data_episode, str) and data_episode:
                server['episode_id'] = data_episode
            else:
                continue
            servers.append(server)
        return servers

    # ===== AJAX Player =====

    def _fetch_filmid_player(self, base_url: str, page_url: str, film_id: str) -> List[StreamFormat]:
        """Fetch using filmId (returns iframe with full video)."""
        data = self._post_ajax(base_url + self.AJAX_ENDPOINT, {'filmId': film_id}, page_url)
        player_html = data.get('player', '')
        if not player_html:
            return []

        soup = BeautifulSoup(player_html, 'html.parser')
        iframe = soup.find('iframe')
        if iframe:
            iframe_src = iframe.get('src')
            if isinstance(iframe_src, str) and iframe_src:
                return self._follow_iframe(iframe_src, page_url, source_label="Iframe primary")
        return []

    def _fetch_server(self, base_url: str, page_url: str, film_id: str, server: Dict) -> List[StreamFormat]:
        """Fetch from a specific server."""
        try:
            data = self._post_ajax(
                base_url + self.AJAX_ENDPOINT,
                {'id': server['episode_id'], 'source': server['source']},
                page_url
            )
        except Exception as e:
            return []

        player_html = data.get('player', '')
        if not player_html:
            return []

        server_name = re.sub(r'[^\w\s]', '', server['name']).strip() or 'fallback'
        soup = BeautifulSoup(player_html, 'html.parser')
        formats = []

        video = soup.find('video')
        if video:
            formats.extend(self._extract_from_video_tag(video, server_name, page_url))

        iframe = soup.find('iframe')
        if iframe:
            iframe_src = iframe.get('src')
            if isinstance(iframe_src, str) and iframe_src:
                iframe_formats = self._follow_iframe(
                    iframe_src,
                    page_url,
                    source_label=f"Server {server_name}",
                )
            else:
                iframe_formats = []
            formats.extend(iframe_formats)

        return formats

    @staticmethod
    def _source_slug(source_label: str) -> str:
        slug = re.sub(r'[^a-z0-9]+', '-', source_label.lower()).strip('-')
        return slug or 'source'

    def _extract_duration_from_text(self, text: str) -> Optional[int]:
        match = re.search(r'duration\s*[:=]\s*["\']?(\d+(?:\.\d+)?)', text, re.IGNORECASE)
        if match:
            return int(float(match.group(1)))
        return None

    def _normalize_media_url(self, raw_url: str, referer: str) -> Optional[str]:
        if not isinstance(raw_url, str):
            return None

        url = raw_url.strip().strip('"\'')
        if not url or url in {'null', 'undefined'}:
            return None

        url = url.replace('\\/', '/').replace('&amp;', '&')
        lower_url = url.lower()

        if url.startswith('//'):
            return 'https:' + url
        if url.startswith(('http://', 'https://')):
            return url
        if url.startswith(('/', './', '../')) or url.startswith('stream/'):
            return urljoin(referer, url)
        if any(token in lower_url for token in ('.m3u8', '.mp4', 'master.txt')):
            return urljoin(referer, url)
        return None

    def _extract_named_links(self, text: str, referer: str) -> Dict[str, str]:
        named_links: Dict[str, str] = {}
        for key, raw_url in re.findall(r'["\']([A-Za-z0-9_]+)["\']\s*:\s*["\']([^"\']+)["\']', text):
            normalized = self._normalize_media_url(raw_url, referer)
            if normalized:
                named_links[key] = normalized
        return named_links

    def _expand_source_expression(
        self,
        expression: str,
        named_links: Dict[str, str],
        referer: str,
    ) -> List[str]:
        urls: List[str] = []
        for part in expression.split('||'):
            token = part.strip().strip('()[]{}')
            if not token:
                continue

            if token.startswith('links.'):
                candidate = named_links.get(token.split('.', 1)[1])
            elif token in named_links:
                candidate = named_links.get(token)
            else:
                candidate = self._normalize_media_url(token, referer)

            if candidate:
                urls.append(candidate)
        return urls

    def _extract_candidate_urls(self, text: str, referer: str) -> List[str]:
        named_links = self._extract_named_links(text, referer)
        candidates: List[str] = []
        seen = set()

        def add_candidate(raw_url: str) -> None:
            normalized = self._normalize_media_url(raw_url, referer)
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            candidates.append(normalized)

        for match in re.finditer(r'file\s*:\s*([^,\]}]+)', text):
            expression = match.group(1).strip()
            expanded = self._expand_source_expression(expression, named_links, referer)
            if expanded:
                for url in expanded:
                    add_candidate(url)
            else:
                add_candidate(expression)

        for pattern in (
            r'data-hash\s*=\s*["\']([^"\']+)["\']',
            r'urlPlay\s*=\s*["\']([^"\']+)["\']',
        ):
            for match in re.finditer(pattern, text, re.IGNORECASE):
                add_candidate(match.group(1))

        for url in named_links.values():
            add_candidate(url)

        absolute_pattern = r'(https?://[^\s"\'\\]+(?:\.m3u8|\.mp4|master\.txt)(?:\?[^\s"\'\\]*)?)'
        relative_pattern = r'((?:/|\./|\.\./)[^\s"\'\\]+(?:\.m3u8|\.mp4|master\.txt)(?:\?[^\s"\'\\]*)?)'

        for match in re.finditer(absolute_pattern, text, re.IGNORECASE):
            add_candidate(match.group(1))
        for match in re.finditer(relative_pattern, text, re.IGNORECASE):
            add_candidate(match.group(1))

        return candidates

    @staticmethod
    def _should_resolve_master_playlist(playlist_url: str, referer: str) -> bool:
        lower_url = playlist_url.lower()
        if '.m3u8' not in lower_url:
            return False

        playlist_host = urlparse(playlist_url).netloc.lower()
        referer_host = urlparse(referer).netloc.lower()
        if playlist_host == referer_host:
            return True

        return '/stream/' in urlparse(playlist_url).path

    def _resolve_master_playlist(
        self,
        playlist_url: str,
        referer: str,
        source_label: str,
    ) -> List[StreamFormat]:
        request_headers = self._make_headers(referer)
        request_headers['Accept'] = '*/*'
        try:
            response = self.session.get(
                playlist_url,
                headers=request_headers,
                allow_redirects=True,
            )
            response.raise_for_status()
        except Exception as e:
            logger.debug(f"Failed to resolve PubJav playlist {playlist_url}: {e}")
            return []

        content = response.text
        if '#EXT-X-STREAM-INF' not in content:
            return []

        formats: List[StreamFormat] = []
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        source_slug = self._source_slug(source_label)

        for idx, line in enumerate(lines):
            if not line.startswith('#EXT-X-STREAM-INF:'):
                continue

            width, height, bitrate = None, None, None
            resolution_match = re.search(r'RESOLUTION=(\d+)x(\d+)', line)
            if resolution_match:
                width = int(resolution_match.group(1))
                height = int(resolution_match.group(2))

            bandwidth_match = re.search(r'BANDWIDTH=(\d+)', line)
            if bandwidth_match:
                bitrate = int(bandwidth_match.group(1)) // 1000

            if idx + 1 >= len(lines):
                continue

            variant_url = lines[idx + 1]
            if not variant_url or variant_url.startswith('#'):
                continue

            normalized_variant_url = self._normalize_media_url(variant_url, response.url)
            if not normalized_variant_url:
                continue

            quality = f"{height}p" if height else None
            label = f"{source_label} HLS {quality}" if quality else f"{source_label} HLS"
            digest = self._generate_id(normalized_variant_url)[:6]

            formats.append(StreamFormat(
                format_id=f"pj-{source_slug}-hls-{height or 0}-{digest}",
                url=normalized_variant_url,
                ext='mp4',
                quality=quality,
                width=width,
                height=height,
                bitrate=bitrate,
                stream_type=StreamType.HLS,
                is_video=True,
                is_audio=True,
                label=label,
                headers=self._media_headers(referer),
            ))

        return formats

    def _extract_from_video_tag(self, video, server_name: str, referer: str) -> List[StreamFormat]:
        formats = []
        sources = []
        if video.get('src'):
            sources.append(video['src'])
        for source in video.find_all('source'):
            if source.get('src'):
                sources.append(source['src'])

        for idx, src in enumerate(sources):
            src = src.replace('\\/', '/')
            if not src.startswith('http'):
                continue
            digest = self._generate_id(src)[:6]
            formats.append(StreamFormat(
                format_id=f"pj-srv-{self._source_slug(server_name)}-{idx}-{digest}",
                url=src,
                ext='mp4',
                stream_type=StreamType.DIRECT,
                label=f"Server {server_name} (preview)",
                headers=self._media_headers(referer),
            ))
        return formats

    # ===== Iframe → VidHide → Unpack JS =====

    def _follow_iframe(self, iframe_url: str, referer: str, source_label: str) -> List[StreamFormat]:
        """Follow iframe through redirects, unpack JS, extract URLs."""
        if iframe_url.startswith('//'):
            iframe_url = 'https:' + iframe_url
        elif not iframe_url.startswith('http'):
            return []

        logger.debug(f"Following iframe: {iframe_url}")

        try:
            resp = self._get(iframe_url, referer=referer, follow_redirects=True)
            if resp.status_code != 200:
                return []
            embed_html = resp.text
            final_url = resp.url
        except Exception as e:
            logger.debug(f"Failed to fetch iframe: {e}")
            return []

        formats = []

        # Unpack all eval(function(p,a,c,k,e,d){...}) blocks
        unpacked = self._unpack_all_js(embed_html)
        if unpacked:
            logger.debug(f"Unpacked {len(unpacked)} chars of JS")
            formats.extend(self._extract_urls_from_js(unpacked, final_url, source_label))

        # Also try raw HTML (non-packed URLs)
        formats.extend(self._extract_urls_from_js(embed_html, final_url, source_label))

        return formats

    def _unpack_all_js(self, html: str) -> str:
        """Find and unpack all eval-packed JS blocks."""
        unpacked_parts = []
        pattern = r"eval\(function\(p,a,c,k,e,d\)\{.*?\}\('.*?'\.split\('\|'\)\)\)"
        for match in re.finditer(pattern, html, re.DOTALL):
            result = unpack_js(match.group(0))
            if result:
                unpacked_parts.append(result)
        return '\n'.join(unpacked_parts)

    def _extract_urls_from_js(self, js_code: str, referer: str, source_label: str) -> List[StreamFormat]:
        """Extract video URLs from JavaScript code."""
        if not self._duration_hint:
            self._duration_hint = self._extract_duration_from_text(js_code)

        formats: List[StreamFormat] = []
        seen = set()
        source_slug = self._source_slug(source_label)

        for candidate_url in self._extract_candidate_urls(js_code, referer):
            lower_url = candidate_url.lower()

            if '.mp4' in lower_url:
                if candidate_url in seen or 'dmm.co.jp' in lower_url or 'litevideo' in lower_url:
                    continue
                seen.add(candidate_url)
                digest = self._generate_id(candidate_url)[:6]
                formats.append(StreamFormat(
                    format_id=f"pj-{source_slug}-mp4-{digest}",
                    url=candidate_url,
                    ext='mp4',
                    stream_type=StreamType.DIRECT,
                    label=f"{source_label} MP4",
                    headers=self._media_headers(referer),
                ))
                continue

            if self._should_resolve_master_playlist(candidate_url, referer):
                resolved_formats = self._resolve_master_playlist(candidate_url, referer, source_label)
                if resolved_formats:
                    for resolved_format in resolved_formats:
                        if resolved_format.url in seen:
                            continue
                        seen.add(resolved_format.url)
                        formats.append(resolved_format)
                    continue

            if '.m3u8' in lower_url or 'master.txt' in lower_url:
                if candidate_url in seen:
                    continue
                seen.add(candidate_url)
                quality, height = None, None
                q_match = re.search(r'(\d{3,4})p', candidate_url)
                if q_match:
                    height = int(q_match.group(1))
                    quality = f"{height}p"

                digest = self._generate_id(candidate_url)[:6]
                fallback_name = None
                if '/hls3/' in lower_url or 'master.txt' in lower_url:
                    fallback_name = 'hls3'
                elif '/hls2/' in lower_url:
                    fallback_name = 'hls2'

                if quality:
                    label = f"{source_label} HLS {quality}"
                elif fallback_name:
                    label = f"{source_label} HLS ({fallback_name})"
                else:
                    label = f"{source_label} HLS"

                formats.append(StreamFormat(
                    format_id=f"pj-{source_slug}-hls-{height or 0}-{digest}",
                    url=candidate_url,
                    ext='mp4',
                    quality=quality,
                    height=height,
                    width=int(height * 16 / 9) if height else None,
                    stream_type=StreamType.HLS,
                    label=label,
                    headers=self._media_headers(referer),
                ))

        return formats

    def _deduplicate(self, formats: List[StreamFormat]) -> List[StreamFormat]:
        seen = set()
        unique = []
        for fmt in formats:
            if fmt.url not in seen:
                seen.add(fmt.url)
                unique.append(fmt)
        return unique
