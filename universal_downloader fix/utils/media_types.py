"""
media_types.py — Definisi lengkap tipe media, MIME types, dan signatures
"""

from dataclasses import dataclass, field
from typing import Dict, List, Set


@dataclass
class MediaSignature:
    """Signature untuk mengenali tipe media"""
    extensions: List[str]
    mime_types: List[str]
    magic_bytes: List[bytes] = field(default_factory=list)  # file signature


class MediaTypes:
    """
    Database lengkap tipe media yang bisa dideteksi.
    Dipakai oleh semua layer untuk identifikasi.
    """
    
    # ── IMAGES ──
    IMAGE = MediaSignature(
        extensions=[
            '.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg',
            '.ico', '.bmp', '.avif', '.tiff', '.tif', '.heif',
            '.heic', '.jfif', '.pjpeg', '.pjp', '.apng'
        ],
        mime_types=[
            'image/jpeg', 'image/png', 'image/gif', 'image/webp',
            'image/svg+xml', 'image/x-icon', 'image/bmp',
            'image/avif', 'image/tiff', 'image/heif', 'image/heic',
            'image/apng'
        ],
        magic_bytes=[
            b'\xff\xd8\xff',        # JPEG
            b'\x89PNG\r\n\x1a\n',   # PNG
            b'GIF87a', b'GIF89a',   # GIF
            b'RIFF',                 # WebP (RIFF....WEBP)
            b'\x00\x00\x01\x00',    # ICO
            b'BM',                   # BMP
        ]
    )
    
    # ── VIDEO ──
    VIDEO = MediaSignature(
        extensions=[
            '.mp4', '.webm', '.mkv', '.avi', '.mov', '.flv',
            '.wmv', '.m4v', '.3gp', '.ogv', '.ts', '.m2ts',
            '.mts', '.vob', '.f4v'
        ],
        mime_types=[
            'video/mp4', 'video/webm', 'video/x-matroska',
            'video/x-msvideo', 'video/quicktime', 'video/x-flv',
            'video/x-ms-wmv', 'video/3gpp', 'video/ogg',
            'video/mp2t', 'video/x-f4v'
        ],
        magic_bytes=[
            b'\x00\x00\x00\x18ftypmp4',   # MP4
            b'\x00\x00\x00\x1cftyp',      # MP4 variant
            b'\x1a\x45\xdf\xa3',          # WebM/MKV
            b'RIFF',                        # AVI (RIFF....AVI )
            b'\x00\x00\x00\x14ftypqt',    # MOV
            b'FLV\x01',                     # FLV
        ]
    )
    
    # ── STREAMING ──
    STREAMING = MediaSignature(
        extensions=[
            '.m3u8', '.m3u',               # HLS
            '.mpd',                         # DASH
            '.f4m',                         # HDS (Adobe)
            '.ism', '.isml',               # Smooth Streaming
            '.ts',                          # MPEG-TS segments
            '.m4s',                         # DASH segments
            '.cmfv', '.cmfa',              # CMAF
        ],
        mime_types=[
            'application/vnd.apple.mpegurl',       # HLS
            'application/x-mpegurl',               # HLS
            'audio/mpegurl',                        # HLS audio
            'application/dash+xml',                 # DASH
            'video/mp2t',                           # TS segments
            'video/iso.segment',                    # M4S segments
            'application/f4m+xml',                  # HDS
        ]
    )
    
    # ── AUDIO ──
    AUDIO = MediaSignature(
        extensions=[
            '.mp3', '.wav', '.ogg', '.aac', '.flac', '.m4a',
            '.wma', '.opus', '.aiff', '.ape', '.wv', '.mid',
            '.midi', '.ac3', '.dts'
        ],
        mime_types=[
            'audio/mpeg', 'audio/wav', 'audio/ogg', 'audio/aac',
            'audio/flac', 'audio/mp4', 'audio/x-ms-wma',
            'audio/opus', 'audio/aiff', 'audio/midi',
            'audio/ac3', 'audio/vnd.dts'
        ],
        magic_bytes=[
            b'ID3',                 # MP3 (ID3 tag)
            b'\xff\xfb',           # MP3 (sync word)
            b'\xff\xf3',           # MP3 (sync word)
            b'RIFF',               # WAV (RIFF....WAVE)
            b'OggS',               # OGG
            b'fLaC',               # FLAC
        ]
    )
    
    # ── DOCUMENTS ──
    DOCUMENT = MediaSignature(
        extensions=[
            '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt',
            '.pptx', '.odt', '.ods', '.odp', '.rtf', '.txt',
            '.csv', '.epub'
        ],
        mime_types=[
            'application/pdf',
            'application/msword',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.ms-excel',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'application/vnd.ms-powerpoint',
            'application/epub+zip',
        ]
    )
    
    # ── ARCHIVES ──
    ARCHIVE = MediaSignature(
        extensions=[
            '.zip', '.rar', '.7z', '.tar', '.gz', '.bz2',
            '.xz', '.tar.gz', '.tgz', '.tar.bz2'
        ],
        mime_types=[
            'application/zip', 'application/x-rar-compressed',
            'application/x-7z-compressed', 'application/x-tar',
            'application/gzip', 'application/x-bzip2',
        ]
    )
    
    # ── FONTS ──
    FONT = MediaSignature(
        extensions=['.woff', '.woff2', '.ttf', '.otf', '.eot'],
        mime_types=[
            'font/woff', 'font/woff2', 'font/ttf', 'font/otf',
            'application/vnd.ms-fontobject',
        ]
    )
    
    @classmethod
    def all_media_extensions(cls) -> Set[str]:
        """Return semua ekstensi media"""
        all_ext = set()
        for sig in [cls.IMAGE, cls.VIDEO, cls.STREAMING, cls.AUDIO, 
                     cls.DOCUMENT, cls.ARCHIVE, cls.FONT]:
            all_ext.update(sig.extensions)
        return all_ext
    
    @classmethod
    def all_media_mimes(cls) -> Set[str]:
        """Return semua MIME types"""
        all_mimes = set()
        for sig in [cls.IMAGE, cls.VIDEO, cls.STREAMING, cls.AUDIO,
                     cls.DOCUMENT, cls.ARCHIVE, cls.FONT]:
            all_mimes.update(sig.mime_types)
        return all_mimes
    
    @classmethod
    def identify_type(cls, url: str = "", mime: str = "", content_bytes: bytes = b"") -> str:
        """
        Identifikasi tipe media dari URL, MIME type, atau bytes.
        Return: 'image', 'video', 'streaming', 'audio', 'document', 'archive', 'font', 'unknown'
        """
        type_map = {
            'image': cls.IMAGE,
            'video': cls.VIDEO,
            'streaming': cls.STREAMING,
            'audio': cls.AUDIO,
            'document': cls.DOCUMENT,
            'archive': cls.ARCHIVE,
            'font': cls.FONT,
        }
        
        url_lower = url.lower().split('?')[0].split('#')[0]
        mime_lower = mime.lower()
        
        for type_name, signature in type_map.items():
            # Check by extension
            if url_lower:
                for ext in signature.extensions:
                    if url_lower.endswith(ext):
                        return type_name
            
            # Check by MIME
            if mime_lower:
                for m in signature.mime_types:
                    if mime_lower == m or mime_lower.startswith(m.split('/')[0] + '/'):
                        if m == mime_lower:
                            return type_name
            
            # Check by magic bytes
            if content_bytes and signature.magic_bytes:
                for magic in signature.magic_bytes:
                    if content_bytes[:len(magic)] == magic:
                        return type_name
        
        return 'unknown'
    
    @classmethod
    def is_media_url(cls, url: str) -> bool:
        """Cek apakah URL mengarah ke file media"""
        return cls.identify_type(url=url) != 'unknown'
    
    @classmethod
    def is_streaming_url(cls, url: str) -> bool:
        """Cek apakah URL adalah streaming manifest/segment"""
        url_lower = url.lower().split('?')[0]
        return any(url_lower.endswith(ext) for ext in cls.STREAMING.extensions)


