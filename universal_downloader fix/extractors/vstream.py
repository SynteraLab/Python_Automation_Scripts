"""VStream extractor powered by reusable strategies."""

from __future__ import annotations

from .base import ExtractorBase, register_extractor
from .strategies.hls import HLSStrategy
from .strategies.html import HTMLParsingStrategy
from .strategies.iframe import IframeEmbedStrategy
from .strategies.json import JSONStrategy
from .strategies.jwplayer import JWPlayerStrategy
from .strategies.packed import PackedPlayerStrategy


@register_extractor()
class VStreamExtractor(ExtractorBase):
    """Extractor for VStream embed pages and wrappers that contain them."""

    EXTRACTOR_NAME = "vstream"
    EXTRACTOR_DESCRIPTION = "VStream embed extractor (strategy-driven iframe -> player resolution)"

    URL_PATTERNS = [
        r'https?://(?:www\.)?vstream\.id/embed/.+',
        r'https?://(?:www\.)?vstream\.id/e/.+',
    ]

    DEFAULT_HINTS = {
        'allowed_iframe_hosts': {'vstream.id'},
        'prefer_embed_page': True,
        'propagate_referer': True,
        'packed_js': True,
        'strategy_order': [
            'iframe_embed',
            'packed_player',
            'jwplayer',
            'hls',
            'json',
            'html',
        ],
    }

    STRATEGIES = [
        IframeEmbedStrategy,
        PackedPlayerStrategy,
        JWPlayerStrategy,
        HLSStrategy,
        JSONStrategy,
        HTMLParsingStrategy,
    ]

    STOP_ON_FIRST_VALID = False

    @classmethod
    def can_handle(cls, html: str) -> bool:
        html_lower = html.lower()
        return 'vstream.id/embed/' in html_lower or 'vstream.id/e/' in html_lower

    def extract(self, url: str):
        return self.extract_with_strategies(url)
