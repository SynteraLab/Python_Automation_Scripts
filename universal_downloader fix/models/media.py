"""
Media data models for the universal downloader.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum
import json


class MediaType(Enum):
    """Enumeration of supported media types."""
    VIDEO = "video"
    AUDIO = "audio"
    COMBINED = "combined"
    PLAYLIST = "playlist"
    UNKNOWN = "unknown"


class StreamType(Enum):
    """Enumeration of stream types."""
    DIRECT = "direct"
    HLS = "hls"
    DASH = "dash"
    PROGRESSIVE = "progressive"


@dataclass
class StreamFormat:
    """Represents a single downloadable stream format."""
    format_id: str
    url: str
    ext: str = "mp4"
    quality: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[int] = None
    vcodec: Optional[str] = None
    acodec: Optional[str] = None
    bitrate: Optional[int] = None
    filesize: Optional[int] = None
    stream_type: StreamType = StreamType.DIRECT
    is_video: bool = True
    is_audio: bool = True
    headers: Dict[str, str] = field(default_factory=dict)
    cookies: Dict[str, str] = field(default_factory=dict)
    label: Optional[str] = None  # Human-readable label like "720p HD"

    @property
    def resolution(self) -> str:
        """Get resolution string."""
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return self.quality or "unknown"

    @property
    def format_note(self) -> str:
        """Get format description."""
        parts = []
        if self.label:
            parts.append(self.label)
        elif self.resolution != "unknown":
            parts.append(self.resolution)
        if self.fps:
            parts.append(f"{self.fps}fps")
        if self.vcodec:
            parts.append(self.vcodec)
        if self.acodec:
            parts.append(self.acodec)
        if self.bitrate:
            parts.append(f"{self.bitrate}kbps")
        if self.filesize:
            size_mb = self.filesize / 1024 / 1024
            parts.append(f"{size_mb:.1f}MB")
        return " | ".join(parts) if parts else "unknown"

    @property
    def quality_score(self) -> int:
        """Numeric quality score for sorting."""
        score = 0
        if self.height:
            score += self.height * 1000
        if self.width:
            score += self.width
        if self.bitrate:
            score += self.bitrate
        if self.fps:
            score += self.fps * 10
        return score

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'format_id': self.format_id,
            'url': self.url,
            'ext': self.ext,
            'quality': self.quality,
            'label': self.label,
            'width': self.width,
            'height': self.height,
            'fps': self.fps,
            'vcodec': self.vcodec,
            'acodec': self.acodec,
            'bitrate': self.bitrate,
            'filesize': self.filesize,
            'stream_type': self.stream_type.value,
            'is_video': self.is_video,
            'is_audio': self.is_audio,
            'resolution': self.resolution,
            'format_note': self.format_note,
        }


@dataclass
class MediaInfo:
    """Contains complete information about extracted media."""
    id: str
    title: str
    url: str
    formats: List[StreamFormat] = field(default_factory=list)
    media_type: MediaType = MediaType.VIDEO
    extractor: str = "generic"

    # Metadata
    description: Optional[str] = None
    thumbnail: Optional[str] = None
    duration: Optional[int] = None
    upload_date: Optional[str] = None
    uploader: Optional[str] = None
    view_count: Optional[int] = None

    # Additional data
    subtitles: Dict[str, List[Dict]] = field(default_factory=dict)
    chapters: List[Dict] = field(default_factory=list)
    playlist_index: Optional[int] = None
    playlist_count: Optional[int] = None

    @property
    def best_format(self) -> Optional[StreamFormat]:
        """Get the best quality format."""
        if not self.formats:
            return None
        return max(self.formats, key=lambda f: (f.quality_score, self._speed_score(f)))

    @property
    def worst_format(self) -> Optional[StreamFormat]:
        """Get the worst quality format."""
        if not self.formats:
            return None
        return min(self.formats, key=lambda f: f.quality_score)

    def get_format_by_quality(self, quality: str) -> Optional[StreamFormat]:
        """Get format by quality string (e.g., '720p', '1080p')."""
        try:
            height = int(quality.lower().replace('p', ''))
        except ValueError:
            return None

        # Exact match
        exact = [fmt for fmt in self.formats if fmt.height == height]
        if exact:
            return max(exact, key=lambda f: (f.quality_score, self._speed_score(f)))

        # Closest match (prefer higher)
        candidates = sorted(
            self.formats,
            key=lambda f: (abs((f.height or 0) - height), -self._speed_score(f))
        )
        return candidates[0] if candidates else None

    @staticmethod
    def _speed_score(fmt: StreamFormat) -> int:
        """
        Prefer stream types that usually download faster at the same resolution.
        This does not reduce quality; it only breaks ties between equal-quality formats.
        """
        ranks = {
            StreamType.DIRECT: 30,
            StreamType.PROGRESSIVE: 30,
            StreamType.HLS: 20,
            StreamType.DASH: 10,
        }
        return ranks.get(fmt.stream_type, 0)

    def get_video_formats(self) -> List[StreamFormat]:
        """Get all video-only formats."""
        return [f for f in self.formats if f.is_video and not f.is_audio]

    def get_audio_formats(self) -> List[StreamFormat]:
        """Get all audio-only formats."""
        return [f for f in self.formats if f.is_audio and not f.is_video]

    def get_combined_formats(self) -> List[StreamFormat]:
        """Get all combined video+audio formats."""
        return [f for f in self.formats if f.is_video and f.is_audio]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'id': self.id,
            'title': self.title,
            'url': self.url,
            'formats': [f.to_dict() for f in self.formats],
            'media_type': self.media_type.value,
            'extractor': self.extractor,
            'description': self.description,
            'thumbnail': self.thumbnail,
            'duration': self.duration,
            'upload_date': self.upload_date,
            'uploader': self.uploader,
            'view_count': self.view_count,
        }

    def to_json(self, indent: int = 2) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


@dataclass
class DownloadTask:
    """Represents a download task."""
    media_info: MediaInfo
    selected_format: StreamFormat
    output_path: str
    audio_format: Optional[StreamFormat] = None
    status: str = "pending"
    progress: float = 0.0
    speed: float = 0.0
    eta: Optional[int] = None
    error: Optional[str] = None

    @property
    def needs_merge(self) -> bool:
        """Check if video and audio need to be merged."""
        return self.audio_format is not None


@dataclass
class PlaylistInfo:
    """Represents a playlist containing multiple media items."""
    id: str
    title: str
    url: str
    entries: List[MediaInfo] = field(default_factory=list)
    extractor: str = "generic"
    description: Optional[str] = None
    thumbnail: Optional[str] = None
    uploader: Optional[str] = None

    @property
    def count(self) -> int:
        """Get number of entries."""
        return len(self.entries)
