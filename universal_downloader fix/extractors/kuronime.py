"""
Kuronime extractor.

Handles kuronime episode pages that load mirrors dynamically through
animeku.org API and iframe-based players.
"""

import re
import logging
import base64
import hashlib
import importlib
import json
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse

from .base import ExtractorBase, register_extractor, ExtractionError
from models.media import MediaInfo, StreamFormat, MediaType, StreamType

try:
    AES = importlib.import_module("Cryptodome.Cipher").AES
except Exception:  # pragma: no cover - optional dependency fallback
    AES = None

logger = logging.getLogger(__name__)


@register_extractor()
class KuronimeExtractor(ExtractorBase):
    """Extractor for kuronime episode pages."""

    EXTRACTOR_NAME = "kuronime"
    EXTRACTOR_DESCRIPTION = "Kuronime extractor (animeku mirrors + iframe players)"
    _ANIMEKU_DECRYPT_KEY = "3&!Z0M,VIZ;dZW=="

    URL_PATTERNS = [
        r"https?://(?:www\.)?kuronime\.(?:sbs|moe|vip|net)/nonton-[^\s]+/?$",
        r"https?://(?:www\.)?kuronime\.(?:sbs|moe|vip|net)/\?p=\d+",
        r"https?://(?:www\.)?kuronime\.(?:sbs|link)/anime/[A-Za-z0-9][\w\-]+/?$",
        r"https?://(?:www\.)?kuronime\.net/[A-Za-z0-9][\w\-]+/?$",
    ]

    _PORTAL_HOSTS = ("kuronime.sbs", "kuronime.link")
    _EPISODE_PATH_RE = re.compile(r"/nonton-[^/]+-episode-(\d+)/?$", re.IGNORECASE)
    _EXCLUDED_EXACT_PATHS = {
        "/",
        "/anime",
        "/movies",
        "/live-action",
        "/ongoing-anime",
        "/popular-anime",
        "/az-list",
        "/jadwal-rilis",
        "/bookmark",
        "/advertise-iklan",
        "/random",
        "/disclaimers",
        "/privacy-policy",
    }
    _EXCLUDED_PREFIXES = (
        "/genres/",
        "/season/",
        "/page/",
    )

    @classmethod
    def suitable(cls, url: str) -> bool:
        if not super().suitable(url):
            return False

        path = urlparse(url).path.rstrip("/").lower() or "/"
        if path in cls._EXCLUDED_EXACT_PATHS:
            return False
        return not any(path.startswith(prefix) for prefix in cls._EXCLUDED_PREFIXES)

    _MEDIA_URL_RE = re.compile(
        r"(https?://[^\s\"'\\]+/(?:[^\s\"'\\?#]+)\.(?:m3u8|mp4|mpd)(?:\?[^\s\"'\\]*)?)",
        re.IGNORECASE,
    )
    _MP4UPLOAD_DIRECT_RE = re.compile(r"https?://[^\s\"'<>]+/video\.mp4[^\s\"'<>]*", re.IGNORECASE)

    def extract(self, url: str) -> MediaInfo:
        logger.info(f"Kuronime extraction for: {url}")
        self._hls_manifest_cache: Dict[str, bool] = {}
        page_url, html = self._resolve_source_page(url)

        title = self._extract_title(html, page_url)
        thumbnail = self._extract_meta(html, "og:image")

        api_data = self._fetch_animeku_sources(page_url, html)
        mirror_formats = self._collect_mirror_formats(page_url, api_data)

        candidates = self._collect_candidate_urls(page_url, html, api_data=api_data)
        if not candidates:
            raise ExtractionError("No player URL candidates found on page")

        formats = mirror_formats + self._resolve_candidates(page_url, candidates)
        if not formats:
            raise ExtractionError(
                "No playable formats found. Try again with cookies: --cookies-from-browser chrome"
            )

        formats = self._deduplicate_formats(formats)
        formats.sort(key=lambda f: f.quality_score, reverse=True)

        return MediaInfo(
            id=self._generate_id(page_url),
            title=title,
            url=page_url,
            formats=formats,
            media_type=MediaType.VIDEO,
            extractor=self.EXTRACTOR_NAME,
            thumbnail=thumbnail,
        )

    def _resolve_source_page(self, url: str) -> tuple[str, str]:
        html = self._fetch_html(url)
        if self._looks_like_playable_page(html):
            return url, html

        episode_page = self._pick_episode_page(self._extract_episode_links(html, url), referer=url)
        if episode_page:
            return episode_page

        for series_url in self._candidate_series_pages(url, html):
            try:
                series_html = self._fetch_html(series_url, referer=url)
            except Exception as e:
                logger.debug(f"Failed to fetch Kuronime series page {series_url}: {e}")
                continue

            if self._looks_like_playable_page(series_html):
                return series_url, series_html

            episode_page = self._pick_episode_page(
                self._extract_episode_links(series_html, series_url),
                referer=series_url,
            )
            if episode_page:
                return episode_page

        return url, html

    def _looks_like_playable_page(self, html: str) -> bool:
        if re.search(r'var\s+_0xa100d42aa\s*=\s*"([^"]+)"', html):
            return True

        for candidate in self._extract_media_or_player_urls(html):
            normalized = self._normalize_candidate_url(candidate)
            if not normalized:
                continue

            lower = normalized.lower()
            if self._detect_stream_type(normalized) in (StreamType.HLS, StreamType.DASH):
                return True
            if lower.endswith(".mp4"):
                return True
            if any(token in lower for token in (
                "player.animeku.org/?data=",
                "blog.animeku.org/player2.php",
                "mp4upload.com/embed-",
                "mp4upload.com/",
                "iframedc",
            )):
                return True

        return False

    def _candidate_series_pages(self, url: str, html: str) -> List[str]:
        parsed = urlparse(url)
        slug = self._extract_series_slug(parsed.path)
        if not slug:
            return []

        hosts = self._extract_portal_hosts(html)
        current_host = parsed.netloc.lower()
        if current_host:
            hosts.insert(0, current_host)

        candidates: List[str] = []
        for host in hosts:
            normalized_host = host.lower()
            if not normalized_host:
                continue
            if normalized_host.endswith(("kuronime.net", "kuronime.sbs", "kuronime.link")):
                candidates.append(f"https://{normalized_host}/anime/{slug}/")

        return self._deduplicate_urls(candidates)

    def _extract_portal_hosts(self, html: str) -> List[str]:
        hosts = [str(host) for host in self._PORTAL_HOSTS]
        for match in re.finditer(r'https://(kuronime\.[A-Za-z0-9.-]+)', html, re.IGNORECASE):
            host = match.group(1).lower()
            if host not in hosts:
                hosts.append(host)
        return hosts

    def _extract_series_slug(self, path: str) -> Optional[str]:
        parts = [part for part in path.strip('/').split('/') if part]
        if not parts:
            return None
        if parts[0] == 'anime' and len(parts) > 1:
            return parts[1]
        if parts[0].startswith('nonton-'):
            slug = re.sub(r'^nonton-', '', parts[0], flags=re.IGNORECASE)
            slug = re.sub(r'-episode-\d+$', '', slug, flags=re.IGNORECASE)
            return slug or None
        return parts[-1]

    def _extract_episode_links(self, html: str, base_url: str) -> List[str]:
        links: List[str] = []
        for match in re.finditer(r'href=["\']([^"\']+/nonton-[^"\']+)["\']', html, re.IGNORECASE):
            links.append(urljoin(base_url, match.group(1).strip()))
        return self._deduplicate_urls(links)

    def _pick_episode_page(self, episode_urls: List[str], referer: str) -> Optional[tuple[str, str]]:
        ordered = sorted(episode_urls, key=self._episode_sort_key, reverse=True)
        for episode_url in ordered[:5]:
            try:
                episode_html = self._fetch_html(episode_url, referer=referer)
            except Exception as e:
                logger.debug(f"Failed to fetch Kuronime episode page {episode_url}: {e}")
                continue
            if self._looks_like_playable_page(episode_html):
                return episode_url, episode_html

        if ordered:
            fallback_url = ordered[0]
            try:
                return fallback_url, self._fetch_html(fallback_url, referer=referer)
            except Exception as e:
                logger.debug(f"Failed to fetch Kuronime fallback episode page {fallback_url}: {e}")
        return None

    def _episode_sort_key(self, url: str) -> tuple[int, str]:
        match = self._EPISODE_PATH_RE.search(urlparse(url).path)
        episode = int(match.group(1)) if match else 0
        return episode, url

    def _collect_candidate_urls(self, page_url: str, html: str, api_data: Optional[Dict] = None) -> List[str]:
        candidates: List[str] = []

        og_video = self._extract_meta(html, "og:video:url")
        if og_video:
            candidates.append(og_video)

        for pattern in [
            r"id=[\"']iframedc[\"'][^>]*data-src=[\"']([^\"']+)",
            r"id=[\"']iframedc[\"'][^>]*src=[\"']([^\"']+)",
        ]:
            for m in re.finditer(pattern, html, re.IGNORECASE):
                src = m.group(1).strip()
                normalized = self._normalize_candidate_url(src)
                if normalized:
                    candidates.append(urljoin(page_url, normalized))

        for u in self._extract_media_or_player_urls(html):
            normalized = self._normalize_candidate_url(u)
            if normalized:
                candidates.append(urljoin(page_url, normalized))

        if api_data is None:
            api_data = self._fetch_animeku_sources(page_url, html)
        if api_data:
            src = api_data.get("src")
            if src:
                candidates.append(f"https://player.animeku.org/?data={src}")

                decrypted = self._decrypt_animeku_payload(src)
                if decrypted:
                    direct_src = decrypted.get("src")
                    if isinstance(direct_src, str):
                        candidates.append(direct_src)

            src_sd = api_data.get("src_sd")
            if src_sd:
                decrypted_sd = self._decrypt_animeku_payload(src_sd)
                if decrypted_sd:
                    direct_src_sd = decrypted_sd.get("src")
                    if isinstance(direct_src_sd, str):
                        candidates.append(direct_src_sd)

            mirror = api_data.get("mirror")
            if mirror:
                decrypted_mirror = self._decrypt_animeku_payload(mirror)
                if decrypted_mirror:
                    candidates.extend(self._extract_http_urls(decrypted_mirror))

            blog = api_data.get("blog")
            if blog:
                candidates.append(f"https://blog.animeku.org/player2.php?id={blog}")

        return self._deduplicate_urls(candidates)

    def _collect_mirror_formats(self, page_url: str, api_data: Optional[Dict]) -> List[StreamFormat]:
        """
        Build quality-aware direct formats from animeku mirror payload.

        This avoids selecting stale HLS URLs when mirror embeds still provide
        working 720p/1080p direct links (e.g. mp4upload).
        """
        if not api_data:
            return []

        mirror_payload = api_data.get("mirror")
        mirror_data = self._decrypt_animeku_payload(mirror_payload) if isinstance(mirror_payload, str) else None
        if not isinstance(mirror_data, dict):
            return []

        formats: List[StreamFormat] = []
        seen_urls: Set[str] = set()

        for section_key in ("embed", "download"):
            section = mirror_data.get(section_key)
            if not isinstance(section, dict):
                continue

            for quality_key, providers in section.items():
                if not isinstance(providers, dict):
                    continue

                quality_height = self._quality_from_key(quality_key)
                quality_label = f"{quality_height}p" if quality_height else None

                for provider_name, provider_url in providers.items():
                    if provider_name != "mp4upload" or not isinstance(provider_url, str):
                        continue

                    embed_url = self._normalize_mp4upload_embed(provider_url)
                    if not embed_url:
                        continue

                    direct_url = self._extract_mp4upload_direct(embed_url, page_url)
                    if not direct_url or direct_url in seen_urls:
                        continue

                    fmt = self._make_format(direct_url, embed_url)
                    if not fmt:
                        continue

                    if quality_height:
                        fmt.height = quality_height
                        fmt.width = self._width_for_height(quality_height)
                        fmt.quality = quality_label
                    fmt.label = f"{quality_label or 'unknown'} MP4Upload"

                    digest = hashlib.md5(direct_url.encode("utf-8")).hexdigest()[:6]
                    fmt.format_id = f"kr-mp4-{quality_height or 0}-{digest}"

                    formats.append(fmt)
                    seen_urls.add(direct_url)

        return formats

    def _normalize_mp4upload_embed(self, url: str) -> Optional[str]:
        if "mp4upload.com" not in url:
            return None

        if "/embed-" in url:
            return url

        path = urlparse(url).path.strip("/")
        if not path:
            return None
        code = path.split("/")[-1]
        if not code:
            return None
        return f"https://www.mp4upload.com/embed-{code}.html"

    def _extract_mp4upload_direct(self, embed_url: str, page_url: str) -> Optional[str]:
        try:
            html = self._fetch_html(embed_url, referer=page_url)
        except Exception as e:
            logger.debug(f"Failed to fetch mp4upload embed page {embed_url}: {e}")
            return None

        m = self._MP4UPLOAD_DIRECT_RE.search(html)
        return m.group(0) if m else None

    @staticmethod
    def _quality_from_key(quality_key: str) -> Optional[int]:
        m = re.search(r"v(\d{3,4})p", quality_key, re.IGNORECASE)
        if not m:
            return None
        return int(m.group(1))

    @staticmethod
    def _width_for_height(height: int) -> int:
        known = {360: 640, 480: 854, 720: 1280, 1080: 1920}
        return known.get(height, int(height * 16 / 9))

    def _decrypt_animeku_payload(self, payload_b64: str) -> Optional[Dict[str, Any]]:
        """
        Decrypt animeku API payloads (src/src_sd/mirror) used by Kuronime JS.
        Payload format: base64(JSON({ct, iv, s})) encrypted with CryptoJS AES.
        """
        if not payload_b64 or AES is None:
            return None

        try:
            wrapper_text = base64.b64decode(payload_b64).decode("utf-8")
            wrapper = json.loads(wrapper_text)
            if not isinstance(wrapper, dict):
                return None

            ct_b64 = wrapper.get("ct")
            salt_hex = wrapper.get("s")
            if not isinstance(ct_b64, str) or not isinstance(salt_hex, str):
                return None

            ciphertext = base64.b64decode(ct_b64)
            salt = bytes.fromhex(salt_hex)
            key, iv = self._evp_bytes_to_key(self._ANIMEKU_DECRYPT_KEY.encode("utf-8"), salt, 32, 16)
            plaintext = AES.new(key, AES.MODE_CBC, iv).decrypt(ciphertext)
            plaintext = self._pkcs7_unpad(plaintext)
            decoded = plaintext.decode("utf-8", errors="ignore")
            data = json.loads(decoded)
            return data if isinstance(data, dict) else None
        except Exception as e:
            logger.debug(f"Failed to decrypt animeku payload: {e}")
            return None

    @staticmethod
    def _evp_bytes_to_key(passphrase: bytes, salt: bytes, key_len: int, iv_len: int) -> tuple[bytes, bytes]:
        """OpenSSL EVP_BytesToKey (MD5) compatible key derivation used by CryptoJS."""
        out = b""
        prev = b""
        target_len = key_len + iv_len
        while len(out) < target_len:
            prev = hashlib.md5(prev + passphrase + salt).digest()
            out += prev
        return out[:key_len], out[key_len:target_len]

    @staticmethod
    def _pkcs7_unpad(data: bytes) -> bytes:
        if not data:
            return data
        pad = data[-1]
        if pad < 1 or pad > 16:
            return data
        if data[-pad:] != bytes([pad]) * pad:
            return data
        return data[:-pad]

    def _extract_http_urls(self, value: Any) -> List[str]:
        urls: List[str] = []

        if isinstance(value, dict):
            for v in value.values():
                urls.extend(self._extract_http_urls(v))
        elif isinstance(value, list):
            for item in value:
                urls.extend(self._extract_http_urls(item))
        elif isinstance(value, str):
            if value.startswith(("http://", "https://")):
                urls.append(value)

        return urls

    def _fetch_animeku_sources(self, page_url: str, html: str) -> Optional[Dict]:
        token_match = re.search(r'var\s+_0xa100d42aa\s*=\s*"([^"]+)"', html)
        if not token_match:
            return None

        token = token_match.group(1)
        headers = {
            "Referer": page_url,
            "Origin": f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
        }

        try:
            response = self.session.post(
                "https://animeku.org/api/v9/sources",
                json={"id": token},
                headers=headers,
            )
            data = response.json()
        except Exception as e:
            logger.debug(f"animeku API request failed: {e}")
            return None

        if not isinstance(data, dict) or data.get("status") != 200:
            return None
        return data

    def _resolve_candidates(self, source_page: str, candidates: List[str]) -> List[StreamFormat]:
        formats: List[StreamFormat] = []
        visited: Set[str] = set()
        queue = [(u, source_page, 0) for u in candidates]

        while queue:
            current_url, referer, depth = queue.pop(0)
            if current_url in visited or depth > 2:
                continue
            visited.add(current_url)

            st = self._detect_stream_type(current_url)
            if st in (StreamType.HLS, StreamType.DASH) or current_url.lower().endswith(".mp4"):
                fmt = self._make_format(current_url, referer)
                if fmt:
                    formats.append(fmt)
                continue

            try:
                page_html = self._fetch_html(current_url, referer=referer)
            except Exception as e:
                logger.debug(f"Failed to fetch candidate {current_url}: {e}")
                continue

            for media_url in self._extract_media_or_player_urls(page_html):
                absolute = urljoin(current_url, media_url)
                media_st = self._detect_stream_type(absolute)
                if media_st in (StreamType.HLS, StreamType.DASH) or absolute.lower().endswith(".mp4"):
                    fmt = self._make_format(absolute, current_url)
                    if fmt:
                        formats.append(fmt)
                elif absolute.startswith("http"):
                    queue.append((absolute, current_url, depth + 1))

            # Try JWPlayer extraction as fallback for this candidate page.
            try:
                from .jwplayer import JWPlayerExtractor
                if JWPlayerExtractor.can_handle(page_html):
                    jw = JWPlayerExtractor(self.session, self.config)
                    try:
                        media = jw.extract(current_url)
                        for fmt in media.formats:
                            if not fmt.headers:
                                fmt.headers = {"Referer": current_url}
                            formats.append(fmt)
                    finally:
                        try:
                            self._run_async(jw.request.close())
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"JWPlayer fallback failed for {current_url}: {e}")

        return formats

    def _extract_media_or_player_urls(self, html: str) -> List[str]:
        urls: List[str] = []

        for m in self._MEDIA_URL_RE.finditer(html):
            urls.append(m.group(1))

        iframe_patterns = [
            r"<iframe[^>]+src=[\"']([^\"']+)[\"']",
            r"data-src=[\"'](https?://[^\"']+)[\"']",
            r"player\.animeku\.org/\?data=[A-Za-z0-9_\-=]+",
            r"blog\.animeku\.org/player2\.php\?id=\d+",
            r"tune\.pk/player/embed_player\.php\?vid=\d+",
        ]

        for pattern in iframe_patterns:
            for m in re.finditer(pattern, html, re.IGNORECASE):
                value = m.group(1) if m.groups() else m.group(0)
                if value and not value.startswith("javascript:"):
                    if value.startswith("//"):
                        value = "https:" + value
                    urls.append(value)

        return self._deduplicate_urls(urls)

    def _normalize_candidate_url(self, value: str) -> Optional[str]:
        if not value:
            return None

        value = value.strip()
        if value.startswith(("about:", "javascript:")):
            return None

        # Domain/path without scheme, e.g. tune.pk/player/embed_player.php?vid=123
        if re.match(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}/", value):
            value = "https://" + value

        if any(ext in value.lower() for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"]):
            return None

        return value

    def _fetch_html(self, url: str, referer: Optional[str] = None) -> str:
        headers = {
            "Accept-Encoding": "gzip, deflate",
        }
        if referer:
            headers["Referer"] = referer
            headers["Origin"] = f"{urlparse(referer).scheme}://{urlparse(referer).netloc}"

        response = self.session.get(url, headers=headers)
        text = response.text

        # Some endpoints may still return garbled text from unsupported encodings.
        if "<html" not in text.lower() and "<!doctype" not in text.lower():
            try:
                text = response.content.decode("utf-8", errors="ignore")
            except Exception:
                pass

        return text

    def _make_format(self, media_url: str, referer: str) -> Optional[StreamFormat]:
        if not media_url.startswith(("http://", "https://")):
            return None

        media_url = self._normalize_candidate_url(media_url) or media_url
        stream_type = self._detect_stream_type(media_url)
        if stream_type == StreamType.HLS and not self._is_hls_manifest_live(media_url, referer):
            logger.debug(f"Skipping stale/unreachable HLS manifest: {media_url}")
            return None

        lower_url = media_url.lower()
        if lower_url.endswith(".mpd") or stream_type == StreamType.DASH:
            ext = "mp4"
        elif lower_url.endswith(".webm"):
            ext = "webm"
        elif lower_url.endswith(".m3u8"):
            ext = "mp4"
        else:
            ext = "mp4"

        quality = None
        width = None
        height = None
        q = re.search(r"(\d{3,4})p", lower_url)
        if q:
            height = int(q.group(1))
            width = int(height * 16 / 9)
            quality = f"{height}p"

        source_host = (urlparse(media_url).hostname or "kuronime").lower()
        if source_host.startswith("www."):
            source_host = source_host[4:]
        source_label = source_host.split(".")[0].replace("-", " ").title()
        digest = hashlib.md5(media_url.encode("utf-8")).hexdigest()[:8]
        quality_suffix = f"-{height}" if height else ""

        return StreamFormat(
            format_id=f"kr-{source_label.lower().replace(' ', '-')}{quality_suffix}-{digest}",
            url=media_url,
            ext=ext,
            quality=quality,
            width=width,
            height=height,
            stream_type=stream_type,
            is_video=True,
            is_audio=True,
            label=f"{source_label} {quality}".strip() if quality else source_label,
            headers={"Referer": referer},
        )

    def _is_hls_manifest_live(self, manifest_url: str, referer: str) -> bool:
        cached = self._hls_manifest_cache.get(manifest_url)
        if cached is not None:
            return cached

        headers = {
            "Accept": "application/vnd.apple.mpegurl, application/x-mpegURL, text/plain, */*",
            "Accept-Encoding": "gzip, deflate",
            "Referer": referer,
        }
        if referer.startswith(("http://", "https://")):
            parsed = urlparse(referer)
            headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"

        try:
            response = self.session.get(manifest_url, headers=headers)
            body = response.text.strip()
        except Exception as e:
            logger.debug(f"HLS manifest check failed for {manifest_url}: {e}")
            self._hls_manifest_cache[manifest_url] = False
            return False

        body_head = body[:1000].lower()
        is_m3u8 = "#extm3u" in body_head and ("#ext-x-stream-inf" in body_head or "#extinf" in body_head)
        if not is_m3u8 or "404 not found" in body_head or "<html" in body_head:
            self._hls_manifest_cache[manifest_url] = False
            return False

        self._hls_manifest_cache[manifest_url] = True
        return True

    def _extract_title(self, html: str, fallback_url: str) -> str:
        title = self._extract_meta(html, "og:title")
        if title:
            return re.sub(r"\s*-\s*Kuronime\s*$", "", title).strip()

        m = re.search(r"<h1[^>]*>([^<]+)</h1>", html, re.IGNORECASE)
        if m:
            return m.group(1).strip()

        parsed = urlparse(fallback_url)
        return parsed.path.strip("/") or parsed.netloc

    def _extract_meta(self, html: str, prop: str) -> Optional[str]:
        pattern = rf'<meta[^>]+(?:property|name)=[\"\']{re.escape(prop)}[\"\'][^>]+content=[\"\']([^\"\']+)'
        m = re.search(pattern, html, re.IGNORECASE)
        return m.group(1).strip() if m else None

    def _deduplicate_urls(self, urls: List[str]) -> List[str]:
        seen = set()
        out = []
        for u in urls:
            if not u:
                continue
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out

    def _deduplicate_formats(self, formats: List[StreamFormat]) -> List[StreamFormat]:
        seen = set()
        out = []
        for fmt in formats:
            if fmt.url in seen:
                continue
            seen.add(fmt.url)
            out.append(fmt)
        return out
