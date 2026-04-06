"""
EroMe extractor.

Features:
- Extract videos and photos from EroMe album pages.
- Support direct EroMe CDN URLs (video/image).
- Attach required Referer/Origin headers so fast direct downloads (aria2)
  can access EroMe CDN assets without 403.
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from .base import ExtractorBase, register_extractor, ExtractionError
from models.media import MediaInfo, StreamFormat, MediaType, StreamType


@dataclass
class EromeAlbumItem:
    index: int
    media_type: str  # "video" or "photo"
    title: str
    format: StreamFormat
    thumbnail: Optional[str] = None


@register_extractor()
class EromeExtractor(ExtractorBase):
    EXTRACTOR_NAME = "erome"
    EXTRACTOR_DESCRIPTION = "EroMe album extractor (video + photo, fast direct download)"

    URL_PATTERNS = [
        r"https?://(?:www\.)?erome\.com/a/[A-Za-z0-9_-]+/?(?:\?.*)?$",
        r"https?://[^/]*erome\.com/.+\.(?:mp4|webm|jpg|jpeg|png|webp|gif|avif)(?:\?.*)?$",
    ]

    _VIDEO_RE = re.compile(r"https?://[^\s\"'<>]+\.(?:mp4|webm)(?:\?[^\s\"'<>]*)?", re.IGNORECASE)
    _IMAGE_RE = re.compile(r"https?://[^\s\"'<>]+\.(?:jpg|jpeg|png|webp|gif|avif)(?:\?[^\s\"'<>]*)?", re.IGNORECASE)
    _IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "avif"}

    def extract(self, url: str) -> MediaInfo:
        if re.match(self.URL_PATTERNS[1], url, re.IGNORECASE):
            return self._extract_direct(url)

        album = self.extract_album_items(url)
        formats = [item.format for item in album["items"]]

        if not formats:
            raise ExtractionError("No downloadable EroMe media found")

        formats.sort(key=lambda f: f.quality_score, reverse=True)

        return MediaInfo(
            id=album["id"],
            title=album["title"],
            url=album["url"],
            formats=formats,
            media_type=MediaType.VIDEO,
            extractor=self.EXTRACTOR_NAME,
            thumbnail=album.get("thumbnail"),
            uploader=album.get("uploader"),
        )

    def extract_album_items(self, url: str) -> Dict[str, Any]:
        """Extract structured album items for EroMe-specific CLI workflows."""
        html = self._fetch_album_html(url)
        title = self._extract_title(html, url)
        thumbnail = self._extract_thumbnail(html)
        uploader = self._extract_uploader(html)
        items = self._extract_album_items_from_html(html, page_url=url, album_title=title)

        if not items:
            raise ExtractionError("No downloadable EroMe media found")

        return {
            "id": self._generate_id(url),
            "title": title,
            "url": url,
            "thumbnail": thumbnail,
            "uploader": uploader,
            "items": items,
        }

    def _extract_direct(self, url: str) -> MediaInfo:
        filename = urlparse(url).path.rsplit("/", 1)[-1]
        title = re.sub(r"\.(mp4|webm|jpg|jpeg|png|webp|gif|avif)$", "", filename, flags=re.IGNORECASE)
        title = title.replace("_", " ").replace("-", " ").strip() or "EroMe Media"

        ext = self._extract_ext_from_url(url, default="bin")
        is_image = ext in self._IMAGE_EXTS
        height = self._parse_height_from_url(url)
        width = int(height * 16 / 9) if height else None
        quality = f"{height}p" if height else None

        fmt = StreamFormat(
            format_id="er-direct",
            url=url,
            ext=ext,
            quality=quality,
            width=width,
            height=height,
            stream_type=StreamType.DIRECT,
            is_video=not is_image,
            is_audio=not is_image,
            label="EroMe Photo" if is_image else "EroMe Direct",
            headers={
                "Referer": "https://www.erome.com/",
                "Origin": "https://www.erome.com",
            },
        )

        return MediaInfo(
            id=self._generate_id(url),
            title=title,
            url=url,
            formats=[fmt],
            media_type=MediaType.UNKNOWN if is_image else MediaType.VIDEO,
            extractor=self.EXTRACTOR_NAME,
        )

    def _fetch_album_html(self, url: str) -> str:
        # Force gzip/deflate to avoid br decoding issues when brotli runtime is absent.
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Referer": "https://www.erome.com/",
        }
        try:
            response = self.session.get(url, headers=headers)
            return response.text
        except Exception as e:
            raise ExtractionError(f"Failed to fetch EroMe page: {e}")

    def _extract_album_items_from_html(self, html: str, page_url: str, album_title: str) -> List[EromeAlbumItem]:
        soup = BeautifulSoup(html, "html.parser")
        album_container = self._find_album_container(soup, page_url)

        if album_container:
            groups = album_container.find_all("div", class_="media-group")
        else:
            groups = []

        if not groups and album_container:
            groups = [album_container]
        if not groups:
            groups = [soup]

        items: List[EromeAlbumItem] = []
        seen_keys = set()
        photo_index = 0
        video_index = 0

        for group in groups:
            video_item = self._build_video_item(group, page_url=page_url, album_title=album_title, index=video_index + 1)
            if video_item:
                media_url = video_item.format.url
                media_key = self._media_dedupe_key(media_url)
                if media_key not in seen_keys:
                    seen_keys.add(media_key)
                    video_index += 1
                    video_item.index = video_index
                    video_item.title = f"{album_title} [video {video_index}]"
                    video_item.format.format_id = f"er-v-{video_index}-{video_item.format.height or 0}"
                    items.append(video_item)
                continue

            photo_item = self._build_photo_item(group, page_url=page_url, album_title=album_title, index=photo_index + 1)
            if photo_item:
                media_url = photo_item.format.url
                media_key = self._media_dedupe_key(media_url)
                if media_key not in seen_keys:
                    seen_keys.add(media_key)
                    photo_index += 1
                    photo_item.index = photo_index
                    photo_item.title = f"{album_title} [photo {photo_index}]"
                    photo_item.format.format_id = f"er-p-{photo_index}"
                    items.append(photo_item)

        # Merge regex/script fallback to capture media that is not represented
        # in normal album blocks (common on layout changes).
        fallback_source = str(album_container) if album_container else html
        for fallback_item in self._build_fallback_items(
            fallback_source,
            page_url=page_url,
            album_title=album_title,
        ):
            media_key = self._media_dedupe_key(fallback_item.format.url)
            if media_key in seen_keys:
                continue

            seen_keys.add(media_key)
            if fallback_item.media_type == "video":
                video_index += 1
                fallback_item.index = video_index
                fallback_item.title = f"{album_title} [video {video_index}]"
                fallback_item.format.format_id = f"er-v-{video_index}-{fallback_item.format.height or 0}"
            else:
                photo_index += 1
                fallback_item.index = photo_index
                fallback_item.title = f"{album_title} [photo {photo_index}]"
                fallback_item.format.format_id = f"er-p-{photo_index}"

            items.append(fallback_item)

        return items

    def _build_fallback_items(self, html: str, page_url: str, album_title: str) -> List[EromeAlbumItem]:
        """Fallback parser for pages where normal album blocks are missing."""
        decoded_html = html.replace("\\/", "/")
        matches: List[tuple[int, str, str]] = []

        for match in self._VIDEO_RE.finditer(decoded_html):
            matches.append((match.start(), "video", match.group(0)))

        for match in self._IMAGE_RE.finditer(decoded_html):
            matches.append((match.start(), "photo", match.group(0)))

        matches.sort(key=lambda item: item[0])

        items: List[EromeAlbumItem] = []
        seen_keys = set()
        video_index = 0
        photo_index = 0
        thumbnail_hint = self._extract_thumbnail(decoded_html)

        for _, media_type, raw_url in matches:
            media_url = self._make_absolute_url(page_url, raw_url)

            if media_type == "photo" and "/thumbs/" in media_url:
                media_url = media_url.replace("/thumbs/", "/")

            if media_type == "photo" and not self._is_likely_photo_media(media_url):
                continue

            media_key = self._media_dedupe_key(media_url)
            if media_key in seen_keys:
                continue
            seen_keys.add(media_key)

            if media_type == "video":
                video_index += 1
                height = self._parse_height_from_url(media_url)
                quality = f"{height}p" if height else None
                ext = self._extract_ext_from_url(media_url, default="mp4")
                fmt = StreamFormat(
                    format_id=f"er-v-fallback-{video_index}",
                    url=media_url,
                    ext=ext,
                    quality=quality,
                    width=int(height * 16 / 9) if height else None,
                    height=height,
                    stream_type=StreamType.DIRECT,
                    is_video=True,
                    is_audio=True,
                    label=f"EroMe Video {quality}" if quality else "EroMe Video",
                    headers={"Referer": page_url, "Origin": "https://www.erome.com"},
                )
                items.append(
                    EromeAlbumItem(
                        index=video_index,
                        media_type="video",
                        title=f"{album_title} [video {video_index}]",
                        format=fmt,
                        thumbnail=thumbnail_hint,
                    )
                )
                continue

            photo_index += 1
            ext = self._extract_ext_from_url(media_url, default="jpg")
            fmt = StreamFormat(
                format_id=f"er-p-fallback-{photo_index}",
                url=media_url,
                ext=ext,
                quality=None,
                width=None,
                height=None,
                stream_type=StreamType.DIRECT,
                is_video=False,
                is_audio=False,
                label="EroMe Photo",
                headers={"Referer": page_url, "Origin": "https://www.erome.com"},
            )
            items.append(
                EromeAlbumItem(
                    index=photo_index,
                    media_type="photo",
                    title=f"{album_title} [photo {photo_index}]",
                    format=fmt,
                    thumbnail=media_url,
                )
            )

        return items

    def _find_album_container(self, soup: BeautifulSoup, page_url: str) -> Optional[Tag]:
        album_slug = page_url.rstrip("/").split("/")[-1]
        container = soup.find(id=f"album_{album_slug}")
        if container:
            return container

        # Fallback: any album_* container on page.
        return soup.find("div", id=re.compile(r"^album_"))

    def _build_video_item(self, group: Tag, page_url: str, album_title: str, index: int) -> Optional[EromeAlbumItem]:
        videos = group.find_all("video")
        if not videos:
            return None

        candidates: List[Dict[str, Any]] = []

        for video in videos:
            poster = video.get("poster") if isinstance(video.get("poster"), str) else None

            video_src = video.get("src")
            if isinstance(video_src, str) and video_src and self._VIDEO_RE.match(video_src):
                candidates.append(
                    {
                        "url": self._make_absolute_url(page_url, video_src),
                        "height": self._parse_height(None, video_src),
                        "label": None,
                        "poster": poster,
                    }
                )

            for source in video.find_all("source"):
                src = source.get("src")
                if not isinstance(src, str) or not src:
                    continue

                abs_src = self._make_absolute_url(page_url, src)
                if not self._VIDEO_RE.match(abs_src):
                    continue

                res_raw = source.get("res")
                label_raw = source.get("label")
                res: Optional[str] = res_raw if isinstance(res_raw, str) else None
                label: Optional[str] = label_raw if isinstance(label_raw, str) else None
                candidates.append(
                    {
                        "url": abs_src,
                        "height": self._parse_height(res, abs_src),
                        "label": label,
                        "poster": poster,
                    }
                )

        if not candidates:
            return None

        best = max(candidates, key=lambda item: (item.get("height") or 0, len(item.get("url") or "")))
        media_url = best["url"]
        height = best.get("height")
        width = int(height * 16 / 9) if height else None
        quality = f"{height}p" if height else None
        raw_label = best.get("label")
        label: str = raw_label.strip() if isinstance(raw_label, str) and raw_label.strip() else "EroMe Video"
        if quality and quality not in label:
            label = f"{label} {quality}".strip()

        fmt = StreamFormat(
            format_id=f"er-v-{index}-{height or 0}",
            url=media_url,
            ext=self._extract_ext_from_url(media_url, default="mp4"),
            quality=quality,
            width=width,
            height=height,
            stream_type=StreamType.DIRECT,
            is_video=True,
            is_audio=True,
            label=label,
            headers={
                "Referer": page_url,
                "Origin": "https://www.erome.com",
            },
        )

        return EromeAlbumItem(
            index=index,
            media_type="video",
            title=f"{album_title} [video {index}]",
            format=fmt,
            thumbnail=best.get("poster"),
        )

    def _build_photo_item(self, group: Tag, page_url: str, album_title: str, index: int) -> Optional[EromeAlbumItem]:
        if group.find("video"):
            return None

        urls = self._collect_photo_urls(group, page_url)
        if not urls:
            return None

        best_url = max(urls, key=self._photo_score)
        ext = self._extract_ext_from_url(best_url, default="jpg")

        fmt = StreamFormat(
            format_id=f"er-p-{index}",
            url=best_url,
            ext=ext,
            quality=None,
            width=None,
            height=None,
            stream_type=StreamType.DIRECT,
            is_video=False,
            is_audio=False,
            label="EroMe Photo",
            headers={
                "Referer": page_url,
                "Origin": "https://www.erome.com",
            },
        )

        return EromeAlbumItem(
            index=index,
            media_type="photo",
            title=f"{album_title} [photo {index}]",
            format=fmt,
            thumbnail=best_url,
        )

    def _collect_photo_urls(self, group: Tag, page_url: str) -> List[str]:
        candidates: List[str] = []

        for element in group.find_all(True):
            for attr in ("src", "data-src", "data-original", "data-url", "href"):
                value = element.get(attr)
                if not isinstance(value, str) or not value:
                    continue
                abs_url = self._make_absolute_url(page_url, value)
                if self._is_image_url(abs_url):
                    candidates.append(abs_url)

        group_html = str(group)
        for url in self._IMAGE_RE.findall(group_html):
            abs_url = self._make_absolute_url(page_url, url)
            if self._is_image_url(abs_url):
                candidates.append(abs_url)

        expanded: List[str] = []
        for url in candidates:
            expanded.append(url)
            if "/thumbs/" in url:
                expanded.append(url.replace("/thumbs/", "/"))

        unique: List[str] = []
        seen = set()
        for url in expanded:
            if url not in seen:
                seen.add(url)
                unique.append(url)
        return unique

    def _extract_title(self, html: str, fallback_url: str) -> str:
        soup = BeautifulSoup(html, "html.parser")

        og_title = soup.find("meta", property="og:title")
        if og_title:
            content = og_title.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()

        h1 = soup.find("h1", class_="album-title-page")
        if h1:
            text = h1.get_text(" ", strip=True)
            if text:
                return text

        if soup.title and soup.title.string:
            title = soup.title.string.strip()
            title = re.sub(r"\s*-\s*Porn\s*-\s*EroMe\s*$", "", title, flags=re.IGNORECASE)
            if title:
                return title

        return fallback_url.rstrip("/").split("/")[-1]

    def _extract_thumbnail(self, html: str) -> Optional[str]:
        soup = BeautifulSoup(html, "html.parser")

        og_image = soup.find("meta", property="og:image")
        if og_image:
            content = og_image.get("content")
            if isinstance(content, str) and content:
                return content

        video = soup.find("video")
        if video:
            poster = video.get("poster")
            if isinstance(poster, str) and poster:
                return poster

        img = soup.find("img")
        if img:
            src = img.get("src")
            if isinstance(src, str) and src:
                return src

        return None

    def _extract_uploader(self, html: str) -> Optional[str]:
        soup = BeautifulSoup(html, "html.parser")
        user_link = soup.find("a", id="user_name")
        if not user_link:
            return None

        text = user_link.get_text(" ", strip=True)
        return text or None

    def _parse_height(self, res_value: Optional[str], media_url: str) -> Optional[int]:
        if isinstance(res_value, str):
            m = re.search(r"(\d{3,4})", res_value)
            if m:
                return int(m.group(1))
        return self._parse_height_from_url(media_url)

    @staticmethod
    def _parse_height_from_url(media_url: str) -> Optional[int]:
        m = re.search(r"(?:_|/|-)(\d{3,4})p(?:[^\d]|$)", media_url, re.IGNORECASE)
        if m:
            return int(m.group(1))

        m = re.search(r"(\d{3,4})p", media_url, re.IGNORECASE)
        if m:
            return int(m.group(1))

        return None

    def _photo_score(self, url: str) -> int:
        score = 0
        if "/thumbs/" not in url:
            score += 100
        if "/thumbs/" in url:
            score -= 50
        score += len(url)
        return score

    def _is_image_url(self, url: str) -> bool:
        return bool(self._IMAGE_RE.match(url))

    @staticmethod
    def _is_likely_photo_media(url: str) -> bool:
        path = urlparse(url).path.lower()
        blocked_tokens = (
            "favicon",
            "logo",
            "avatar",
            "/assets/",
            "/static/",
            "/emoji/",
            "/icons/",
        )
        return not any(token in path for token in blocked_tokens)

    @staticmethod
    def _media_dedupe_key(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}"

    @staticmethod
    def _extract_ext_from_url(url: str, default: str = "bin") -> str:
        path = urlparse(url).path.lower()
        m = re.search(r"\.([a-z0-9]+)$", path)
        if not m:
            return default
        return m.group(1)
