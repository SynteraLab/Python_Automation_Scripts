"""Generic fallback extractor powered by reusable strategies."""

from __future__ import annotations

from .base import ExtractorBase, register_extractor
from .strategies.api import APIExtractionStrategy
from .strategies.browser import BrowserNetworkStrategy
from .strategies.hls import HLSStrategy
from .strategies.html import HTMLParsingStrategy
from .strategies.iframe import IframeEmbedStrategy
from .strategies.json import JSONStrategy
from .strategies.jwplayer import JWPlayerStrategy
from .strategies.packed import PackedPlayerStrategy
from utils.parser import MetadataExtractor


@register_extractor(generic=True)
class GenericExtractor(ExtractorBase):
    """Ultimate fallback extractor for known and unknown sites."""

    EXTRACTOR_NAME = "generic"
    EXTRACTOR_DESCRIPTION = "Generic extractor with staged strategies and intelligent fallback"
    URL_PATTERNS = [r'https?://.+']
    STOP_ON_FIRST_VALID = False

    DEFAULT_HINTS = {
        'generic_mode': True,
        'allow_browser_fallback': True,
        'probe_embeds': True,
    }

    STRATEGIES = [
        IframeEmbedStrategy,
        JWPlayerStrategy,
        PackedPlayerStrategy,
        HLSStrategy,
        JSONStrategy,
        APIExtractionStrategy,
        HTMLParsingStrategy,
        BrowserNetworkStrategy,
    ]

    def extract(self, url: str):
        return self.extract_with_strategies(url)

    async def build_context(self, url: str):
        ctx = await super().build_context(url)
        doc = await ctx.request.get_text(url)
        ctx.html = doc.text or ''
        ctx.final_url = doc.url
        ctx.response_headers = doc.headers
        ctx.metadata.update(MetadataExtractor(url).extract(ctx.html))
        return ctx
