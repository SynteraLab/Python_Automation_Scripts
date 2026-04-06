# Universal Media Downloader

A modular, extensible universal video downloader with **JWPlayer auto-detection**, HLS/DASH support, and plugin architecture.

## Features

- **Social Media Extractor** — Direct extractor for YouTube, TikTok, Instagram, Facebook, X/Twitter, Reddit, Twitch, SoundCloud, Vimeo, and more
- **Direct yt-dlp Social Download Mode** — Social/creator URLs automatically download through `yt-dlp` for better reliability on YouTube, TikTok, Instagram, and similar platforms
- **JWPlayer Universal Extractor** — Automatically detects and extracts from any site using JWPlayer 7/8
- **HLS Stream Download** — M3U8 playlist parsing with concurrent segment download
- **DASH Support** — Via FFmpeg integration
- **Generic HTML Extractor** — Finds `<video>` tags, meta tags, script-embedded URLs
- **Advanced Browser Extractor** — Playwright-based for JS-rendered pages
- **Async Downloads** — Fast concurrent downloading with progress bars
- **Cookie Support** — Load from file or browser (Chrome, Firefox, Edge, Brave, Opera)
- **Proxy Support** — HTTP, HTTPS, SOCKS5
- **Plugin Architecture** — Drop `.py` files into `extractors/plugins/`
- **Colored Terminal UI** — Progress bars with speed, ETA, and spinner

## Installation

```bash
# Clone/download the project
cd universal_downloader

# Install dependencies
pip install -r requirements.txt

# Install FFmpeg (required for HLS/DASH/merging)
brew install ffmpeg          # macOS
# sudo apt install ffmpeg    # Linux
```

## Quick Start

```bash
# Download a video
python main.py download "https://example.com/video"

# Download from social media
python main.py download "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
python main.py download "https://www.tiktok.com/@example/video/1234567890"
python main.py download --cookies-from-browser chrome "https://www.instagram.com/reel/ABC123/"
# Social URLs auto-switch to direct yt-dlp download mode for better stability

# Download with specific quality
python main.py download -q 720p "https://example.com/video"

# Force JWPlayer extractor
python main.py download --force-jwplayer "https://example.com/page-with-jwplayer"

# Download HLS stream
python main.py download "https://example.com/stream/index.m3u8"

# List available formats
python main.py list-formats "https://example.com/video"

# Get video info
python main.py info "https://example.com/video"

# Batch download from file
python main.py batch urls.txt

# List all extractors
python main.py list-extractors

# Use browser for JS-heavy sites (requires Playwright)
pip install playwright && playwright install chromium
python main.py download --use-browser "https://js-heavy-site.com/video"
```

## EroMe Album CLI (Photo + Video)

Perintah khusus EroMe untuk extract album berisi foto dan video, dengan mode select atau download semua.

```bash
# List media di album (tanpa download)
python main.py erome "https://www.erome.com/a/ALBUM_ID" --list-only

# Download pilihan item (nomor/range)
python main.py erome "https://www.erome.com/a/ALBUM_ID" --select "1,3-5"

# Alias select
python main.py erome "https://www.erome.com/a/ALBUM_ID" --pick "2,4"

# Download semua item
python main.py erome "https://www.erome.com/a/ALBUM_ID" --all

# Alias download semua
python main.py erome "https://www.erome.com/a/ALBUM_ID" --download-all

# Filter tipe media
python main.py erome "https://www.erome.com/a/ALBUM_ID" --type video --all
python main.py erome "https://www.erome.com/a/ALBUM_ID" --type photo --all
```

## Supported Sites

| Type | Description |
|------|-------------|
| **JWPlayer** | Any site using JWPlayer (auto-detected) |
| **Social Media** | YouTube, TikTok, Instagram, Facebook, X/Twitter, Threads, Reddit, Twitch, Vimeo, SoundCloud, and more |
| **HLS** | Direct `.m3u8` URLs |
| **DASH** | Direct `.mpd` URLs (via FFmpeg) |
| **Generic** | Sites with `<video>` tags, Open Graph meta, script-embedded URLs |
| **NontonDrama** | `tv*.nontondrama.my` episodes (playeriframe/cloud HLS) |
| **Advanced** | JS-rendered pages (requires Playwright) |
| **JW Platform** | `cdn.jwplayer.com` hosted videos |

## Command Reference

