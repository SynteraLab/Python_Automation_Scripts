# pyright: reportOptionalMemberAccess=false
"""
Utility helpers for TUI formatting, sizing, and data processing.
These are pure functions with no UI side effects (except debug logging).
"""

import re
import logging
from typing import Optional, List

from models.media import StreamType

logger = logging.getLogger(__name__)


def compact_error_message(message: str, limit: int = 120) -> str:
    """Compact error message for display, preserving Cloudflare messages."""
    text = (message or '').strip()
    if not text:
        return ''
    if 'Cloudflare' in text or '\n' in text:
        return text
    return text[:limit]


def human_size(num_bytes: float) -> str:
    """Format bytes into human-readable size."""
    size = float(num_bytes)
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_idx = 0
    while size >= 1024 and unit_idx < len(units) - 1:
        size /= 1024.0
        unit_idx += 1

    if unit_idx == 0:
        return f"{int(size)}{units[unit_idx]}"
    return f"{size:.1f}{units[unit_idx]}"


def estimated_filesize_bytes(fmt, duration_seconds: Optional[int]) -> Optional[int]:
    """Estimate filesize from bitrate + duration when exact size is unavailable."""
    if fmt.filesize and fmt.filesize > 0:
        return int(fmt.filesize)

    if not duration_seconds or duration_seconds <= 0:
        return None

    if not fmt.bitrate or fmt.bitrate <= 0:
        return None

    # bitrate in kbps -> bytes
    return int((fmt.bitrate * 1000 / 8) * duration_seconds)


def guess_bitrate_kbps(fmt) -> Optional[int]:
    """Try to derive bitrate when extractor doesn't set it explicitly."""
    if fmt.bitrate and fmt.bitrate > 0:
        return int(fmt.bitrate)

    candidates = [fmt.format_id or "", fmt.quality or "", fmt.label or ""]
    for text in candidates:
        match = re.search(r"(\d{3,5})\s*kbps", text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))

    # Common pattern from some extractors: hls-3611-0 (bitrate ~3611 kbps)
    match = re.search(r"(?:^|[-_])hls-(\d{3,5})(?:[-_]|$)", (fmt.format_id or "").lower())
    if match:
        return int(match.group(1))

    return None


def probe_hls_duration_seconds(fmt, timeout_seconds: int = 12) -> Optional[int]:
    """Probe HLS playlist and read total duration from EXTINF entries."""
    if fmt.stream_type != StreamType.HLS:
        return None

    try:
        # Inline imports preserved for optional dependency safety
        import requests
        from extractors.hls import HLSParser

        headers = dict(fmt.headers or {})
        headers.setdefault("User-Agent", "Mozilla/5.0")

        response = requests.get(fmt.url, headers=headers, timeout=timeout_seconds)
        response.raise_for_status()
        parser = HLSParser(fmt.url, response.text)

        if parser.is_master_playlist():
            variants = parser.parse_master_playlist()
            video_variants = [
                v for v in variants
                if v.get('is_video', True) and isinstance(v.get('url'), str)
            ]
            if video_variants:
                if fmt.height:
                    chosen = min(
                        video_variants,
                        key=lambda v: abs((v.get('height') or fmt.height) - fmt.height),
                    )
                else:
                    chosen = max(
                        video_variants,
                        key=lambda v: (v.get('height') or 0, v.get('bitrate') or 0),
                    )

                variant_url = chosen.get('url')
                if isinstance(variant_url, str) and variant_url:
                    response = requests.get(variant_url, headers=headers, timeout=timeout_seconds)
                    response.raise_for_status()
                    parser = HLSParser(variant_url, response.text)

        media_info = parser.parse_media_playlist()
        total_duration = media_info.get('total_duration')
        if total_duration and total_duration > 0:
            return int(total_duration)
    except Exception as e:
        logger.debug(f"HLS duration probe failed for {fmt.format_id}: {e}")

    return None


def resolve_duration_for_table(formats, duration_seconds: Optional[int]) -> Optional[int]:
    """Resolve duration from metadata; fallback to HLS probing."""
    if duration_seconds and duration_seconds > 0:
        return int(duration_seconds)

    for fmt in formats:
        if fmt.stream_type != StreamType.HLS:
            continue
        probed = probe_hls_duration_seconds(fmt)
        if probed and probed > 0:
            return probed

    return None


def format_size_cell(fmt, duration_seconds: Optional[int], bitrate_kbps: Optional[int] = None) -> str:
    """Return displayable size text for format table."""
    if fmt.filesize and fmt.filesize > 0:
        return human_size(fmt.filesize)

    effective_bitrate = bitrate_kbps if bitrate_kbps and bitrate_kbps > 0 else fmt.bitrate
    if effective_bitrate:
        # Temporary duck-typed object for estimator helper (expects fmt.bitrate)
        class _Tmp:
            bitrate = effective_bitrate
            filesize = None

        estimate = estimated_filesize_bytes(_Tmp, duration_seconds)
    else:
        estimate = None

    if estimate:
        return f"~{human_size(estimate)}"

    # For HLS/DASH we often only know bitrate, not exact total file size.
    if effective_bitrate and effective_bitrate > 0:
        return f"~{effective_bitrate}kbps"

    return "unknown"


def ordered_formats(formats) -> List:
    """Sort formats for user-facing selection."""
    stream_rank = {
        StreamType.DIRECT: 3,
        StreamType.PROGRESSIVE: 3,
        StreamType.HLS: 2,
        StreamType.DASH: 1,
    }
    return sorted(
        list(formats),
        key=lambda f: (f.quality_score, stream_rank.get(f.stream_type, 0)),
        reverse=True,
    )


def parse_selection_ranges(selection: str, max_value: int) -> List[int]:
    """Parse selection string like '1,3-5' or 'all' into sorted indices (1-based)."""
    normalized = selection.strip().lower()
    if normalized in {'all', '*'}:
        return list(range(1, max_value + 1))

    picked = set()
    for part in selection.split(','):
        token = part.strip()
        if not token:
            continue

        if '-' in token:
            start_str, end_str = token.split('-', 1)
            if not start_str.strip().isdigit() or not end_str.strip().isdigit():
                raise ValueError(f"Invalid range token: {token}")

            start = int(start_str.strip())
            end = int(end_str.strip())
            if start > end:
                start, end = end, start

            if start < 1 or end > max_value:
                raise ValueError(f"Range out of bounds: {token}")

            for idx in range(start, end + 1):
                picked.add(idx)
            continue

        if not token.isdigit():
            raise ValueError(f"Invalid token: {token}")

        idx = int(token)
        if idx < 1 or idx > max_value:
            raise ValueError(f"Index out of bounds: {idx}")
        picked.add(idx)

    if not picked:
        raise ValueError("No valid selection")

    return sorted(picked)