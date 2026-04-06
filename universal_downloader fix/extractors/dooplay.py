# pyright: reportMissingImports=false, reportOptionalMemberAccess=false
"""
DooPlay/IDLIX extractor.

Handles pages that load players through DooPlay AJAX endpoints and encrypted
embed payloads.
"""

import base64
import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .base import ExtractorBase, register_extractor, ExtractionError
from models.media import MediaInfo, StreamFormat, MediaType, StreamType
from utils.parser import MediaURLExtractor, MetadataExtractor

try:
    from curl_cffi import requests as curl_requests
    CURL_CFFI_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    curl_requests = None
    CURL_CFFI_AVAILABLE = False

try:
    from Cryptodome.Cipher import AES
except Exception:  # pragma: no cover - optional dependency fallback
    AES = None

logger = logging.getLogger(__name__)


@register_extractor()
class DooplayExtractor(ExtractorBase):
    """Extractor for DooPlay-powered IDLIX pages."""

    EXTRACTOR_NAME = "dooplay"
    EXTRACTOR_DESCRIPTION = "DooPlay/IDLIX extractor (AJAX player + encrypted embed)"

    URL_PATTERNS = [
        r"https?://(?:tv\d+\.)?idlixku\.com/[\w\-./?%=&+#]+",
        r"https?://(?:www\.)?idlixku\.com/[\w\-./?%=&+#]+",
    ]

    _TITLE_SUFFIX_RE = re.compile(r"\s*-\s*IDLIX\s*$", re.IGNORECASE)
    _AJAX_CONFIG_RE = re.compile(r"var\s+dtAjax\s*=\s*(\{.*?\})\s*;", re.DOTALL)
    _DIRECT_MEDIA_RE = re.compile(
        r"(https?://[^\s\"'<>\\]+\.(?:m3u8|mp4|mpd|webm)(?:\?[^\s\"'<>]*)?)",
        re.IGNORECASE,
    )

    @classmethod
    def can_handle(cls, html: str) -> bool:
        html_lower = html.lower()
        markers = [
            "dooplay_player_option",
            "dooplay-ajax-counter",
            "player_api",
        ]
        return all(marker in html_lower for marker in markers)

    def extract(self, url: str) -> MediaInfo:
        logger.info(f"DooPlay extraction for: {url}")

        self._visited_urls: Set[str] = set()
        self._impersonated_session = None
        html = self._fetch_html(url)

        if not self.can_handle(html):
            raise ExtractionError("No DooPlay player markers found on page")

        metadata = MetadataExtractor(url).extract(html)
        title = self._extract_title(html, url, metadata)
        thumbnail = metadata.get("thumbnail") or self._extract_thumbnail(html)

        options = self._extract_player_options(html, url)
        if not options:
            raise ExtractionError("No DooPlay player options found on page")

        ajax_config = self._extract_ajax_config(html, url)
        formats: List[StreamFormat] = []
        errors: List[str] = []

        for option in options:
            try:
                formats.extend(self._extract_option_formats(url, option, ajax_config))
            except Exception as e:
                logger.debug(
                    "DooPlay option failed for %s (nume=%s): %s",
                    option.get("label") or option.get("nume"),
                    option.get("nume"),
                    e,
                )
                errors.append(str(e))

        formats = self._deduplicate_formats(formats)
        formats.sort(key=lambda fmt: fmt.quality_score, reverse=True)

        if formats:
            return MediaInfo(
                id=self._generate_id(url),
                title=title,
                url=url,
                formats=formats,
                media_type=MediaType.VIDEO,
                extractor=self.EXTRACTOR_NAME,
                description=metadata.get("description"),
                thumbnail=thumbnail,
                duration=metadata.get("duration"),
                upload_date=metadata.get("upload_date"),
                uploader=metadata.get("uploader"),
            )

        browser_media = self._extract_with_browser(url, fallback_title=title, fallback_thumbnail=thumbnail)
        if browser_media and browser_media.formats:
            browser_media.extractor = self.EXTRACTOR_NAME
            browser_media.description = browser_media.description or metadata.get("description")
            browser_media.upload_date = browser_media.upload_date or metadata.get("upload_date")
            browser_media.uploader = browser_media.uploader or metadata.get("uploader")
            return browser_media

        raise ExtractionError(self._build_failure_hint(errors))

    def _extract_title(self, html: str, fallback_url: str, metadata: Dict[str, Any]) -> str:
        title = metadata.get("title")
        if title:
            return self._TITLE_SUFFIX_RE.sub("", title).strip()

        soup = BeautifulSoup(html, "html.parser")
        episode_title = soup.select_one("h1.epih1")
        if episode_title:
            text = episode_title.get_text(" ", strip=True)
            if text:
                return text

        return self._extract_title_from_url(fallback_url)

    def _extract_thumbnail(self, html: str) -> Optional[str]:
        soup = BeautifulSoup(html, "html.parser")
        og_image = soup.find("meta", attrs={"property": "og:image"})
        if og_image and og_image.get("content"):
            return str(og_image["content"]).strip()
        return None

    def _extract_player_options(self, html: str, page_url: str) -> List[Dict[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        post_meta = soup.select_one("#dooplay-ajax-counter")
        fallback_post = None
        if post_meta and post_meta.get("data-postid") is not None:
            fallback_post = str(post_meta.get("data-postid")).strip()
        fallback_type = self._guess_content_type(page_url, html)

        options: List[Dict[str, str]] = []
        seen = set()

        for node in soup.select("li.dooplay_player_option"):
            post_id = str(node.get("data-post") or fallback_post or "").strip()
            nume = str(node.get("data-nume") or "1").strip()
            content_type = str(node.get("data-type") or fallback_type).strip()

            title_node = node.select_one(".title")
            label = title_node.get_text(" ", strip=True) if title_node else node.get_text(" ", strip=True)
            label = label or f"Player {nume}"

            if not post_id:
                continue

            key = (post_id, content_type, nume)
            if key in seen:
                continue
            seen.add(key)

            options.append(
                {
                    "post": post_id,
                    "type": content_type,
                    "nume": nume,
                    "label": label,
                }
            )

        if not options and fallback_post:
            options.append(
                {
                    "post": fallback_post,
                    "type": fallback_type,
                    "nume": "1",
                    "label": "Player 1",
                }
            )

        return options

    def _guess_content_type(self, page_url: str, html: str) -> str:
        url_lower = page_url.lower()
        html_lower = html.lower()

        if "/episode/" in url_lower or 'itemtype="https://schema.org/episode"' in html_lower:
            return "tv"
        return "movie"

    def _extract_ajax_config(self, html: str, page_url: str) -> Dict[str, str]:
        default = {
            "url": urljoin(page_url, "/wp-admin/admin-ajax.php"),
            "player_api": urljoin(page_url, "/wp-json/dooplayer/v2/"),
            "play_method": "admin_ajax",
        }

        match = self._AJAX_CONFIG_RE.search(html)
        if not match:
            return default

        try:
            payload = json.loads(match.group(1))
        except Exception as e:
            logger.debug(f"Failed to parse dtAjax config: {e}")
            return default

        if isinstance(payload.get("url"), str):
            payload["url"] = urljoin(page_url, payload["url"])
        if isinstance(payload.get("player_api"), str):
            payload["player_api"] = urljoin(page_url, payload["player_api"])

        return {
            "url": payload.get("url") or default["url"],
            "player_api": payload.get("player_api") or default["player_api"],
            "play_method": payload.get("play_method") or default["play_method"],
        }

    def _extract_option_formats(
        self,
        page_url: str,
        option: Dict[str, str],
        ajax_config: Dict[str, str],
    ) -> List[StreamFormat]:
        errors: List[str] = []

        for method in self._candidate_methods(ajax_config.get("play_method")):
            try:
                payload = self._load_player_payload(page_url, option, ajax_config, method)
                formats = self._extract_formats_from_payload(
                    payload,
                    page_url=page_url,
                    label=option.get("label") or "DooPlay",
                )
                if formats:
                    return formats
            except Exception as e:
                errors.append(str(e))

        detail = errors[-1] if errors else "No formats resolved"
        raise ExtractionError(
            f"Could not resolve DooPlay source '{option.get('label') or option.get('nume')}': {detail}"
        )

    @staticmethod
    def _candidate_methods(preferred: Optional[str]) -> List[str]:
        if preferred == "wp_json":
            return ["wp_json", "admin_ajax"]
        if preferred == "admin_ajax":
            return ["admin_ajax", "wp_json"]
        return ["admin_ajax", "wp_json"]

    def _load_player_payload(
        self,
        page_url: str,
        option: Dict[str, str],
        ajax_config: Dict[str, str],
        method: str,
    ) -> Any:
        if method == "wp_json":
            endpoint = ajax_config.get("player_api") or urljoin(page_url, "/wp-json/dooplayer/v2/")
            endpoint = endpoint.rstrip("/")
            endpoint = f"{endpoint}/{option['post']}/{option['type']}/{option['nume']}"
            response = self._request_response(
                "GET",
                endpoint,
                headers=self._browser_headers(page_url, accept="application/json, text/plain, */*"),
                allow_redirects=True,
            )
        else:
            endpoint = ajax_config.get("url") or urljoin(page_url, "/wp-admin/admin-ajax.php")
            response = self._request_response(
                "POST",
                endpoint,
                headers=self._browser_headers(page_url, accept="*/*", ajax=True),
                data={
                    "action": "doo_player_ajax",
                    "post": option["post"],
                    "nume": option["nume"],
                    "type": option["type"],
                },
                allow_redirects=True,
            )

        if self._is_cloudflare_challenge(response):
            raise ExtractionError(self._cloudflare_hint())

        if response.status_code == 401:
            raise ExtractionError(self._cloudflare_hint())
        if response.status_code >= 400:
            raise ExtractionError(f"Player endpoint returned HTTP {response.status_code}")

        text = response.text.strip()
        if not text:
            raise ExtractionError("Player endpoint returned empty response")

        try:
            return response.json()
        except Exception:
            return text

    def _extract_formats_from_payload(self, payload: Any, page_url: str, label: str) -> List[StreamFormat]:
        if isinstance(payload, dict):
            embed_url = payload.get("embed_url")
            key = payload.get("key")
            if isinstance(embed_url, str) and isinstance(key, str):
                decoded = self._decrypt_embed_value(embed_url, key)
                return self._extract_formats_from_value(decoded, page_url, page_url, label, depth=0)

        return self._extract_formats_from_value(payload, page_url, page_url, label, depth=0)

    def _extract_formats_from_value(
        self,
        value: Any,
        base_url: str,
        referer: str,
        label: str,
        depth: int,
    ) -> List[StreamFormat]:
        if depth > 3 or value is None:
            return []

        formats: List[StreamFormat] = []

        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []

            if self._looks_like_url(text):
                absolute = self._absolute_url(base_url, text)
                return self._resolve_candidate_url(absolute, referer, label, depth)

            if self._looks_like_html(text):
                return self._extract_formats_from_html(text, base_url, referer, label, depth + 1)

            for match in self._DIRECT_MEDIA_RE.finditer(text):
                formats.extend(self._resolve_candidate_url(match.group(1), referer, label, depth))

            return self._deduplicate_formats(formats)

        if isinstance(value, dict):
            for nested in value.values():
                formats.extend(self._extract_formats_from_value(nested, base_url, referer, label, depth + 1))
            return self._deduplicate_formats(formats)

        if isinstance(value, list):
            for nested in value:
                formats.extend(self._extract_formats_from_value(nested, base_url, referer, label, depth + 1))
            return self._deduplicate_formats(formats)

        return []

    def _extract_formats_from_html(
        self,
        html: str,
        base_url: str,
        referer: str,
        label: str,
        depth: int,
    ) -> List[StreamFormat]:
        formats: List[StreamFormat] = []
        seen_candidates = set()
        url_extractor = MediaURLExtractor(base_url)

        try:
            found_urls = url_extractor.extract_from_html(html)
        except Exception as e:
            logger.debug(f"MediaURLExtractor failed for {base_url}: {e}")
            found_urls = []

        for item in found_urls:
            candidate = item.get("url")
            if not isinstance(candidate, str) or candidate in seen_candidates:
                continue
            seen_candidates.add(candidate)

            item_type = item.get("type")
            if item_type == "thumbnail":
                continue

            if item_type in {"direct", "hls", "dash", "audio"}:
                formats.extend(self._resolve_candidate_url(candidate, referer, label, depth))
            elif item_type in {"embed", "iframe", "unknown"}:
                formats.extend(self._resolve_candidate_url(candidate, base_url, label, depth + 1))

        for match in self._DIRECT_MEDIA_RE.finditer(html):
            candidate = match.group(1)
            if candidate not in seen_candidates:
                seen_candidates.add(candidate)
                formats.extend(self._resolve_candidate_url(candidate, referer, label, depth))

        formats.extend(self._extract_jwplayer_formats(html, base_url))
        return self._deduplicate_formats(formats)

    def _resolve_candidate_url(
        self,
        candidate_url: str,
        referer: str,
        label: str,
        depth: int,
    ) -> List[StreamFormat]:
        if depth > 3:
            return []

        candidate_url = self._absolute_url(referer, candidate_url)
        if candidate_url in self._visited_urls:
            return []

        if self._looks_like_non_media_asset(candidate_url):
            return []

        self._visited_urls.add(candidate_url)

        stream_type = self._detect_stream_type(candidate_url)
        if stream_type == StreamType.HLS:
            variants = self._expand_hls_variants(candidate_url, referer, label)
            if variants:
                return variants
            return [self._make_stream_format(candidate_url, referer, label)]

        if stream_type == StreamType.DASH or candidate_url.lower().endswith((".mp4", ".webm", ".mkv", ".m4v")):
            return [self._make_stream_format(candidate_url, referer, label)]

        jeniusplay_formats = self._extract_jeniusplay_formats(candidate_url, referer, label)
        if jeniusplay_formats:
            return jeniusplay_formats

        try:
            page_html = self._fetch_html(candidate_url, referer=referer)
        except Exception as e:
            logger.debug(f"Failed to fetch nested candidate {candidate_url}: {e}")
            return []

        return self._extract_formats_from_html(
            page_html,
            base_url=candidate_url,
            referer=candidate_url,
            label=label,
            depth=depth + 1,
        )

    def _extract_jeniusplay_formats(
        self,
        candidate_url: str,
        source_referer: str,
        label: str,
    ) -> List[StreamFormat]:
        parsed = urlparse(candidate_url)
        if not parsed.netloc.lower().endswith("jeniusplay.com"):
            return []

        match = re.search(r"/video/([A-Za-z0-9]+)", parsed.path)
        if not match:
            return []

        video_id = match.group(1)
        endpoint = f"{parsed.scheme or 'https'}://{parsed.netloc}/player/index.php?data={video_id}&do=getVideo"

        try:
            response = self._request_response(
                "POST",
                endpoint,
                headers=self._browser_headers(candidate_url, accept="*/*", ajax=True),
                data={"hash": video_id, "r": source_referer},
                allow_redirects=True,
            )
        except Exception as e:
            logger.debug(f"JeniusPlay source request failed for {candidate_url}: {e}")
            return []

        if response.status_code >= 400 or self._is_cloudflare_challenge(response):
            logger.debug(
                "JeniusPlay source request returned HTTP %s for %s",
                response.status_code,
                candidate_url,
            )
            return []

        try:
            payload = response.json()
        except Exception as e:
            logger.debug(f"JeniusPlay source JSON failed for {candidate_url}: {e}")
            return []

        if not isinstance(payload, dict):
            return []

        formats: List[StreamFormat] = []
        signed_hls = payload.get("securedLink") or payload.get("videoSource")
        if isinstance(signed_hls, str) and signed_hls:
            formats.extend(self._resolve_candidate_url(signed_hls, candidate_url, label, depth=0))

        decryption_key = payload.get("ck") if isinstance(payload.get("ck"), str) else None
        for source in payload.get("videoSources") or []:
            if not isinstance(source, dict):
                continue

            source_url = source.get("file")
            if isinstance(source_url, str) and decryption_key and source_url.startswith("{"):
                try:
                    source_url = self._decrypt_embed_value(source_url, decryption_key)
                except Exception as e:
                    logger.debug(f"JeniusPlay source decrypt failed for {candidate_url}: {e}")
                    continue

            if isinstance(source_url, str) and source_url:
                formats.extend(self._resolve_candidate_url(source_url, candidate_url, label, depth=0))

        return self._deduplicate_formats(formats)

    def _extract_jwplayer_formats(self, html: str, page_url: str) -> List[StreamFormat]:
        try:
            from .jwplayer import JWPlayerExtractor

            if not JWPlayerExtractor.can_handle(html):
                return []

            extractor = JWPlayerExtractor(self.session, self.config)
            configs = extractor._extract_setup_configs(html)
            if configs:
                media = extractor._process_configs(configs, page_url, html)
                return media.formats

            return extractor._extract_source_urls_from_html(html, page_url)
        except Exception as e:
            logger.debug(f"JWPlayer parsing failed for {page_url}: {e}")
            return []

    def _expand_hls_variants(self, master_url: str, referer: str, label: str) -> List[StreamFormat]:
        try:
            response = self._request_response(
                "GET",
                master_url,
                headers=self._browser_headers(
                    referer,
                    accept="application/vnd.apple.mpegurl, application/x-mpegURL, text/plain, */*",
                ),
                allow_redirects=True,
            )
        except Exception as e:
            logger.debug(f"Failed to fetch HLS manifest {master_url}: {e}")
            return []

        if response.status_code >= 400 or self._is_cloudflare_challenge(response):
            return []

        playlist = response.text
        if "#EXT-X-STREAM-INF" not in playlist:
            return []

        from .hls import HLSParser

        parser = HLSParser(master_url, playlist)
        variants = parser.parse_master_playlist()
        formats: List[StreamFormat] = []
        seen_urls = set()

        for variant in variants:
            variant_url = variant.get("url")
            if not variant_url or variant_url in seen_urls:
                continue
            seen_urls.add(variant_url)

            if variant.get("type") not in {"video", "audio", None}:
                continue

            quality = variant.get("quality")
            formats.append(
                StreamFormat(
                    format_id=f"dp-hls-{variant.get('height') or 0}-{self._generate_id(variant_url)[:6]}",
                    url=variant_url,
                    ext="mp4",
                    quality=quality,
                    width=variant.get("width"),
                    height=variant.get("height"),
                    fps=variant.get("fps"),
                    vcodec=variant.get("vcodec"),
                    acodec=variant.get("acodec"),
                    bitrate=variant.get("bitrate"),
                    stream_type=StreamType.HLS,
                    is_video=variant.get("is_video", True),
                    is_audio=variant.get("is_audio", True),
                    headers={"Referer": referer},
                    label=f"{label} {quality}" if quality else label,
                )
            )

        return formats

    def _make_stream_format(self, stream_url: str, referer: str, label: str) -> StreamFormat:
        stream_type = self._detect_stream_type(stream_url)
        quality, width, height = self._parse_quality_from_url(stream_url)
        stream_key = {
            StreamType.HLS: "hls",
            StreamType.DASH: "dash",
        }.get(stream_type, "direct")

        ext = "mp4"
        lower_url = stream_url.lower()
        if ".webm" in lower_url:
            ext = "webm"

        return StreamFormat(
            format_id=f"dp-{stream_key}-{height or 0}-{self._generate_id(stream_url)[:6]}",
            url=stream_url,
            ext=ext,
            quality=quality,
            width=width,
            height=height,
            stream_type=stream_type,
            is_video=True,
            is_audio=True,
            headers={"Referer": referer},
            label=f"{label} {quality}" if quality else label,
        )

    @staticmethod
    def _parse_quality_from_url(url: str) -> tuple[Optional[str], Optional[int], Optional[int]]:
        lower_url = url.lower()

        for pattern in [r"(\d{3,4})p", r"/(\d{3,4})\.m3u8(?:\?|$)"]:
            match = re.search(pattern, lower_url)
            if match:
                height = int(match.group(1))
                if 144 <= height <= 4320:
                    return f"{height}p", int(height * 16 / 9), height

        return None, None, None

    def _decrypt_embed_value(self, encrypted_value: str, password: str) -> Any:
        if not encrypted_value:
            return encrypted_value

        try:
            payload = json.loads(encrypted_value)
        except Exception:
            return encrypted_value

        actual_password = password
        mapping = payload.get("m")
        if isinstance(mapping, str):
            derived = self._decode_obfuscated_password(password, mapping)
            if derived:
                actual_password = derived

        decrypted_text = self._decrypt_cryptojs_json(payload, actual_password)
        try:
            return json.loads(decrypted_text)
        except Exception:
            return decrypted_text

    def _decrypt_cryptojs_json(self, payload: Dict[str, Any], password: str) -> str:
        if AES is None:
            raise ExtractionError("PyCryptodome is required for DooPlay decryption")

        ciphertext_b64 = payload.get("ct")
        if not isinstance(ciphertext_b64, str) or not ciphertext_b64:
            raise ExtractionError("Encrypted DooPlay payload is missing ciphertext")

        try:
            ciphertext = base64.b64decode(ciphertext_b64)
        except Exception as e:
            raise ExtractionError(f"Invalid DooPlay ciphertext: {e}") from e

        salt = b""
        salt_hex = payload.get("s")
        if isinstance(salt_hex, str) and salt_hex:
            salt = bytes.fromhex(salt_hex)

        iv = None
        iv_hex = payload.get("iv")
        if isinstance(iv_hex, str) and iv_hex:
            iv = bytes.fromhex(iv_hex)

        key, derived_iv = self._evp_bytes_to_key(password.encode("utf-8"), salt, 32, 16)
        cipher = AES.new(key, AES.MODE_CBC, iv or derived_iv)
        plaintext = cipher.decrypt(ciphertext)
        return self._pkcs7_unpad(plaintext).decode("utf-8", errors="ignore")

    @staticmethod
    def _decode_obfuscated_password(password: str, mapping: str) -> Optional[str]:
        try:
            parts = password.split("\\x")
            decoded = DooplayExtractor._safe_b64decode(mapping[::-1]).decode("utf-8", errors="ignore")
            rebuilt = []
            for token in decoded.split("|"):
                if not token.isdigit():
                    continue
                idx = int(token) + 1
                if 0 <= idx < len(parts):
                    rebuilt.append("\\x" + parts[idx])
            return "".join(rebuilt) or None
        except Exception:
            return None

    @staticmethod
    def _safe_b64decode(value: str) -> bytes:
        padding = (-len(value)) % 4
        return base64.b64decode(value + ("=" * padding))

    @staticmethod
    def _evp_bytes_to_key(passphrase: bytes, salt: bytes, key_len: int, iv_len: int) -> tuple[bytes, bytes]:
        output = b""
        prev = b""
        target_len = key_len + iv_len

        while len(output) < target_len:
            prev = hashlib.md5(prev + passphrase + salt).digest()
            output += prev

        return output[:key_len], output[key_len:target_len]

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

    def _extract_with_browser(
        self,
        url: str,
        fallback_title: str,
        fallback_thumbnail: Optional[str],
    ) -> Optional[MediaInfo]:
        try:
            from .advanced import AdvancedExtractor, PLAYWRIGHT_AVAILABLE
        except ImportError:
            return None

        if not PLAYWRIGHT_AVAILABLE:
            return None

        try:
            media = AdvancedExtractor(self.session, self.config).extract(url)
        except Exception as e:
            logger.debug(f"Browser fallback failed for {url}: {e}")
            return None

        if not media or not media.formats:
            return None

        if not media.title or media.title == urlparse(url).netloc:
            media.title = fallback_title
        if not media.thumbnail:
            media.thumbnail = fallback_thumbnail
        return media

    def _build_failure_hint(self, errors: List[str]) -> str:
        combined = " ".join(errors).lower()
        if any(token in combined for token in ["cloudflare", "just a moment", "logged out", "401", "403"]):
            return self._cloudflare_hint()

        detail = errors[-1] if errors else "No DooPlay streams found"
        return f"No playable DooPlay streams found: {detail}"

    @staticmethod
    def _cloudflare_hint() -> str:
        return (
            "DooPlay player request was blocked. Install project dependencies from "
            "`requirements.txt` (notably `curl_cffi`) or retry with `--use-browser`."
        )

    def _fetch_html(self, url: str, referer: Optional[str] = None) -> str:
        response = self._request_response(
            "GET",
            url,
            headers=self._browser_headers(referer),
            allow_redirects=True,
        )

        if self._is_cloudflare_challenge(response):
            raise ExtractionError(self._cloudflare_hint())
        if response.status_code >= 400:
            raise ExtractionError(f"HTTP {response.status_code} for {url}")

        return response.text

    def _browser_headers(
        self,
        referer: Optional[str],
        accept: Optional[str] = None,
        ajax: bool = False,
    ) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Accept-Encoding": "gzip, deflate",
        }
        if accept or ajax:
            headers["Accept"] = accept or "*/*"
        if referer:
            headers["Referer"] = referer
            parsed = urlparse(referer)
            if parsed.scheme and parsed.netloc:
                headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"
        if ajax:
            headers.update(
                {
                    "X-Requested-With": "XMLHttpRequest",
                    "Sec-Fetch-Site": "same-origin",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Dest": "empty",
                }
            )
        return headers

    def _request_response(self, method: str, url: str, headers: Optional[Dict[str, str]] = None, **kwargs):
        if CURL_CFFI_AVAILABLE:
            session = self._get_impersonated_session()
            if session is not None:
                merged_headers = {**session.headers, **(headers or {})}
                timeout = getattr(self.session, "timeout", 60)
                return session.request(method.upper(), url, headers=merged_headers, timeout=timeout, **kwargs)

        raw_session = getattr(self.session, "_session", None)
        if raw_session is None:
            if method.upper() == "GET":
                return self.session.get(url, headers=headers, **kwargs)
            return self.session.post(url, headers=headers, **kwargs)

        merged_headers = {**raw_session.headers, **(headers or {})}
        timeout = getattr(self.session, "timeout", 60)
        return raw_session.request(method.upper(), url, headers=merged_headers, timeout=timeout, **kwargs)

    def _get_impersonated_session(self):
        if not CURL_CFFI_AVAILABLE:
            return None
        existing_session = getattr(self, "_impersonated_session", None)
        if existing_session is not None:
            return existing_session

        session = curl_requests.Session(impersonate="chrome124", default_headers=True)

        proxy = getattr(self.session, "proxy", None)
        if proxy:
            session.proxies.update(proxy)

        raw_session = getattr(self.session, "_session", None)
        raw_cookies = getattr(raw_session, "cookies", None)
        if raw_cookies is not None:
            for cookie in raw_cookies:
                try:
                    session.cookies.set(
                        cookie.name,
                        cookie.value,
                        domain=cookie.domain,
                        path=cookie.path,
                    )
                except Exception:
                    continue

        self._impersonated_session = session
        return session

    def _close_impersonated_session(self) -> None:
        """Close the curl_cffi impersonated session if it was created."""
        session = getattr(self, "_impersonated_session", None)
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
            self._impersonated_session = None

    def __del__(self) -> None:
        self._close_impersonated_session()

    @staticmethod
    def _is_cloudflare_challenge(response) -> bool:
        body = (response.text or "")[:4000].lower()
        header = (response.headers.get("cf-mitigated") or "").lower()
        indicators = [
            "just a moment",
            "cf-browser-verification",
            "challenge-platform",
            "__cf_chl",
        ]
        return header == "challenge" or any(token in body for token in indicators)

    @staticmethod
    def _looks_like_url(value: str) -> bool:
        return value.startswith(("http://", "https://", "//", "/"))

    @staticmethod
    def _looks_like_html(value: str) -> bool:
        lower = value.lower()
        return any(token in lower for token in ["<iframe", "<video", "<source", "<script", "<div", "jwplayer("])

    @staticmethod
    def _looks_like_non_media_asset(url: str) -> bool:
        lower = url.lower()
        blocked = [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".css", ".js"]
        return any(lower.endswith(token) for token in blocked)

    @staticmethod
    def _absolute_url(base_url: str, value: str) -> str:
        if value.startswith("//"):
            return f"{urlparse(base_url).scheme}:{value}"
        return urljoin(base_url, value)

    @staticmethod
    def _extract_title_from_url(url: str) -> str:
        parsed = urlparse(url)
        slug = parsed.path.strip("/").split("/")[-1]
        if not slug:
            return parsed.netloc
        return slug.replace("-", " ").replace("_", " ").strip().title()

    @staticmethod
    def _deduplicate_formats(formats: List[StreamFormat]) -> List[StreamFormat]:
        seen_urls = set()
        result: List[StreamFormat] = []
        for fmt in formats:
            if fmt.url in seen_urls:
                continue
            seen_urls.add(fmt.url)
            result.append(fmt)
        return result