### `download` (alias: `dl`)
```
python main.py download [OPTIONS] URL

Options:
  -o, --output          Output filename or directory
  -q, --quality         Quality: best, worst, 720p, 1080p, etc.
  -f, --format          Specific format ID
  --audio-only          Download audio only
  --no-merge            Don't merge separate video/audio
  --proxy               Proxy URL
  --cookies             Path to cookies.txt
  --cookies-from-browser  Load cookies from browser
  --use-browser         Use Playwright for JS pages
  --force-jwplayer      Force JWPlayer extractor
  --force-generic       Force generic extractor
```

### `list-formats` (alias: `lf`)
```
python main.py list-formats [--json] URL
```

### `info`
```
python main.py info [--json] URL
```

### `batch`
```
python main.py batch [OPTIONS] FILE

Options:
  -q, --quality         Quality preference
  -o, --output-dir      Output directory
  --parallel            Number of parallel downloads
```

## Configuration

Config file locations (auto-detected):
- `./config.yaml`
- `./config.json`
- `~/.universal_downloader/config.yaml`

Example `config.yaml`:
```yaml
download:
  output_dir: ./downloads
  output_template: "%(title)s_%(resolution)s.%(ext)s"
  max_concurrent: 4
  max_retries: 5

extractor:
  user_agent: "Mozilla/5.0 ..."
  jwplayer_fallback: true

proxy:
  http: http://proxy:8080

log_level: WARNING
ffmpeg_path: ffmpeg
```

Environment variables:
- `UNIDOWN_OUTPUT_DIR` — Output directory
- `UNIDOWN_PROXY_HTTP` — HTTP proxy
- `UNIDOWN_LOG_LEVEL` — Log level
- `UNIDOWN_FFMPEG_PATH` — FFmpeg path
- `UNIDOWN_USER_AGENT` — User agent

## Writing Plugins

Create a `.py` file in `extractors/plugins/`:

```python
from extractors.base import ExtractorBase, register_extractor
from models.media import MediaInfo, StreamFormat, MediaType

@register_extractor()
class MySiteExtractor(ExtractorBase):
    EXTRACTOR_NAME = "mysite"
    EXTRACTOR_DESCRIPTION = "Extractor for mysite.com"
    URL_PATTERNS = [r'https?://(?:www\.)?mysite\.com/.+']

    def extract(self, url):
        html = self._fetch_page(url)
        # ... parse and return MediaInfo
```

## Project Structure

```
universal_downloader/
├── main.py                 # Entry point
├── cli.py                  # CLI interface
├── config.py               # Configuration
├── core/
│   ├── downloader.py       # Download engine
│   └── merger.py           # FFmpeg integration
├── extractors/
│   ├── base.py             # Base class + registry
│   ├── jwplayer.py         # ★ JWPlayer universal extractor
│   ├── generic.py          # Generic HTML extractor
│   ├── hls.py              # HLS stream extractor
│   ├── advanced.py         # Playwright-based extractor
│   └── plugins/            # Custom plugins
├── models/
│   └── media.py            # Data models
├── utils/
│   ├── network.py          # HTTP sessions
│   ├── parser.py           # HTML/JS parsing
│   ├── progress.py         # Progress bars
│   └── helpers.py          # Utilities
├── requirements.txt
└── README.md
```

## License

MIT
# EKSEKUSI
cd /path/ke/folder/baru/universal_downloader
source venv/bin/activate
python main.py

# BUAT DOWNLOAD SUPJAV
F12 kemudian network dan ketikan "turbovidhls" pada filter (ini harus di play dulu videonya)

# ===== MAINTENANCE =====

Yang perlu dilakukan rutin:
# Update yt-dlp (lakukan setiap 1-2 minggu)
pip install -U yt-dlp

# CLI mode (tanpa menu):
python main.py download "URL"
python main.py download -q 720p "URL"
python main.py list-formats "URL"
python main.py info "URL"
python main.py batch urls.txt

# Kalau ada error di kemudian hari:

Custom extractor error → kemungkinan situs ubah struktur, perlu update kode
yt-dlp error → jalankan pip install -U yt-dlp
Connection refused → ISP blokir, nyalakan WARP
Cloudflare challenge → buka situs di Chrome dulu, pakai --cookies-from-browser chrome

# File penting jangan dihapus:

Folder venv/ — semua library ada di sini
Folder downloads/ — hasil download
File ~/.universal_downloader/history.db — riwayat download

python3 -m venv venv
source venv/bin/activate