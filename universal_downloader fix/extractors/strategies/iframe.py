"""Iframe/embed discovery strategy."""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .base import ExtractionStrategy


BLOCKED_IFRAME_HOSTS = {
    'googletagmanager.com',
    'google.com',
    'googleadservices.com',
    'doubleclick.net',
    'facebook.com',
    'www.facebook.com',
    'connect.facebook.net',
    'platform.twitter.com',
    'x.com',
    'www.youtube.com',
}

MEDIA_HINT_TOKENS = (
    'embed',
    'player',
    'stream',
    'video',
    'watch',
    'play',
    'load',
)


class IframeEmbedStrategy(ExtractionStrategy):
    NAME = "iframe_embed"
    PRIORITY = 20
    REQUIRES_HTML = True
    STAGE = "discovery"

    async def execute(self, ctx: Any) -> Optional[None]:
        if not ctx.html:
            ctx.html = await self.extractor._fetch_page_async(ctx)

        soup = BeautifulSoup(ctx.html or '', 'html.parser')
        allowed_hosts = set(ctx.hints.get('allowed_iframe_hosts') or [])

        for iframe in soup.find_all('iframe'):
            src = iframe.get('src') or iframe.get('data-src')
            if not isinstance(src, str) or not src.strip():
                continue

            absolute = urljoin(ctx.final_url or ctx.url, src)
            parsed = urlparse(absolute)
            host = (parsed.hostname or '').lower()
            if allowed_hosts and host not in allowed_hosts and not any(host.endswith(f'.{item}') for item in allowed_hosts):
                continue
            if self._is_blocked_host(host):
                continue
            if not allowed_hosts and not self._looks_like_media_embed(parsed):
                continue

            doc = await ctx.request.get_text(absolute, headers={'Referer': ctx.final_url or ctx.url})
            ctx.state['outer_url'] = ctx.original_url
            ctx.state['outer_html'] = ctx.html
            ctx.state['embedded_from'] = ctx.final_url or ctx.url
            ctx.url = doc.url
            ctx.final_url = doc.url
            ctx.html = doc.text or ''
            ctx.response_headers = doc.headers
            ctx.hints['embedded_url'] = doc.url
            return None

        return None

    @staticmethod
    def _is_blocked_host(host: str) -> bool:
        return any(host == blocked or host.endswith(f'.{blocked}') for blocked in BLOCKED_IFRAME_HOSTS)

    @staticmethod
    def _looks_like_media_embed(parsed: Any) -> bool:
        text = f"{parsed.netloc}{parsed.path}".lower()
        return any(token in text for token in MEDIA_HINT_TOKENS)