# ── VIDEO PLAYER SIGNATURES ──
# Dipakai oleh Layer 8 untuk fingerprint
VIDEO_PLAYERS = {
    'jwplayer': {
        'indicators': ['jwplayer', 'jwplatform', 'cdn.jwplayer.com', 'jwpsrv'],
        'config_pattern': r'jwplayer\s*\(\s*["\'](\w+)["\']\s*\)\s*\.setup\s*\(\s*({.+?})\s*\)',
        'media_keys': ['file', 'sources', 'playlist', 'image'],
    },
    'videojs': {
        'indicators': ['video.js', 'videojs', 'video-js', 'vjs-'],
        'config_pattern': r'videojs\s*\(\s*["\'](\w+)["\']\s*(?:,\s*({.+?}))?\s*\)',
        'media_keys': ['src', 'sources', 'poster'],
    },
    'plyr': {
        'indicators': ['plyr', 'plyr.js', 'plyr.min.js'],
        'config_pattern': r'new\s+Plyr\s*\(\s*["\'](.+?)["\']\s*(?:,\s*({.+?}))?\s*\)',
        'media_keys': ['src'],
    },
    'hlsjs': {
        'indicators': ['hls.js', 'hls.min.js', 'Hls.', 'hls.loadSource'],
        'config_pattern': r'hls\.loadSource\s*\(\s*["\'](.+?)["\']\s*\)',
        'media_keys': [],
    },
    'dashjs': {
        'indicators': ['dash.js', 'dash.all', 'dashjs', 'dash.MediaPlayer'],
        'config_pattern': r'\.initialize\s*\(\s*\w+\s*,\s*["\'](.+?)["\']\s*',
        'media_keys': [],
    },
    'shaka': {
        'indicators': ['shaka-player', 'shaka.Player'],
        'config_pattern': r'\.load\s*\(\s*["\'](.+?)["\']\s*',
        'media_keys': [],
    },
    'flowplayer': {
        'indicators': ['flowplayer'],
        'config_pattern': r'flowplayer\s*\(\s*["\'](.+?)["\']\s*,\s*({.+?})\s*\)',
        'media_keys': ['clip', 'src'],
    },
    'dplayer': {
        'indicators': ['DPlayer', 'dplayer'],
        'config_pattern': r'new\s+DPlayer\s*\(\s*({.+?})\s*\)',
        'media_keys': ['video', 'url', 'pic'],
    },
    'artplayer': {
        'indicators': ['Artplayer', 'artplayer'],
        'config_pattern': r'new\s+Artplayer\s*\(\s*({.+?})\s*\)',
        'media_keys': ['url', 'poster'],
    },
}

