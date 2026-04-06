"""Base extractor class and strategy-aware extraction primitives."""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Pattern, Type
from urllib.parse import urljoin, urlparse

from models.media import MediaInfo, MediaType, PlaylistInfo, StreamFormat, StreamType
from utils.extraction_logging import get_extraction_logger
from utils.network import RequestManager, SessionManager
from .engine import StrategyExecutionEngine
from .strategies.base import ExtractionStrategy, StrategyError, StrategyResult

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    """Raised when extraction fails."""


@dataclass(slots=True)
class ExtractionContext:
    """Shared execution context passed across strategies."""

    url: str
    original_url: str
    extractor_name: str
    session: SessionManager
    request: RequestManager
    config: Mapping[str, Any]
    html: Optional[str] = None
    final_url: Optional[str] = None
    response_headers: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    hints: Dict[str, Any] = field(default_factory=dict)
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    candidates: List[StrategyResult] = field(default_factory=list)
    state: Dict[str, Any] = field(default_factory=dict)


class ExtractorBase(ABC):
    """Abstract base class for all extractors."""

    EXTRACTOR_NAME: str = "base"
    EXTRACTOR_DESCRIPTION: str = "Base extractor class"
    EXTRACTOR_VERSION: Optional[str] = None
    URL_PATTERNS: ClassVar[List[str]] = []
    PRIORITY: int = 100
    REPLACES: ClassVar[List[str]] = []
    IS_GENERIC: bool = False
    ENABLED: bool = True
    REQUIRES_BROWSER: bool = False
    SUPPORTED_TYPES: ClassVar[List[MediaType]] = [MediaType.VIDEO]
    DEFAULT_HINTS: ClassVar[Dict[str, Any]] = {}
    STRATEGIES: ClassVar[List[Type[ExtractionStrategy]]] = []
    FALLBACK_STRATEGIES: ClassVar[List[Type[ExtractionStrategy]]] = []
    STOP_ON_FIRST_VALID: ClassVar[bool] = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.STRATEGIES = list(getattr(cls, 'STRATEGIES', []))
        cls.FALLBACK_STRATEGIES = list(getattr(cls, 'FALLBACK_STRATEGIES', []))

    def __init__(self, session: SessionManager, config: Optional[Dict[str, Any]] = None):
        self.session = session
        self.config = config or {}
        self._compiled_patterns: List[Pattern[str]] = []
        self._compile_patterns()
        self.request = RequestManager.from_legacy_session(session, self._config_mapping())
        self.engine = StrategyExecutionEngine()
        self.log = get_extraction_logger(__name__, self.EXTRACTOR_NAME)

    def _compile_patterns(self) -> None:
        self._compiled_patterns = [
            re.compile(pattern, re.IGNORECASE)
            for pattern in self.URL_PATTERNS
        ]

    @classmethod
    def suitable(cls, url: str) -> bool:
        for pattern in cls.URL_PATTERNS:
            if re.match(pattern, url, re.IGNORECASE):
                return True
        return False

    @classmethod
    def can_handle(cls, value: str) -> bool:
        return cls.suitable(value)

    @classmethod
    def match_confidence(cls, url: str) -> float:
        return 0.0

    @classmethod
    def register_strategy(
        cls,
        strategy_cls: Type[ExtractionStrategy],
        *,
        prepend: bool = False,
        fallback: bool = False,
    ) -> None:
        target = cls.FALLBACK_STRATEGIES if fallback else cls.STRATEGIES
        if strategy_cls in target:
            return
        if prepend:
            target.insert(0, strategy_cls)
        else:
            target.append(strategy_cls)

    def match_url(self, url: str) -> Optional[re.Match[str]]:
        for pattern in self._compiled_patterns:
            match = pattern.match(url)
            if match:
                return match
        return None

    @abstractmethod
    def extract(self, url: str) -> MediaInfo:
        """Extract media information from URL."""

    def extract_playlist(self, url: str) -> PlaylistInfo:
        raise NotImplementedError("This extractor does not support playlists")

    def extract_with_strategies(self, url: str) -> MediaInfo:
        return self._run_async(self._extract_with_strategies_async(url))

    async def _extract_with_strategies_async(self, url: str) -> MediaInfo:
        ctx = await self.build_context(url)
        winner = await self.execute_strategies(ctx)
        return self.build_media_info(ctx, winner)

    async def build_context(self, url: str) -> ExtractionContext:
        return ExtractionContext(
            url=url,
            original_url=url,
            extractor_name=self.EXTRACTOR_NAME,
            session=self.session,
            request=self.request,
            config=self._config_mapping(),
            hints=dict(self.DEFAULT_HINTS),
        )

    async def execute_strategies(self, ctx: ExtractionContext) -> StrategyResult:
        winners: List[StrategyResult] = []
        ordered = self.engine.resolve(self._strategy_chain(), ctx.hints)

        for strategy_cls in ordered:
            strategy = strategy_cls(self)
            strategy_log = self.log.bind(strategy=strategy.NAME)

            try:
                if not await strategy.applies(ctx):
                    strategy_log.event('SKIP', ctx.url)
                    continue

                strategy_log.event('START', ctx.url)
                result = await strategy.execute(ctx)
                if result is None:
                    strategy_log.event('MISS', ctx.url)
                    continue

                self._validate_result(ctx, result)
                result.score = self.score_result(ctx, result)
                winners.append(result)
                ctx.candidates.append(result)

                strategy_log.event(
                    'SUCCESS',
                    ctx.url,
                    details={
                        'score': result.score,
                        'confidence': result.confidence,
                        'formats': len(result.formats),
                    },
                )
                if self.STOP_ON_FIRST_VALID and result.stop_fallback:
                    break
            except StrategyError as exc:
                ctx.diagnostics.append({'strategy': strategy.NAME, 'error': str(exc)})
                strategy_log.event('FAIL', ctx.url, details={'error': str(exc)})
                if exc.fatal:
                    raise ExtractionError(str(exc)) from exc
            except Exception as exc:
                ctx.diagnostics.append({'strategy': strategy.NAME, 'error': str(exc)})
                strategy_log.event('FAIL', ctx.url, details={'error': str(exc)})

        if not winners:
            detail = ctx.diagnostics[-1]['error'] if ctx.diagnostics else 'no strategies produced a valid result'
            raise ExtractionError(f"{self.EXTRACTOR_NAME}: {detail}")

        return max(winners, key=lambda item: item.score)

    def score_result(self, ctx: ExtractionContext, result: StrategyResult) -> float:
        score = max(result.confidence, 0.1) * 100.0
        score += min(len(result.formats), 8) * 4.0
        score += 8.0 if result.metadata.get('title') else 0.0
        score += 4.0 if result.metadata.get('thumbnail') else 0.0
        score += 4.0 if result.metadata.get('duration') else 0.0
        score += sum(
            3.0 for fmt in result.formats
            if fmt.stream_type in {StreamType.DIRECT, StreamType.PROGRESSIVE}
        )
        return score

    def build_media_info(self, ctx: ExtractionContext, result: StrategyResult) -> MediaInfo:
        metadata = dict(ctx.metadata)
        metadata.update(result.metadata)
        media_type = MediaType.AUDIO if result.formats and not any(fmt.is_video for fmt in result.formats) else MediaType.VIDEO
        return MediaInfo(
            id=str(metadata.get('id') or self._generate_id(ctx.original_url)),
            title=str(metadata.get('title') or self._extract_title_from_url(ctx.original_url)),
            url=ctx.original_url,
            formats=result.formats,
            media_type=media_type,
            extractor=self.EXTRACTOR_NAME,
            description=metadata.get('description'),
            thumbnail=metadata.get('thumbnail'),
            duration=metadata.get('duration'),
            upload_date=metadata.get('upload_date'),
            uploader=metadata.get('uploader'),
            view_count=metadata.get('view_count'),
            subtitles=metadata.get('subtitles', {}),
            chapters=metadata.get('chapters', []),
        )

    def _validate_result(self, ctx: ExtractionContext, result: StrategyResult) -> None:
        deduped: List[StreamFormat] = []
        seen = set()

        for fmt in result.formats:
            if not fmt.url:
                continue
            fmt.headers = {str(k): str(v) for k, v in fmt.headers.items()}
            fmt.headers.setdefault('Referer', ctx.final_url or ctx.url)
            if fmt.stream_type is None:
                fmt.stream_type = self._detect_stream_type(fmt.url)
            if not fmt.ext:
                fmt.ext = 'mp4'
            key = (fmt.format_id, fmt.url)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(fmt)

        deduped.sort(key=lambda item: item.quality_score, reverse=True)
        if not deduped:
            raise StrategyError('strategy returned no valid formats')
        result.formats = deduped

    def _strategy_chain(self) -> List[Type[ExtractionStrategy]]:
        return [*self.STRATEGIES, *self.FALLBACK_STRATEGIES]

    def _config_mapping(self) -> Dict[str, Any]:
        if isinstance(self.config, dict):
            return self.config

        mapping: Dict[str, Any] = {}
        if hasattr(self.config, 'to_dict'):
            try:
                return dict(self.config.to_dict())
            except Exception:
                pass

        for attribute in dir(self.config):
            if attribute.startswith('_'):
                continue
            value = getattr(self.config, attribute)
            if callable(value):
                continue
            mapping[attribute] = value
        return mapping

    def _run_async(self, coro: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(lambda: asyncio.run(coro))
            return future.result()

    def close(self) -> None:
        """Release any async request resources created by this extractor."""
        try:
            self._run_async(self.request.close())
        except Exception:
            pass

    # ===== Helper Methods =====

    def _fetch_page(self, url: str, headers: Optional[Dict[str, str]] = None) -> str:
        try:
            response = self.session.get(url, headers=headers)
            return response.text
        except Exception as e:
            logger.error(f"Failed to fetch {url}: {e}")
            raise ExtractionError(f"Failed to fetch page: {e}")

    def _fetch_json(self, url: str, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        try:
            response = self.session.get(url, headers=headers)
            return response.json()
        except Exception as e:
            logger.error(f"Failed to fetch JSON from {url}: {e}")
            raise ExtractionError(f"Failed to fetch JSON: {e}")

    async def _fetch_page_async(self, ctx: ExtractionContext, url: Optional[str] = None,
                                headers: Optional[Dict[str, str]] = None) -> str:
        target_url = url or ctx.url
        document = await ctx.request.get_text(target_url, headers=headers)
        ctx.final_url = document.url
        ctx.response_headers = document.headers
        return document.text or ''

    async def _fetch_json_async(self, ctx: ExtractionContext, url: str,
                                headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        document = await ctx.request.get_json(url, headers=headers)
        return document.json_data or {}

    def _generate_id(self, url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()[:12]

    def _make_absolute_url(self, base_url: str, path: str) -> str:
        if path.startswith(('http://', 'https://')):
            return path
        return urljoin(base_url, path)

    def _sanitize_filename(self, filename: str) -> str:
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        if len(filename) > 200:
            filename = filename[:200]
        return filename.strip()

    def _parse_quality(self, quality_str: str) -> Optional[int]:
        quality_map = {
            '4k': 2160, 'uhd': 2160, '2160p': 2160,
            '1440p': 1440, '2k': 1440,
            '1080p': 1080, 'fhd': 1080,
            '720p': 720, 'hd': 720,
            '480p': 480, 'sd': 480,
            '360p': 360, '240p': 240, '144p': 144,
        }
        normalized = quality_str.lower().strip()
        if normalized in quality_map:
            return quality_map[normalized]
        match = re.search(r'(\d+)p?', normalized)
        if match:
            return int(match.group(1))
        return None

    def _create_format(self, url: str, format_id: Optional[str] = None, ext: str = 'mp4',
                       quality: Optional[str] = None, width: Optional[int] = None, height: Optional[int] = None,
                       **kwargs: Any) -> StreamFormat:
        return StreamFormat(
            format_id=format_id or self._generate_id(url),
            url=url, ext=ext, quality=quality,
            width=width, height=height, **kwargs
        )

    def _deduplicate_urls(self, urls: List[str]) -> List[str]:
        seen = set()
        unique: List[str] = []
        for url in urls:
            if not url or url in seen:
                continue
            seen.add(url)
            unique.append(url)
        return unique

    def _deduplicate_formats(self, formats: List[StreamFormat]) -> List[StreamFormat]:
        seen = set()
        unique: List[StreamFormat] = []
        for fmt in formats:
            key = (fmt.url, fmt.quality, fmt.height, fmt.width, fmt.stream_type)
            if not fmt.url or key in seen:
                continue
            seen.add(key)
            unique.append(fmt)
        return unique

    def _detect_stream_type(self, url: str) -> StreamType:
        url_lower = url.lower()
        if '.m3u8' in url_lower or '/hls/' in url_lower:
            return StreamType.HLS
        if '.mpd' in url_lower or '/dash/' in url_lower:
            return StreamType.DASH
        if any(ext in url_lower for ext in ('.mp4', '.m4v', '.webm', '.mkv', '.mov')):
            return StreamType.PROGRESSIVE
        return StreamType.DIRECT

    def _extract_title_from_url(self, url: str) -> str:
        parsed = urlparse(url)
        path_parts = parsed.path.strip('/').split('/')
        for part in reversed(path_parts):
            if part and not part.startswith(('index', 'watch', 'video', 'embed')):
                title = part.replace('-', ' ').replace('_', ' ')
                title = re.sub(r'\.[^.]+$', '', title)
                return title.title()
        return parsed.netloc

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}: {self.EXTRACTOR_NAME}>"
 

BaseExtractor = ExtractorBase


from .registry import ExtractorRegistry, register_extractor, registry
