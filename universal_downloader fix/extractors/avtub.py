"""
AVTub extractor.

Handles AVTub watch pages that embed YStream player URLs and resolves playable
stream links via YStream playback API.
"""

import difflib
import json
import logging
import re
import subprocess
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .base import ExtractorBase, register_extractor, ExtractionError
from models.media import MediaInfo, MediaType, StreamFormat, StreamType
from utils.parser import MediaURLExtractor, MetadataExtractor

logger = logging.getLogger(__name__)


@register_extractor()
class AvtubExtractor(ExtractorBase):
    """Extractor for AVTub watch pages and YStream embeds."""

    EXTRACTOR_NAME = "avtub"
    EXTRACTOR_DESCRIPTION = "AVTub extractor (watch pages + YStream playback API)"

    URL_PATTERNS = [
        r"https?://(?:www\.)?avtub\.(?:so|cx)/watch/[\w\-./?%=&+#]+",
    ]

    _WATCH_SLUG_RE = re.compile(r"/watch/([^/?#]+)", re.IGNORECASE)
    _YSTREAM_CODE_RE = re.compile(r"/(?:e|mwvgz)/([A-Za-z0-9]+)", re.IGNORECASE)

    def extract(self, url: str) -> MediaInfo:
        logger.info("AVTub extraction for: %s", url)

        page_url, html = self._resolve_watch_page(url)
        metadata = MetadataExtractor(page_url).extract(html)

        title = metadata.get("title") or self._extract_title_from_url(page_url)
        if " - AVTub" in title:
            title = title.replace(" - AVTub", "").strip()

        candidate_urls = self._collect_candidate_urls(page_url, html)
        formats: List[StreamFormat] = []

        for candidate in candidate_urls:
            try:
                formats.extend(self._extract_candidate_formats(candidate, referer=page_url))
            except Exception as e:
                logger.debug("Candidate extraction failed for %s: %s", candidate, e)

        formats = self._deduplicate_formats(formats)
        formats.sort(key=lambda f: f.quality_score, reverse=True)

        if not formats:
            raise ExtractionError("No playable streams found on AVTub page")

        return MediaInfo(
            id=self._generate_id(page_url),
            title=title,
            url=page_url,
            formats=formats,
            media_type=MediaType.VIDEO,
            extractor=self.EXTRACTOR_NAME,
            description=metadata.get("description"),
            thumbnail=metadata.get("thumbnail"),
            duration=metadata.get("duration"),
            upload_date=metadata.get("upload_date"),
            uploader=metadata.get("uploader"),
        )

    def _resolve_watch_page(self, original_url: str) -> Tuple[str, str]:
        final_url, html = self._fetch_page_with_final_url(original_url)
        if self._looks_like_watch_page(final_url, html):
            return final_url, html

        replacement = self._search_replacement_watch_url(original_url)
        if replacement:
            repl_url, repl_html = self._fetch_page_with_final_url(replacement)
            if self._looks_like_watch_page(repl_url, repl_html):
                return repl_url, repl_html

        if html:
            return original_url, html
        raise ExtractionError("Unable to resolve AVTub watch page")

    def _fetch_page_with_final_url(self, url: str) -> Tuple[str, str]:
        session = getattr(self.session, "_session", None)
        if session is None:
            response = self.session.get(url, headers={"Accept-Encoding": "gzip, deflate"}, allow_redirects=True)
            return response.url, response.text

        response = session.get(
            url,
            headers={
                **session.headers,
                "Accept-Encoding": "gzip, deflate",
            },
            allow_redirects=True,
            timeout=getattr(self.session, "timeout", 60),
        )
        response.raise_for_status()
        return response.url, response.text

    @staticmethod
    def _looks_like_watch_page(final_url: str, html: str) -> bool:
        if "/watch/" in final_url and "<article id=\"post-" in html:
            return True
        if "itemprop=\"embedURL\"" in html or "ystream.id/e/" in html:
            return True
        return False

    def _search_replacement_watch_url(self, original_url: str) -> Optional[str]:
        slug = self._extract_watch_slug(original_url)
        if not slug:
            return None

        base = f"{urlparse(original_url).scheme}://{urlparse(original_url).netloc}"
        search_term = " ".join(slug.replace("-", " ").split()[:6]).strip()
        if not search_term:
            return None

        endpoint = f"{base}/wp-json/wp/v2/posts"
        try:
            response = self.session.get(endpoint, params={"search": search_term, "per_page": 10})
            items = response.json()
        except Exception as e:
            logger.debug("AVTub replacement search failed: %s", e)
            return None

        if not isinstance(items, list):
            return None

        best_link = None
        best_score = 0.0

        for item in items:
            if not isinstance(item, dict):
                continue
            item_slug = str(item.get("slug") or "").strip()
            item_link = str(item.get("link") or "").strip()
            if not item_slug or not item_link:
                continue

            score = difflib.SequenceMatcher(None, slug, item_slug).ratio()
            if item_slug.startswith(slug.split("-")[0]):
                score += 0.08

            if score > best_score:
                best_score = score
                best_link = item_link

        if best_score >= 0.35:
            logger.info("Resolved AVTub slug '%s' to '%s' (score %.2f)", slug, best_link, best_score)
            return best_link
        return None

    def _collect_candidate_urls(self, page_url: str, html: str) -> List[str]:
        soup = BeautifulSoup(html, "html.parser")
        candidates: List[str] = []

        embed_meta = soup.find("meta", attrs={"itemprop": "embedURL"})
        if embed_meta and embed_meta.get("content"):
            candidates.append(self._make_absolute_url(page_url, str(embed_meta.get("content"))))

        for iframe in soup.find_all("iframe"):
            src = iframe.get("src") or iframe.get("data-src")
            if src:
                candidates.append(self._make_absolute_url(page_url, str(src)))

        extracted = MediaURLExtractor(page_url).extract_from_html(html)
        for item in extracted:
            media_url = item.get("url")
            if not isinstance(media_url, str):
                continue
            if item.get("type") == "thumbnail":
                continue
            candidates.append(media_url)

        return self._deduplicate_urls(candidates)

    def _extract_candidate_formats(self, candidate_url: str, referer: str) -> List[StreamFormat]:
        stream_type = self._detect_stream_type(candidate_url)
        if stream_type in {StreamType.HLS, StreamType.DASH} or candidate_url.lower().endswith((".mp4", ".webm", ".mkv", ".m4v")):
            return [self._build_stream_format(candidate_url, referer)]

        ystream_code = self._extract_ystream_code(candidate_url)
        if ystream_code:
            return self._extract_ystream_formats(ystream_code, candidate_url, referer)

        try:
            nested_html = self._fetch_page(candidate_url, headers={"Referer": referer, "Accept-Encoding": "gzip, deflate"})
        except Exception:
            return []

        nested_formats: List[StreamFormat] = []
        nested_urls = MediaURLExtractor(candidate_url).extract_from_html(nested_html)
        for item in nested_urls:
            nested_url = item.get("url")
            if not isinstance(nested_url, str):
                continue
            if item.get("type") in {"thumbnail", "iframe", "embed"}:
                continue
            nested_formats.append(self._build_stream_format(nested_url, candidate_url))

        return self._deduplicate_formats(nested_formats)

    def _extract_ystream_formats(self, code: str, embed_url: str, page_referer: str) -> List[StreamFormat]:
        playback = self._fetch_ystream_playback(code, embed_url, page_referer)
        if not playback:
            return []

        playback_data = self._decode_ystream_playback(playback)
        if not isinstance(playback_data, dict):
            return []

        formats: List[StreamFormat] = []
        for idx, source in enumerate(playback_data.get("sources") or []):
            if not isinstance(source, dict):
                continue

            stream_url = source.get("url")
            if not isinstance(stream_url, str) or not stream_url.strip():
                continue

            quality = source.get("label") or source.get("quality")
            height = source.get("height")
            bitrate = source.get("bitrate_kbps")

            stream_type = self._detect_stream_type(stream_url)
            if stream_type == StreamType.DIRECT and ".m3u8" in stream_url.lower():
                stream_type = StreamType.HLS

            fmt = StreamFormat(
                format_id=f"avtub-ystream-{idx}",
                url=stream_url,
                ext="mp4",
                quality=str(quality) if quality is not None else None,
                height=int(height) if isinstance(height, int) else None,
                width=int(int(height) * 16 / 9) if isinstance(height, int) and height > 0 else None,
                bitrate=int(bitrate) if isinstance(bitrate, (int, float)) else None,
                stream_type=stream_type,
                is_video=True,
                is_audio=True,
                headers={"Referer": embed_url, "Origin": f"{urlparse(embed_url).scheme}://{urlparse(embed_url).netloc}"},
                label=f"YStream {quality}" if quality else "YStream",
            )
            formats.append(fmt)

        return self._deduplicate_formats(formats)

    def _fetch_ystream_playback(self, code: str, embed_url: str, page_referer: str) -> Optional[Dict[str, Any]]:
        endpoints = [
            f"https://ystream.id/api/videos/{code}/embed/playback",
            f"https://ystream.id/api/videos/{code}/playback",
        ]

        for endpoint in endpoints:
            try:
                response = self.session.get(
                    endpoint,
                    headers={
                        "Accept": "application/json",
                        "Referer": embed_url,
                        "Origin": "https://ystream.id",
                        "X-Embed-Origin": f"{urlparse(page_referer).scheme}://{urlparse(page_referer).netloc}",
                        "X-Embed-Referer": page_referer,
                    },
                )
                data = response.json()
                playback = data.get("playback")
                if isinstance(playback, dict):
                    return playback
            except Exception as e:
                logger.debug("YStream playback request failed for %s: %s", endpoint, e)

        return None

    def _decode_ystream_playback(self, playback: Dict[str, Any]) -> Dict[str, Any]:
        if "sources" in playback:
            return playback

        for key in ("payload", "payload2"):
            payload = playback.get(key)
            iv = playback.get("iv" if key == "payload" else "iv2")
            key_parts = playback.get("key_parts")
            if not (isinstance(payload, str) and isinstance(iv, str) and isinstance(key_parts, list)):
                continue

            decoded = self._decrypt_with_pycryptodome(key_parts, iv, payload)
            if decoded:
                return decoded

            decoded = self._decrypt_with_node(key_parts, iv, payload)
            if decoded:
                return decoded

        raise ExtractionError(
            "Unable to decrypt YStream playback payload. Install `pycryptodome` or ensure `node` is available."
        )

    @staticmethod
    def _b64url_decode(value: str) -> bytes:
        import base64

        normalized = value.replace("-", "+").replace("_", "/")
        normalized += "=" * ((4 - (len(normalized) % 4)) % 4)
        return base64.b64decode(normalized)

    def _decrypt_with_pycryptodome(self, key_parts: List[str], iv_b64u: str, payload_b64u: str) -> Optional[Dict[str, Any]]:
        try:
            from Cryptodome.Cipher import AES
        except Exception:
            return None

        try:
            key = b"".join(self._b64url_decode(part) for part in key_parts)
            iv = self._b64url_decode(iv_b64u)
            encrypted = self._b64url_decode(payload_b64u)
            ciphertext, tag = encrypted[:-16], encrypted[-16:]

            cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
            plaintext = cipher.decrypt_and_verify(ciphertext, tag)
            return json.loads(plaintext.decode("utf-8", errors="ignore"))
        except Exception as e:
            logger.debug("PyCryptodome playback decrypt failed: %s", e)
            return None

    def _decrypt_with_node(self, key_parts: List[str], iv_b64u: str, payload_b64u: str) -> Optional[Dict[str, Any]]:
        script = (
            "const fs=require('fs');"
            "const crypto=require('crypto');"
            "const x=JSON.parse(fs.readFileSync(0,'utf8'));"
            "const b=s=>{s=s.replace(/-/g,'+').replace(/_/g,'/');while(s.length%4)s+='=';return Buffer.from(s,'base64')};"
            "const key=Buffer.concat(x.key_parts.map(b));"
            "const iv=b(x.iv);"
            "const payload=b(x.payload);"
            "const ct=payload.subarray(0,payload.length-16);"
            "const tag=payload.subarray(payload.length-16);"
            "const dec=crypto.createDecipheriv('aes-256-gcm',key,iv);"
            "dec.setAuthTag(tag);"
            "let out=dec.update(ct);out=Buffer.concat([out,dec.final()]);"
            "process.stdout.write(out.toString('utf8'));"
        )

        payload = {
            "key_parts": key_parts,
            "iv": iv_b64u,
            "payload": payload_b64u,
        }

        try:
            proc = subprocess.run(
                ["node", "-e", script],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                check=True,
                timeout=15,
            )
            return json.loads(proc.stdout)
        except Exception as e:
            logger.debug("Node playback decrypt failed: %s", e)
            return None

    def _build_stream_format(self, stream_url: str, referer: str) -> StreamFormat:
        stream_type = self._detect_stream_type(stream_url)
        quality, width, height = self._parse_quality(stream_url)

        return StreamFormat(
            format_id=f"avtub-{self._generate_id(stream_url)}",
            url=stream_url,
            ext="mp4",
            quality=quality,
            width=width,
            height=height,
            stream_type=stream_type,
            is_video=True,
            is_audio=True,
            headers={"Referer": referer},
            label="AVTub stream",
        )

    @staticmethod
    def _extract_watch_slug(url: str) -> Optional[str]:
        match = AvtubExtractor._WATCH_SLUG_RE.search(url)
        if not match:
            return None
        slug = match.group(1).strip().strip("/")
        slug = re.sub(r"-\d+$", "", slug)
        return slug or None

    @staticmethod
    def _extract_ystream_code(url: str) -> Optional[str]:
        match = AvtubExtractor._YSTREAM_CODE_RE.search(url)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _deduplicate_urls(urls: List[str]) -> List[str]:
        seen = set()
        result: List[str] = []
        for url in urls:
            if not isinstance(url, str):
                continue
            normalized = url.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result

    @staticmethod
    def _deduplicate_formats(formats: List[StreamFormat]) -> List[StreamFormat]:
        seen = set()
        result: List[StreamFormat] = []
        for fmt in formats:
            if fmt.url in seen:
                continue
            seen.add(fmt.url)
            result.append(fmt)
        return result
