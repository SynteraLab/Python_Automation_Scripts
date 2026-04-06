# player_intelligence/config_extractors.py
"""
Reusable config extraction engine.

Provides structured extraction of player configuration blocks
from HTML/JS content. Each extraction strategy is a callable
that receives raw text and a pattern match, and returns a
ConfigExtractionResult with parsed data and media hints.

Extraction strategies:
    - setup_call:      player.setup({...}) or new Player({...})
    - json_blob:       inline JSON config blocks
    - data_attr_json:  data-* attributes with JSON values
    - window_var:      window.config = {...} assignments
    - sources_array:   sources: [{file: '...'}] arrays
    - embed_url:       URLs extracted from iframe src or similar

All strategies share the same result model and produce
structured MediaHints when media URLs/formats are found.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Sequence

from .models import (
    ConfigExtractionResult,
    ConfigType,
    MediaHints,
    OutputType,
    PlayerConfigPattern,
    PlayerFamily,
    PlayerProfile,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# JS OBJECT PARSING
# ═══════════════════════════════════════════════════════════════

def parse_js_object(raw_text: str) -> dict[str, Any] | None:
    """
    Attempt to parse a JS object literal into a Python dict.

    Strategy layers (tried in order):
    1. Direct JSON parse (works for JSON-safe JS objects).
    2. Relaxed parse — fix common JS-isms:
       - Single-quoted strings → double-quoted
       - Trailing commas removed
       - Unquoted keys quoted
       - JS comments removed
    3. Return None if all strategies fail.

    ADAPTATION POINT: If the host repository has a proper JS
    parser (e.g., pyjsparser, slimit), it should replace
    this function for higher accuracy.
    """
    if not raw_text or not raw_text.strip():
        return None

    text = raw_text.strip()

    # Strategy 1: direct JSON
    result = _try_json_parse(text)
    if result is not None:
        return result

    # Strategy 2: relaxed JS → JSON conversion
    relaxed = _relax_js_to_json(text)
    result = _try_json_parse(relaxed)
    if result is not None:
        return result

    # Strategy 3: aggressive cleanup
    aggressive = _aggressive_js_cleanup(text)
    result = _try_json_parse(aggressive)
    if result is not None:
        return result

    return None


def _try_json_parse(text: str) -> dict[str, Any] | None:
    """Attempt JSON parse, return dict or None."""
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass
    return None


# Patterns for JS → JSON relaxation
_JS_SINGLE_LINE_COMMENT = re.compile(r'//[^\n]*')
_JS_MULTI_LINE_COMMENT = re.compile(r'/\*.*?\*/', re.DOTALL)
_JS_TRAILING_COMMA = re.compile(r',\s*([}\]])')
_JS_UNQUOTED_KEY = re.compile(r'(?<=[\{,])\s*([a-zA-Z_$][a-zA-Z0-9_$]*)\s*:')
_JS_SINGLE_QUOTED_STRING = re.compile(r"(?<!\\)'((?:[^'\\]|\\.)*)'")


def _relax_js_to_json(text: str) -> str:
    """Apply gentle JS → JSON transformations."""
    result = text
    # Remove comments
    result = _JS_SINGLE_LINE_COMMENT.sub('', result)
    result = _JS_MULTI_LINE_COMMENT.sub('', result)
    # Fix single-quoted strings → double-quoted
    result = _JS_SINGLE_QUOTED_STRING.sub(
        lambda m: '"' + m.group(1).replace('"', '\\"') + '"',
        result,
    )
    # Quote unquoted keys
    result = _JS_UNQUOTED_KEY.sub(
        lambda m: ' "' + m.group(1) + '":',
        result,
    )
    # Remove trailing commas
    result = _JS_TRAILING_COMMA.sub(r'\1', result)
    return result


def _aggressive_js_cleanup(text: str) -> str:
    """
    More aggressive cleanup for difficult JS objects.

    Removes function expressions, regex literals, template literals,
    and undefined/null replacements that block JSON parsing.
    """
    result = _relax_js_to_json(text)
    # Replace function expressions with null
    result = re.sub(
        r'function\s*\([^)]*\)\s*\{[^}]*\}',
        'null',
        result,
    )
    # Replace arrow functions with null
    result = re.sub(
        r'(?:\([^)]*\)|[a-zA-Z_$]+)\s*=>\s*(?:\{[^}]*\}|[^,}\]]+)',
        'null',
        result,
    )
    # Replace undefined with null
    result = re.sub(r'\bundefined\b', 'null', result)
    # Replace boolean-like identifiers
    result = re.sub(r'\btrue\b', 'true', result)
    result = re.sub(r'\bfalse\b', 'false', result)
    # Remove trailing commas again after substitutions
    result = _JS_TRAILING_COMMA.sub(r'\1', result)
    return result


# ═══════════════════════════════════════════════════════════════
# BALANCED BRACE EXTRACTION
# ═══════════════════════════════════════════════════════════════

def extract_balanced_braces(text: str, start_pos: int, open_char: str = '{', close_char: str = '}') -> str | None:
    """
    Extract a balanced brace-delimited block from text.

    Starting at `start_pos` (which should point to the opening brace),
    finds the matching closing brace accounting for nesting and
    string literals (both single and double quoted).

    Returns the full block including braces, or None if unbalanced.
    """
    if start_pos >= len(text) or text[start_pos] != open_char:
        return None

    depth = 0
    in_single_quote = False
    in_double_quote = False
    escape_next = False
    i = start_pos

    while i < len(text):
        ch = text[i]

        if escape_next:
            escape_next = False
            i += 1
            continue

        if ch == '\\':
            escape_next = True
            i += 1
            continue

        if ch == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
        elif ch == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
        elif not in_single_quote and not in_double_quote:
            if ch == open_char:
                depth += 1
            elif ch == close_char:
                depth -= 1
                if depth == 0:
                    return text[start_pos:i + 1]

        i += 1

    return None  # Unbalanced


def _find_block_after_pattern(text: str, pattern: re.Pattern[str], open_char: str = '{') -> str | None:
    """
    Find pattern in text, then extract balanced block starting from the
    open_char captured in the first group or found immediately after match.
    """
    m = pattern.search(text)
    if not m:
        return None

    # Try to find the opening brace position
    # If the pattern captured a group, the group text is the open char
    # and its position is m.start(1) or m.start() of that group
    try:
        brace_pos = m.start(1)
    except IndexError:
        brace_pos = m.end()

    # Scan forward for the actual opening char if not exact
    while brace_pos < len(text) and text[brace_pos] != open_char:
        brace_pos += 1
        if brace_pos - m.end() > 50:
            return None  # Too far away, probably wrong

    close_char = '}' if open_char == '{' else ']'
    return extract_balanced_braces(text, brace_pos, open_char, close_char)


# ═══════════════════════════════════════════════════════════════
# MEDIA HINTS EXTRACTION
# ═══════════════════════════════════════════════════════════════

_MEDIA_URL_RE = re.compile(
    r'https?://[^\s"\'<>\}]+\.(?:m3u8|mp4|webm|flv|mpd|mp3|aac|ogg)'
    r'(?:\?[^\s"\'<>\}]*)?',
    re.IGNORECASE,
)

_FORMAT_EXTENSION_MAP: dict[str, OutputType] = {
    '.m3u8': OutputType.HLS,
    '.mp4': OutputType.MP4,
    '.webm': OutputType.WEBM,
    '.flv': OutputType.FLV,
    '.mpd': OutputType.DASH,
    '.mp3': OutputType.AUDIO,
    '.aac': OutputType.AUDIO,
    '.ogg': OutputType.AUDIO,
}

_DRM_INDICATORS = re.compile(
    r'drm|widevine|playready|fairplay|clearkey|license[_\-]?url',
    re.IGNORECASE,
)

_SUBTITLE_INDICATORS = re.compile(
    r'subtitle|caption|track|\.vtt|\.srt|\.ass',
    re.IGNORECASE,
)

_THUMBNAIL_INDICATORS = re.compile(
    r'thumbnail|sprite|preview|\.vtt\b.*thumbnail',
    re.IGNORECASE,
)


def extract_media_hints(parsed_config: dict[str, Any] | None, raw_text: str = "") -> MediaHints:
    """
    Extract media-related hints from a parsed config dict and/or raw text.

    Scans for media URLs, format types, DRM indicators, subtitle
    references, and thumbnail references.
    """
    urls: list[str] = []
    formats: set[OutputType] = set()
    has_drm = False
    has_subtitles = False
    has_thumbnails = False
    extra: dict[str, Any] = {}

    # Scan raw text for URLs regardless of parse success
    scan_text = raw_text
    if parsed_config:
        scan_text += ' ' + json.dumps(parsed_config, default=str)

    for url_match in _MEDIA_URL_RE.finditer(scan_text):
        url = url_match.group(0)
        if url not in urls:
            urls.append(url)
        # Determine format from extension
        for ext, output_type in _FORMAT_EXTENSION_MAP.items():
            if ext.lower() in url.lower():
                formats.add(output_type)
                break

    # Check for DRM
    if _DRM_INDICATORS.search(scan_text):
        has_drm = True

    # Check for subtitles
    if _SUBTITLE_INDICATORS.search(scan_text):
        has_subtitles = True

    # Check for thumbnails
    if _THUMBNAIL_INDICATORS.search(scan_text):
        has_thumbnails = True

    # Extract named fields from parsed config
    if parsed_config:
        _extract_named_fields(parsed_config, urls, formats, extra)

    return MediaHints(
        urls=tuple(urls),
        formats=tuple(sorted(formats, key=lambda f: f.value)),
        has_drm=has_drm,
        has_subtitles=has_subtitles,
        has_thumbnails=has_thumbnails,
        extra=extra,
    )


def _extract_named_fields(
    config: dict[str, Any],
    urls: list[str],
    formats: set[OutputType],
    extra: dict[str, Any],
    depth: int = 0,
) -> None:
    """Recursively extract well-known fields from a config dict."""
    if depth > 5:
        return

    for key, value in config.items():
        key_lower = key.lower()

        # Direct URL fields
        if key_lower in ('file', 'src', 'source', 'url', 'hls', 'dash', 'mp4'):
            if isinstance(value, str) and value.startswith(('http', '//')):
                if value not in urls:
                    urls.append(value)
                # Infer format from key name
                if key_lower == 'hls' or '.m3u8' in value:
                    formats.add(OutputType.HLS)
                elif key_lower == 'dash' or '.mpd' in value:
                    formats.add(OutputType.DASH)
                elif key_lower == 'mp4' or '.mp4' in value:
                    formats.add(OutputType.MP4)

        # Sources array
        elif key_lower in ('sources', 'playlist', 'tracks'):
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        _extract_named_fields(item, urls, formats, extra, depth + 1)
                    elif isinstance(item, str) and item.startswith(('http', '//')):
                        if item not in urls:
                            urls.append(item)

        # Title / poster metadata
        elif key_lower in ('title', 'poster', 'image', 'thumbnail'):
            if isinstance(value, str) and value:
                extra[key_lower] = value

        # Nested objects
        elif isinstance(value, dict):
            _extract_named_fields(value, urls, formats, extra, depth + 1)


# ═══════════════════════════════════════════════════════════════
# EXTRACTION STRATEGY FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def _extract_setup_call(
    html: str,
    pattern: PlayerConfigPattern,
    family: PlayerFamily,
) -> ConfigExtractionResult | None:
    """
    Extract config from a setup/init call pattern.

    Handles patterns like:
        jwplayer('id').setup({...})
        new Clappr.Player({...})
        new DPlayer({...})
        videojs('id', {...})
    """
    raw_block = _find_block_after_pattern(html, pattern.pattern, '{')
    if not raw_block:
        # Try array form
        raw_block = _find_block_after_pattern(html, pattern.pattern, '[')
        if not raw_block:
            return None

    parsed = parse_js_object(raw_block)
    media_hints = extract_media_hints(parsed, raw_block)

    return ConfigExtractionResult(
        config_type=pattern.config_type,
        raw_text=raw_block[:2000],  # Cap storage size
        parsed=parsed,
        source_pattern=pattern.description,
        media_hints=media_hints,
        family=family,
        extraction_error=None if parsed else "JS object parse failed",
    )


def _extract_window_var(
    html: str,
    pattern: PlayerConfigPattern,
    family: PlayerFamily,
) -> ConfigExtractionResult | None:
    """
    Extract config from a window/global variable assignment.

    Handles patterns like:
        window.playerConfig = {...}
        var jwConfig = {...}
        let playerOptions = {...}
    """
    raw_block = _find_block_after_pattern(html, pattern.pattern, '{')
    if not raw_block:
        return None

    parsed = parse_js_object(raw_block)
    media_hints = extract_media_hints(parsed, raw_block)

    return ConfigExtractionResult(
        config_type=ConfigType.WINDOW_VAR,
        raw_text=raw_block[:2000],
        parsed=parsed,
        source_pattern=pattern.description,
        media_hints=media_hints,
        family=family,
        extraction_error=None if parsed else "JS object parse failed",
    )


def _extract_sources_array(
    html: str,
    pattern: PlayerConfigPattern,
    family: PlayerFamily,
) -> ConfigExtractionResult | None:
    """
    Extract a sources/playlist array.

    Handles patterns like:
        sources: [{file: 'url', type: 'hls'}, ...]
        jwplayer().load([{file: 'url'}])
    """
    raw_block = _find_block_after_pattern(html, pattern.pattern, '[')
    if not raw_block:
        return None

    # Wrap in an object for uniform parsing
    wrapped = '{"sources":' + raw_block + '}'
    parsed = parse_js_object(wrapped)
    media_hints = extract_media_hints(parsed, raw_block)

    return ConfigExtractionResult(
        config_type=ConfigType.SOURCES_ARRAY,
        raw_text=raw_block[:2000],
        parsed=parsed,
        source_pattern=pattern.description,
        media_hints=media_hints,
        family=family,
        extraction_error=None if parsed else "Sources array parse failed",
    )


def _extract_data_attr_json(
    html: str,
    pattern: PlayerConfigPattern,
    family: PlayerFamily,
) -> ConfigExtractionResult | None:
    """
    Extract config from a data-* attribute with JSON value.

    Handles patterns like:
        data-setup='{"controls": true, "sources": [...]}'
        data-plyr-config='{"speed": {"selected": 1}}'
    """
    m = pattern.pattern.search(html)
    if not m:
        return None

    try:
        raw_json = m.group(1)
    except IndexError:
        return None

    if not raw_json or not raw_json.strip():
        return None

    # Data attributes are typically proper JSON (HTML-encoded)
    # Unescape HTML entities first
    raw_json = _unescape_html_entities(raw_json)
    parsed = parse_js_object(raw_json)
    media_hints = extract_media_hints(parsed, raw_json)

    return ConfigExtractionResult(
        config_type=ConfigType.DATA_ATTR,
        raw_text=raw_json[:2000],
        parsed=parsed,
        source_pattern=pattern.description,
        media_hints=media_hints,
        family=family,
        extraction_error=None if parsed else "Data attribute JSON parse failed",
    )


def _extract_embed_url(
    html: str,
    pattern: PlayerConfigPattern,
    family: PlayerFamily,
) -> ConfigExtractionResult | None:
    """
    Extract a URL from an embed/iframe src pattern.

    Handles patterns like:
        <iframe src="https://example.com/embed/video123">
    """
    m = pattern.pattern.search(html)
    if not m:
        return None

    try:
        url = m.group(1)
    except IndexError:
        return None

    if not url or not url.strip():
        return None

    url = _unescape_html_entities(url.strip())
    media_hints = extract_media_hints(None, url)

    return ConfigExtractionResult(
        config_type=ConfigType.EMBED_URL,
        raw_text=url[:2000],
        parsed={"url": url},
        source_pattern=pattern.description,
        media_hints=media_hints,
        family=family,
    )


# ═══════════════════════════════════════════════════════════════
# HTML ENTITY HELPER
# ═══════════════════════════════════════════════════════════════

_HTML_ENTITIES: dict[str, str] = {
    '&amp;': '&',
    '&lt;': '<',
    '&gt;': '>',
    '&quot;': '"',
    '&#39;': "'",
    '&apos;': "'",
    '&#x27;': "'",
    '&#x2F;': '/',
    '&#47;': '/',
}


def _unescape_html_entities(text: str) -> str:
    """Unescape common HTML entities in attribute values."""
    result = text
    for entity, char in _HTML_ENTITIES.items():
        result = result.replace(entity, char)
    return result


# ═══════════════════════════════════════════════════════════════
# EXTRACTOR DISPATCH
# ═══════════════════════════════════════════════════════════════

# Maps PlayerConfigPattern.extractor_fn string keys to callables.
# Each callable has the signature:
#   (html: str, pattern: PlayerConfigPattern, family: PlayerFamily)
#   -> ConfigExtractionResult | None

_EXTRACTOR_DISPATCH: dict[
    str,
    Callable[[str, PlayerConfigPattern, PlayerFamily], ConfigExtractionResult | None],
] = {
    "setup_call": _extract_setup_call,
    "window_var": _extract_window_var,
    "sources_array": _extract_sources_array,
    "data_attr_json": _extract_data_attr_json,
    "embed_url": _extract_embed_url,
}


def get_extractor(fn_key: str) -> Callable[..., ConfigExtractionResult | None] | None:
    """Look up an extraction function by its string key."""
    return _EXTRACTOR_DISPATCH.get(fn_key)


def register_extractor(
    fn_key: str,
    fn: Callable[[str, PlayerConfigPattern, PlayerFamily], ConfigExtractionResult | None],
) -> None:
    """
    Register a custom extraction function.

    Allows downstream code to add new extraction strategies
    without modifying this module.
    """
    # ADAPTATION POINT: Host repo may use a different registration
    # mechanism or plugin system for config extractors.
    _EXTRACTOR_DISPATCH[fn_key] = fn


# ═══════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════

def extract_config(
    html: str,
    pattern: PlayerConfigPattern,
    family: PlayerFamily = PlayerFamily.UNKNOWN,
) -> ConfigExtractionResult | None:
    """
    Extract a single config block using a specific pattern.

    Dispatches to the appropriate extraction strategy based on
    the pattern's `extractor_fn` key.

    Returns None if the pattern doesn't match or extraction fails.
    """
    extractor = get_extractor(pattern.extractor_fn)
    if extractor is None:
        logger.warning(
            "No extractor registered for key %r (pattern: %s)",
            pattern.extractor_fn,
            pattern.description,
        )
        return None

    try:
        return extractor(html, pattern, family)
    except Exception as exc:
        logger.debug(
            "Config extraction failed for pattern %r: %s",
            pattern.description,
            exc,
        )
        return ConfigExtractionResult(
            config_type=pattern.config_type,
            raw_text="",
            source_pattern=pattern.description,
            family=family,
            extraction_error=str(exc),
        )


def extract_configs_for_profile(
    html: str,
    profile: PlayerProfile,
) -> list[ConfigExtractionResult]:
    """
    Extract all config blocks matching a profile's config patterns.

    Patterns are tried in priority order (lower number = higher priority).
    All successful extractions are returned, not just the first.

    Returns a list of ConfigExtractionResult objects sorted by priority.
    """
    if not profile.config_patterns:
        return []

    results: list[ConfigExtractionResult] = []
    sorted_patterns = sorted(profile.config_patterns, key=lambda p: p.priority)

    for pattern in sorted_patterns:
        if pattern.matches(html):
            result = extract_config(html, pattern, profile.family)
            if result is not None:
                results.append(result)

    return results


def extract_all_configs(
    html: str,
    profiles: Sequence[PlayerProfile],
    family_filter: PlayerFamily | None = None,
) -> list[ConfigExtractionResult]:
    """
    Extract configs across all given profiles (or a specific family).

    Used by the public API's `extract_player_configs()` function.

    Parameters
    ----------
    html : str
        HTML/JS content to extract from.
    profiles : Sequence[PlayerProfile]
        Profiles whose config patterns to try.
    family_filter : PlayerFamily | None
        If set, only extract for this specific family.

    Returns
    -------
    list[ConfigExtractionResult]
        All successful extractions across all matched profiles.
    """
    results: list[ConfigExtractionResult] = []

    for profile in profiles:
        if family_filter is not None and profile.family != family_filter:
            continue
        profile_results = extract_configs_for_profile(html, profile)
        results.extend(profile_results)

    return results