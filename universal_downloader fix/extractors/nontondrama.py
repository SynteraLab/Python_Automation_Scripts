"""
NontonDrama extractor.

Handles nontondrama episode pages that embed playeriframe.sbs, then resolve
stream URLs via cloud.hownetwork.xyz API.
"""

import re
from typing import List, Dict, Optional, Any
from urllib.parse import urljoin, urlparse, parse_qs, quote
import logging

from bs4 import BeautifulSoup

from .base import ExtractorBase, register_extractor, ExtractionError
from models.media import MediaInfo, StreamFormat, MediaType, StreamType

logger = logging.getLogger(__name__)


@register_extractor()
class NontonDramaExtractor(ExtractorBase):
    """Extractor for nontondrama.my episode pages."""

    EXTRACTOR_NAME = "nontondrama"
    EXTRACTOR_DESCRIPTION = "NontonDrama extractor (playeriframe + cloud API)"

    URL_PATTERNS = [
        r"https?://(?:tv\d+\.)?nontondrama\.my/[\w\-+/]+",
        r"https?://(?:www\.)?nontondrama\.my/[\w\-+/]+",
    ]

    EXCLUDED_PATH_PREFIXES = (
        '/genre/',
        '/country/',
        '/year/',
        '/series/',
        '/latest',
        '/populer',
        '/most-commented',
        '/rating',
        '/release',
        '/privacy-policy',
        '/faq',
        '/dmca',
        '/cara-install-vpn',
        '/rekomendasi-film-pintar',
    )

    _MEDIA_URL_RE = re.compile(
        r"(https?://[^\s\"'<>\\]+\.(?:m3u8|mp4)(?:\?[^\s\"'<>]*)?)",
        re.IGNORECASE,
    )
    _PLAYER_IFRAME_RE = re.compile(
        r"https?://(?:www\.)?playeriframe\.sbs/iframe/[a-z0-9]+/[A-Za-z0-9_-]+",
        re.IGNORECASE,
    )

    _PLAYER_PRIORITY = {
        "p2p": 0,
        "turbovip": 1,
        "cast": 2,
        "hydrax": 3,
    }

    @classmethod
    def suitable(cls, url: str) -> bool:
        if not super().suitable(url):
            return False
        path = urlparse(url).path.lower()
        return not any(path == prefix or path.startswith(prefix + '/') for prefix in cls.EXCLUDED_PATH_PREFIXES)

    def extract(self, url: str) -> MediaInfo:
        logger.info(f"NontonDrama extraction for: {url}")

        html = self._fetch_page(url)
        title = self._extract_title(html, url)
        thumbnail = self._extract_thumbnail(html)

        player_urls = self._sort_player_urls(self._extract_player_urls(html, url))
        if not player_urls:
            episode_links = self._extract_episode_links(html, url)
            for episode_url in episode_links[:5]:
                try:
                    episode_html = self._fetch_page(episode_url)
                except Exception as e:
                    logger.debug(f"Failed to fetch fallback episode page {episode_url}: {e}")
                    continue

                episode_players = self._sort_player_urls(self._extract_player_urls(episode_html, episode_url))
                if not episode_players:
                    continue

                url = episode_url
                html = episode_html
                title = self._extract_title(episode_html, episode_url)
                thumbnail = self._extract_thumbnail(episode_html) or thumbnail
                player_urls = episode_players
                break

        if not player_urls:
            raise ExtractionError("No player iframe URLs found on page")

        formats: List[StreamFormat] = []
        for player_url in player_urls:
            formats.extend(self._resolve_player_url(player_url, page_url=url))
            if self._has_high_quality_stream(formats):
                break

        formats = self._deduplicate_formats(formats)
        formats.sort(key=lambda fmt: fmt.quality_score, reverse=True)

        if not formats:
            raise ExtractionError(
                "No playable streams found. Player may require cookies or the source may be unavailable."
            )

        return MediaInfo(
            id=self._generate_id(url),
            title=title,
            url=url,
            formats=formats,
            media_type=MediaType.VIDEO,
            extractor=self.EXTRACTOR_NAME,
            thumbnail=thumbnail,
        )

    def _extract_title(self, html: str, fallback_url: str) -> str:
        soup = BeautifulSoup(html, "html.parser")

        og_title = soup.find("meta", attrs={"property": "og:title"})
        if og_title and og_title.get("content"):
            return str(og_title["content"]).strip()

        title_tag = soup.find("title")
        if title_tag:
            text = title_tag.get_text(" ", strip=True)
            if text:
                return text

        return self._extract_title_from_url(fallback_url)

    @staticmethod
    def _extract_thumbnail(html: str) -> Optional[str]:
        soup = BeautifulSoup(html, "html.parser")
        og_image = soup.find("meta", attrs={"property": "og:image"})
        if og_image and og_image.get("content"):
            return str(og_image["content"])
        return None

    def _extract_player_urls(self, html: str, base_url: str) -> List[str]:
        soup = BeautifulSoup(html, "html.parser")
        candidates: List[str] = []

        for iframe in soup.find_all("iframe"):
            src = iframe.get("src") or iframe.get("data-src")
            if isinstance(src, str) and src.strip():
                candidates.append(urljoin(base_url, src.strip()))

        for match in self._PLAYER_IFRAME_RE.finditer(html):
            candidates.append(match.group(0))

        return self._deduplicate_urls(candidates)

    def _resolve_player_url(self, player_url: str, page_url: str) -> List[StreamFormat]:
        parsed = urlparse(player_url)
        host = parsed.netloc.lower()

        if "cloud.hownetwork.xyz" in host and parsed.path.startswith("/video.php"):
            return self._extract_hownetwork_stream(player_url, referrer=page_url)

        if "playeriframe.sbs" not in host:
            return []

        try:
            response = self.session.get(
                player_url,
                headers={"Referer": page_url},
                allow_redirects=True,
            )
        except Exception as e:
            logger.debug(f"Failed to fetch player iframe {player_url}: {e}")
            return []

        player_html = response.text
        formats: List[StreamFormat] = []

        soup = BeautifulSoup(player_html, "html.parser")
        nested_urls: List[str] = []
        for iframe in soup.find_all("iframe"):
            src = iframe.get("src") or iframe.get("data-src")
            if isinstance(src, str) and src.strip():
                nested_urls.append(urljoin(response.url, src.strip()))

        nested_urls = self._deduplicate_urls(nested_urls)

        # Fast path: resolve cloud API first and stop if stream is found.
        cloud_formats: List[StreamFormat] = []
        for nested_url in nested_urls:
            nested_parsed = urlparse(nested_url)
            if "cloud.hownetwork.xyz" in nested_parsed.netloc.lower() and nested_parsed.path.startswith("/video.php"):
                cloud_formats.extend(self._extract_hownetwork_stream(nested_url, referrer=response.url))

        if cloud_formats:
            return self._deduplicate_formats(cloud_formats)

        # Fallback path: try direct extraction and then other nested players.
        formats.extend(self._extract_direct_streams(player_html, base_url=response.url, referer=page_url))

        for nested_url in nested_urls:
            nested_parsed = urlparse(nested_url)
            if "cloud.hownetwork.xyz" in nested_parsed.netloc.lower() and nested_parsed.path.startswith("/video.php"):
                continue

            try:
                nested_resp = self.session.get(
                    nested_url,
                    headers={"Referer": response.url},
                    allow_redirects=True,
                )
            except Exception as e:
                logger.debug(f"Failed to fetch nested iframe {nested_url}: {e}")
                continue

            formats.extend(
                self._extract_direct_streams(
                    nested_resp.text,
                    base_url=nested_resp.url,
                    referer=response.url,
                )
            )

        return formats

    def _extract_episode_links(self, html: str, base_url: str) -> List[str]:
        soup = BeautifulSoup(html, "html.parser")
        candidates: List[str] = []

        for link in soup.find_all("a"):
            href = link.get("href")
            if not isinstance(href, str) or not href.strip():
                continue
            absolute = urljoin(base_url, href.strip())
            path = urlparse(absolute).path.lower()
            if 'episode' not in path:
                continue
            if not self.suitable(absolute):
                continue
            candidates.append(absolute)

        return self._deduplicate_urls(candidates)

    def _sort_player_urls(self, urls: List[str]) -> List[str]:
        def sort_key(indexed: tuple[int, str]) -> tuple[int, int]:
            idx, value = indexed
            path = urlparse(value).path.lower()
            priority = 99

            for key, rank in self._PLAYER_PRIORITY.items():
                if f"/iframe/{key}/" in path:
                    priority = rank
                    break

            return priority, idx

        ordered = sorted(enumerate(urls), key=sort_key)
        return [value for _, value in ordered]

    @staticmethod
    def _has_high_quality_stream(formats: List[StreamFormat]) -> bool:
        for fmt in formats:
            if (fmt.height or 0) >= 720:
                return True
        return False

    def _extract_hownetwork_stream(self, cloud_url: str, referrer: str) -> List[StreamFormat]:
        parsed = urlparse(cloud_url)
        host = parsed.netloc
        params = parse_qs(parsed.query)
        player_id = params.get("id", [""])[0].strip()
        if not player_id:
            return []

        origin = f"{parsed.scheme}://{host}"
        api_url = f"{origin}/api2.php?id={quote(player_id)}"

        try:
            response = self.session.post(
                api_url,
                data={
                    "r": referrer,
                    "d": host,
                },
                headers={
                    "Referer": cloud_url,
                    "Origin": origin,
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                },
            )
            payload = response.json()
        except Exception as e:
            logger.debug(f"Failed to load cloud API for {cloud_url}: {e}")
            return []

        formats: List[StreamFormat] = []
        stream_url = self._extract_stream_url_from_payload(payload)
        if stream_url:
            variants = self._expand_hls_variants(
                stream_url,
                referer=cloud_url,
                label_prefix="NontonDrama Stream",
            )
            if variants:
                formats.extend(variants)
            else:
                formats.append(
                    self._make_stream_format(
                        stream_url,
                        referer=cloud_url,
                        label="NontonDrama Stream",
                    )
                )

        return formats

    def _extract_stream_url_from_payload(self, payload: Any) -> Optional[str]:
        if isinstance(payload, dict):
            file_url = payload.get("file")
            if isinstance(file_url, str) and file_url.startswith(("http://", "https://")):
                return file_url

            sources = payload.get("sources")
            if isinstance(sources, list):
                for source in sources:
                    if not isinstance(source, dict):
                        continue
                    source_url = source.get("file") or source.get("src")
                    if isinstance(source_url, str) and source_url.startswith(("http://", "https://")):
                        return source_url

        return None

    def _extract_direct_streams(self, html: str, base_url: str, referer: str) -> List[StreamFormat]:
        formats: List[StreamFormat] = []
        seen_urls = set()

        for match in self._MEDIA_URL_RE.finditer(html):
            media_url = match.group(1).replace("\\/", "/")
            if media_url in seen_urls:
                continue
            seen_urls.add(media_url)

            if self._detect_stream_type(media_url) == StreamType.HLS:
                variants = self._expand_hls_variants(
                    media_url,
                    referer=referer,
                    label_prefix="Embedded Stream",
                )
                if variants:
                    formats.extend(variants)
                    continue

            formats.append(self._make_stream_format(media_url, referer=referer, label="Embedded Stream"))

        relative_matches = re.findall(r"['\"](/[^'\"]+\.(?:m3u8|mp4)[^'\"]*)['\"]", html, re.IGNORECASE)
        for relative_url in relative_matches:
            media_url = urljoin(base_url, relative_url)
            if media_url in seen_urls:
                continue
            seen_urls.add(media_url)

            if self._detect_stream_type(media_url) == StreamType.HLS:
                variants = self._expand_hls_variants(
                    media_url,
                    referer=referer,
                    label_prefix="Embedded Stream",
                )
                if variants:
                    formats.extend(variants)
                    continue

            formats.append(self._make_stream_format(media_url, referer=referer, label="Embedded Stream"))

        return formats

    def _expand_hls_variants(self, master_url: str, referer: str, label_prefix: str) -> List[StreamFormat]:
        """Expand HLS master manifest entries into quality-aware formats."""
        try:
            response = self.session.get(master_url, headers={"Referer": referer})
            playlist = response.text
        except Exception as e:
            logger.debug(f"Failed to fetch HLS playlist {master_url}: {e}")
            return []

        if "#EXT-X-STREAM-INF" not in playlist:
            return []

        lines = [line.strip() for line in playlist.splitlines() if line.strip()]
        variants: List[StreamFormat] = []
        seen_urls = set()

        for idx, line in enumerate(lines):
            if not line.startswith("#EXT-X-STREAM-INF:"):
                continue

            attrs = line.split(":", 1)[1] if ":" in line else ""
            resolution_match = re.search(r"RESOLUTION=(\d+)x(\d+)", attrs)
            bandwidth_match = re.search(r"BANDWIDTH=(\d+)", attrs)

            variant_url = None
            for next_line in lines[idx + 1:]:
                if next_line.startswith("#"):
                    continue
                variant_url = urljoin(master_url, next_line)
                break

            if not variant_url or variant_url in seen_urls:
                continue
            seen_urls.add(variant_url)

            width = None
            height = None
            quality = None
            if resolution_match:
                width = int(resolution_match.group(1))
                height = int(resolution_match.group(2))
                quality = f"{height}p"

            bitrate = None
            if bandwidth_match:
                try:
                    bitrate = int(int(bandwidth_match.group(1)) / 1000)
                except Exception:
                    bitrate = None

            digest = self._generate_id(variant_url)
            variants.append(
                StreamFormat(
                    format_id=f"nd-hls-{height or 0}-{digest[:6]}",
                    url=variant_url,
                    ext="mp4",
                    quality=quality,
                    width=width,
                    height=height,
                    bitrate=bitrate,
                    stream_type=StreamType.HLS,
                    is_video=True,
                    is_audio=True,
                    headers={"Referer": referer},
                    label=f"{label_prefix} {quality}" if quality else label_prefix,
                )
            )

        return variants

    def _make_stream_format(self, stream_url: str, referer: str, label: str) -> StreamFormat:
        stream_type = self._detect_stream_type(stream_url)
        quality, width, height = self._parse_quality_from_url(stream_url)

        ext = "mp4"
        if ".mp4" in stream_url.lower():
            ext = "mp4"

        digest = self._generate_id(stream_url)
        stream_key = "hls" if stream_type == StreamType.HLS else "direct"

        return StreamFormat(
            format_id=f"nd-{stream_key}-{height or 0}-{digest[:6]}",
            url=stream_url,
            ext=ext,
            quality=quality,
            width=width,
            height=height,
            stream_type=stream_type,
            is_video=True,
            is_audio=True,
            headers={
                "Referer": referer,
            },
            label=label if not quality else f"{label} {quality}",
        )

    @staticmethod
    def _parse_quality_from_url(url: str) -> tuple[Optional[str], Optional[int], Optional[int]]:
        lower_url = url.lower()

        match = re.search(r"(\d{3,4})p", lower_url)
        if match:
            height = int(match.group(1))
            if 144 <= height <= 4320:
                return f"{height}p", int(height * 16 / 9), height

        match = re.search(r"/(\d{3,4})\.m3u8(?:\?|$)", lower_url)
        if match:
            height = int(match.group(1))
            if 144 <= height <= 4320:
                return f"{height}p", int(height * 16 / 9), height

        return None, None, None

    def _extract_title_from_url(self, url: str) -> str:
        parsed = urlparse(url)
        slug = parsed.path.strip("/").split("/")[-1]
        if not slug:
            return parsed.netloc
        return slug.replace("-", " ").replace("_", " ").strip().title()

    def _deduplicate_urls(self, urls: List[str]) -> List[str]:
        seen = set()
        result: List[str] = []
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            result.append(url)
        return result

    def _deduplicate_formats(self, formats: List[StreamFormat]) -> List[StreamFormat]:
        seen_urls = set()
        result: List[StreamFormat] = []
        for fmt in formats:
            if fmt.url in seen_urls:
                continue
            seen_urls.add(fmt.url)
            result.append(fmt)
        return result