# ── CMS / FRAMEWORK SIGNATURES ──
CMS_SIGNATURES = {
    'wordpress': {
        'indicators': ['/wp-content/', '/wp-includes/', 'wp-json', 'wordpress'],
        'media_paths': ['/wp-content/uploads/'],
    },
    'drupal': {
        'indicators': ['/sites/default/files/', 'Drupal', 'drupal.js'],
        'media_paths': ['/sites/default/files/'],
    },
    'joomla': {
        'indicators': ['/media/jui/', '/components/com_', 'Joomla'],
        'media_paths': ['/images/', '/media/'],
    },
    'nextjs': {
        'indicators': ['_next/static', '_next/data', '__NEXT_DATA__', '_next/image'],
        'media_paths': ['/_next/static/media/', '/_next/image'],
    },
    'nuxtjs': {
        'indicators': ['_nuxt/', '__NUXT__'],
        'media_paths': ['/_nuxt/'],
    },
    'react': {
        'indicators': ['__REACT', 'react-root', '_reactRoot', 'data-reactroot'],
        'media_paths': ['/static/media/'],
    },
    'angular': {
        'indicators': ['ng-version', 'ng-app', 'angular'],
        'media_paths': ['/assets/'],
    },
    'vue': {
        'indicators': ['__vue__', 'data-v-', 'vue.js', 'vue.min.js'],
        'media_paths': ['/static/', '/assets/'],
    },
    'laravel': {
        'indicators': ['laravel', 'XSRF-TOKEN', 'laravel_session'],
        'media_paths': ['/storage/', '/public/'],
    },
    'django': {
        'indicators': ['csrfmiddlewaretoken', 'django'],
        'media_paths': ['/media/', '/static/'],
    },
}

# ── CDN SIGNATURES ──
CDN_SIGNATURES = {
    'cloudflare': ['cloudflare', 'cf-ray', 'cf-cache-status', '__cflb'],
    'cloudfront': ['cloudfront.net', 'x-amz-cf-id', 'x-amz-cf-pop'],
    'akamai': ['akamai', 'akamaized.net', 'akadns.net', 'edgekey.net'],
    'fastly': ['fastly', 'x-fastly', 'fastly-io'],
    'bunnycdn': ['b-cdn.net', 'bunnycdn', 'bunny.net'],
    'keycdn': ['kxcdn.com', 'keycdn'],
    'stackpath': ['stackpath', 'stackpathdns'],
    'jsdelivr': ['cdn.jsdelivr.net'],
    'unpkg': ['unpkg.com'],
    'googlecdn': ['googleapis.com', 'gstatic.com', 'ggpht.com'],
    'awss3': ['s3.amazonaws.com', 's3-', '.s3.'],
    'azure_blob': ['blob.core.windows.net'],
    'gcs': ['storage.googleapis.com'],
}

# ── DRM SIGNATURES ──
DRM_SIGNATURES = {
    'widevine': {
        'indicators': ['widevine', 'com.widevine.alpha', 'license', 'wv'],
        'license_pattern': r'(?:license|widevine|wv)[_\-]?(?:url|server|proxy)',
    },
    'fairplay': {
        'indicators': ['fairplay', 'fps', 'com.apple.fps', 'skd://'],
        'license_pattern': r'(?:certificate|license)[_\-]?(?:url|server)',
    },
    'playready': {
        'indicators': ['playready', 'com.microsoft.playready'],
        'license_pattern': r'(?:license|playready)[_\-]?(?:url|server)',
    },
    'clearkey': {
        'indicators': ['clearkey', 'org.w3.clearkey'],
        'license_pattern': r'clearkey',
    },
}