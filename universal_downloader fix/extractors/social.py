"""
Social media extractor backed by yt-dlp.

Handles major social/video/audio platforms through a registered extractor so the
CLI can route YouTube, TikTok, Instagram, X, Facebook, Reddit, SoundCloud, and
similar URLs to the right backend before falling back to the generic parser.
"""

import re
from typing import Dict, Iterable, Optional
from urllib.parse import urlparse

from .base import ExtractorBase, ExtractionError, register_extractor
from .ytdlp import YtdlpExtractor
from models.media import MediaInfo


@register_extractor()
class SocialMediaExtractor(ExtractorBase):
    """Domain-aware extractor for social and creator platforms."""

    EXTRACTOR_NAME = "social"
    EXTRACTOR_DESCRIPTION = (
        "Social media extractor (YouTube, TikTok, Instagram, Facebook, X, "
        "Reddit, SoundCloud, Twitch, Vimeo, and more)"
    )

    SITE_DOMAINS: Dict[str, tuple[str, ...]] = {
        "youtube": (
            "youtube.com",
            "youtu.be",
            "youtube-nocookie.com",
        ),
        "tiktok": (
            "tiktok.com",
            "vt.tiktok.com",
            "vm.tiktok.com",
            "douyin.com",
        ),
        "instagram": (
            "instagram.com",
            "instagr.am",
        ),
        "facebook": (
            "facebook.com",
            "fb.watch",
        ),
        "x": (
            "x.com",
            "twitter.com",
        ),
        "threads": (
            "threads.net",
        ),
        "reddit": (
            "reddit.com",
            "redd.it",
            "v.redd.it",
        ),
        "vimeo": (
            "vimeo.com",
        ),
        "dailymotion": (
            "dailymotion.com",
            "dai.ly",
        ),
        "twitch": (
            "twitch.tv",
            "clips.twitch.tv",
        ),
        "soundcloud": (
            "soundcloud.com",
        ),
        "bandcamp": (
            "bandcamp.com",
        ),
        "mixcloud": (
            "mixcloud.com",
        ),
        "pinterest": (
            "pinterest.com",
            "pin.it",
        ),
        "linkedin": (
            "linkedin.com",
        ),
        "bilibili": (
            "bilibili.com",
            "b23.tv",
        ),
        "niconico": (
            "nicovideo.jp",
            "nico.ms",
        ),
        "vk": (
            "vk.com",
            "vkvideo.ru",
        ),
        "weibo": (
            "weibo.com",
        ),
        "xiaohongshu": (
            "xiaohongshu.com",
            "xhslink.com",
        ),
    }

    URL_PATTERNS = [
        r"https?://(?:(?:www|m|music)\.)?(?:youtube\.com|youtube-nocookie\.com)/.+",
        r"https?://(?:youtu\.be)/.+",
        r"https?://(?:(?:www|m)\.)?tiktok\.com/.+",
        r"https?://(?:vm|vt)\.tiktok\.com/.+",
        r"https?://(?:www\.)?douyin\.com/.+",
        r"https?://(?:www\.)?(?:instagram\.com|instagr\.am)/.+",
        r"https?://(?:(?:www|m)\.)?facebook\.com/.+",
        r"https?://fb\.watch/.+",
        r"https?://(?:(?:www|m)\.)?(?:twitter\.com|x\.com)/.+",
        r"https?://(?:www\.)?threads\.net/.+",
        r"https?://(?:www\.)?(?:reddit\.com|redd\.it|v\.redd\.it)/.+",
        r"https?://(?:www\.)?(?:vimeo\.com|player\.vimeo\.com)/.+",
        r"https?://(?:www\.)?(?:dailymotion\.com|dai\.ly)/.+",
        r"https?://(?:(?:www|m)\.)?twitch\.tv/.+",
        r"https?://clips\.twitch\.tv/.+",
        r"https?://(?:www\.)?soundcloud\.com/.+",
        r"https?://(?:[\w-]+\.)?bandcamp\.com/.+",
        r"https?://(?:www\.)?mixcloud\.com/.+",
        r"https?://(?:www\.)?(?:pinterest\.com|pin\.it)/.+",
        r"https?://(?:www\.)?linkedin\.com/.+",
        r"https?://(?:www\.)?(?:bilibili\.com|b23\.tv)/.+",
        r"https?://(?:www\.)?(?:nicovideo\.jp|nico\.ms)/.+",
        r"https?://(?:www\.)?(?:vk\.com|vkvideo\.ru)/.+",
        r"https?://(?:www\.)?weibo\.com/.+",
        r"https?://(?:www\.)?(?:xiaohongshu\.com|xhslink\.com)/.+",
    ]

    @classmethod
    def suitable(cls, url: str) -> bool:
        if not YtdlpExtractor.is_available():
            return False
        return super().suitable(url)

    def extract(self, url: str) -> MediaInfo:
        if not YtdlpExtractor.is_available():
            raise ExtractionError("Social extractor requires yt-dlp. Install: pip install -U yt-dlp")

        delegate = YtdlpExtractor(self.session, self.config)
        media_info = delegate.extract(url)
        site_name = self._resolve_site_name(url, media_info.extractor)
        media_info.extractor = f"{self.EXTRACTOR_NAME}/{site_name}"
        return media_info

    @classmethod
    def _resolve_site_name(cls, url: str, delegated_name: Optional[str] = None) -> str:
        hostname = cls._hostname(url)
        for site_name, domains in cls.SITE_DOMAINS.items():
            if cls._matches_domain(hostname, domains):
                return site_name

        if delegated_name and "/" in delegated_name:
            _, raw_name = delegated_name.split("/", 1)
            normalized = re.sub(r"[^a-z0-9]+", "-", raw_name.lower()).strip("-")
            if normalized:
                return normalized

        return "unknown"

    @staticmethod
    def _hostname(url: str) -> str:
        return (urlparse(url).hostname or "").lower().lstrip(".")

    @staticmethod
    def _matches_domain(hostname: str, domains: Iterable[str]) -> bool:
        return any(hostname == domain or hostname.endswith(f".{domain}") for domain in domains)
