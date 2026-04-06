"""
Helper utilities for the universal downloader.
"""

import re
import hashlib
from urllib.parse import urlparse, unquote
from typing import Optional


def sanitize_filename(filename: str, max_length: int = 200) -> str:
    """Sanitize filename for filesystem."""
    invalid_chars = '<>:"/\\|?*\n\r\t'
    for char in invalid_chars:
        filename = filename.replace(char, '_')
    while '__' in filename:
        filename = filename.replace('__', '_')
    if len(filename) > max_length:
        filename = filename[:max_length]
    return filename.strip('_ .')


def generate_id(url: str) -> str:
    """Generate a unique ID from URL."""
    return hashlib.md5(url.encode()).hexdigest()[:12]


def extract_title_from_url(url: str) -> str:
    """Extract a human-readable title from URL."""
    parsed = urlparse(url)
    path_parts = parsed.path.strip('/').split('/')
    if path_parts:
        for part in reversed(path_parts):
            if part and not part.startswith(('index', 'watch', 'video', 'embed')):
                title = unquote(part)
                title = title.replace('-', ' ').replace('_', ' ')
                title = re.sub(r'\.[^.]+$', '', title)
                return title.title()
    return parsed.netloc


def format_duration(seconds: Optional[int]) -> str:
    """Format seconds to human-readable duration."""
    if seconds is None:
        return "unknown"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_filesize(size: Optional[int]) -> str:
    """Format bytes to human-readable size."""
    if size is None:
        return "unknown"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if abs(size) < 1024.0:
            return f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}PB"


def parse_quality_string(quality_str: str) -> Optional[int]:
    """Parse quality string to height in pixels."""
    quality_map = {
        '4k': 2160, 'uhd': 2160, '2160p': 2160,
        '1440p': 1440, '2k': 1440,
        '1080p': 1080, 'fhd': 1080, 'full hd': 1080,
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
