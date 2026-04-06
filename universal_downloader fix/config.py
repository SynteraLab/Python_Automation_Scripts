"""
Configuration management for the universal downloader.
"""

import os
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import logging

logger = logging.getLogger(__name__)

# Try to import yaml, fallback gracefully
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    yaml = None
    YAML_AVAILABLE = False


@dataclass
class ProxyConfig:
    """Proxy configuration."""
    http: Optional[str] = None
    https: Optional[str] = None
    socks5: Optional[str] = None

    def to_dict(self) -> Dict[str, str]:
        """Convert to requests-compatible proxy dict."""
        proxies = {}
        if self.http:
            proxies['http'] = self.http
        if self.https:
            proxies['https'] = self.https
        if self.socks5:
            proxies['http'] = self.socks5
            proxies['https'] = self.socks5
        return proxies


@dataclass
class DownloadConfig:
    """Download-related configuration."""
    output_dir: str = str(Path.home() / "Downloads")
    output_template: str = "%(title)s_%(resolution)s_%(extractor)s.%(ext)s"
    max_concurrent: int = 8
    chunk_size: int = 1024 * 1024  # 1MB
    max_retries: int = 5
    retry_delay: float = 1.0
    timeout: int = 60
    rate_limit: Optional[int] = None  # bytes per second
    overwrite: bool = False
    keep_fragments: bool = False
    # Aria2c accelerator
    use_aria2: bool = True   # Auto-use aria2c if available
    aria2_path: str = "aria2c"
    aria2_connections: int = 16  # Connections per server


@dataclass
class ExtractorConfig:
    """Extractor-related configuration."""
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
    use_browser: bool = False
    browser_type: str = "chromium"  # chromium, firefox, webkit
    headless: bool = True
    plugin_dirs: List[str] = field(default_factory=lambda: ["./extractors/plugins"])
    upgraded_dirs: List[str] = field(default_factory=lambda: ["./extractors/upgraded"])
    jwplayer_fallback: bool = True  # Auto-detect JWPlayer on generic pages
    enable_label_mapping_v2: bool = True
    enable_player_db_v2: bool = True
    label_confidence_threshold: float = 0.55
    ambiguous_label_policy: str = "keep_candidates"
    site_label_overrides_path: Optional[str] = None
    debug_resolution_trace: bool = False
    enable_candidate_reporting: bool = True
    debug: bool = False
    save_debug_html: bool = False
    save_debug_json: bool = False


@dataclass
class Config:
    """Main configuration class."""
    download: DownloadConfig = field(default_factory=DownloadConfig)
    extractor: ExtractorConfig = field(default_factory=ExtractorConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)

    # Logging
    log_level: str = "WARNING"
    log_file: Optional[str] = None

    # Cookies
    cookies_file: Optional[str] = None
    cookies_from_browser: Optional[str] = None

    # FFmpeg
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"

    # Quality preferences
    preferred_quality: str = "best"
    prefer_free_formats: bool = False

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> 'Config':
        """Load configuration from file."""
        config = cls()

        default_paths = [
            Path("./config.yaml"),
            Path("./config.yml"),
            Path("./config.json"),
            Path.home() / ".universal_downloader" / "config.yaml",
            Path.home() / ".universal_downloader" / "config.json",
        ]

        if config_path:
            paths_to_try = [Path(config_path)]
        else:
            paths_to_try = default_paths

        for path in paths_to_try:
            if path.exists():
                logger.info(f"Loading config from: {path}")
                try:
                    config = cls._load_from_file(path)
                except Exception as e:
                    logger.warning(f"Failed to load config from {path}: {e}")
                break

        config = cls._apply_env_overrides(config)
        return config

    @classmethod
    def _load_from_file(cls, path: Path) -> 'Config':
        """Load config from file."""
        with open(path, 'r') as f:
            if path.suffix in ['.yaml', '.yml']:
                if not YAML_AVAILABLE:
                    logger.warning("PyYAML not installed, skipping YAML config")
                    return cls()
                if yaml is None:
                    return cls()
                yaml_module = yaml
                data = yaml_module.safe_load(f)
            else:
                data = json.load(f)

        return cls._from_dict(data or {})

    @classmethod
    def _from_dict(cls, data: Dict[str, Any]) -> 'Config':
        """Create config from dictionary."""
        config = cls()

        if 'download' in data:
            for key, value in data['download'].items():
                if hasattr(config.download, key):
                    setattr(config.download, key, value)

        if 'extractor' in data:
            for key, value in data['extractor'].items():
                if hasattr(config.extractor, key):
                    setattr(config.extractor, key, value)

        if 'proxy' in data:
            for key, value in data['proxy'].items():
                if hasattr(config.proxy, key):
                    setattr(config.proxy, key, value)

        for key in ['log_level', 'log_file', 'cookies_file', 'cookies_from_browser',
                     'ffmpeg_path', 'ffprobe_path', 'preferred_quality', 'prefer_free_formats']:
            if key in data:
                setattr(config, key, data[key])

        return config

    @classmethod
    def _apply_env_overrides(cls, config: 'Config') -> 'Config':
        """Apply environment variable overrides."""
        env_mappings = {
            'UNIDOWN_OUTPUT_DIR': ('download', 'output_dir'),
            'UNIDOWN_PROXY_HTTP': ('proxy', 'http'),
            'UNIDOWN_PROXY_HTTPS': ('proxy', 'https'),
            'UNIDOWN_LOG_LEVEL': (None, 'log_level'),
            'UNIDOWN_FFMPEG_PATH': (None, 'ffmpeg_path'),
            'UNIDOWN_USER_AGENT': ('extractor', 'user_agent'),
        }

        for env_var, (section, attr) in env_mappings.items():
            value = os.environ.get(env_var)
            if value:
                if section:
                    setattr(getattr(config, section), attr, value)
                else:
                    setattr(config, attr, value)

        return config

    def save(self, path: str) -> None:
        """Save configuration to file."""
        data = self.to_dict()
        output_path: Path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w') as f:
            if output_path.suffix in ['.yaml', '.yml'] and YAML_AVAILABLE and yaml is not None:
                yaml_module = yaml
                yaml_module.dump(data, f, default_flow_style=False)
            else:
                json.dump(data, f, indent=2)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'download': {
                'output_dir': self.download.output_dir,
                'output_template': self.download.output_template,
                'max_concurrent': self.download.max_concurrent,
                'chunk_size': self.download.chunk_size,
                'max_retries': self.download.max_retries,
                'retry_delay': self.download.retry_delay,
                'timeout': self.download.timeout,
                'rate_limit': self.download.rate_limit,
            },
            'extractor': {
                'user_agent': self.extractor.user_agent,
                'use_browser': self.extractor.use_browser,
                'browser_type': self.extractor.browser_type,
                'headless': self.extractor.headless,
                'plugin_dirs': self.extractor.plugin_dirs,
                'upgraded_dirs': self.extractor.upgraded_dirs,
                'enable_label_mapping_v2': self.extractor.enable_label_mapping_v2,
                'enable_player_db_v2': self.extractor.enable_player_db_v2,
                'label_confidence_threshold': self.extractor.label_confidence_threshold,
                'ambiguous_label_policy': self.extractor.ambiguous_label_policy,
                'site_label_overrides_path': self.extractor.site_label_overrides_path,
                'debug_resolution_trace': self.extractor.debug_resolution_trace,
                'enable_candidate_reporting': self.extractor.enable_candidate_reporting,
                'debug': self.extractor.debug,
                'save_debug_html': self.extractor.save_debug_html,
                'save_debug_json': self.extractor.save_debug_json,
            },
            'proxy': {
                'http': self.proxy.http,
                'https': self.proxy.https,
                'socks5': self.proxy.socks5,
            },
            'log_level': self.log_level,
            'log_file': self.log_file,
            'cookies_file': self.cookies_file,
            'ffmpeg_path': self.ffmpeg_path,
            'preferred_quality': self.preferred_quality,
        }


@dataclass
class BrowserConfig:
    """Browser configuration for diagnostic tooling."""

    headless: bool = True
    timeout: int = 30_000
    navigation_timeout: int = 60_000
    viewport_width: int = 1920
    viewport_height: int = 1080
    locale: str = "en-US"
    timezone: str = "America/New_York"
    stealth_mode: bool = True
    random_delay_min: float = 0.5
    random_delay_max: float = 2.0
    user_agents: List[str] = field(default_factory=lambda: [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ])


@dataclass
class ScanConfig:
    """Scan configuration for diagnostic tooling."""

    max_total_time: int = 300
    max_layer_time: int = 30
    max_network_wait: int = 15
    max_js_files: int = 50
    max_js_file_size: int = 5_000_000
    ast_parsing: bool = True
    auto_scroll: bool = True
    max_scrolls: int = 20
    scroll_delay: float = 1.0
    auto_click_play: bool = True
    auto_close_popups: bool = True
    auto_accept_cookies: bool = True
    auto_click_tabs: bool = True
    max_download_sample: int = 5_000_000
    max_crawl_depth: int = 2
    capture_request_headers: bool = True
    capture_response_headers: bool = True
    capture_response_body: bool = True
    max_response_body_size: int = 1_000_000
    probe_found_apis: bool = True
    max_api_probes: int = 20
    multi_server_detection: bool = True
    max_servers_to_probe: int = 15
    server_switch_delay: float = 1.5
    server_network_wait: float = 5.0
    probe_embed_iframes: bool = True


@dataclass
class ReportConfig:
    """Report configuration for diagnostic tooling."""

    output_dir: str = "diagnostic_output"
    report_format: str = "json"
    include_curl_commands: bool = True
    include_sample_responses: bool = True
    include_extraction_strategy: bool = True
    include_session_flow: bool = True
    max_sample_response_size: int = 10_000
    take_screenshots: bool = True
    screenshot_format: str = "png"


@dataclass
class DiagnosticConfig:
    """Master configuration used by the diagnostic subsystem."""

    browser: BrowserConfig = field(default_factory=BrowserConfig)
    scan: ScanConfig = field(default_factory=ScanConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    proxy: Optional[str] = None
    enabled_layers: Dict[str, bool] = field(default_factory=lambda: {
        "layer_01_static": True,
        "layer_02_dynamic": True,
        "layer_03_js_ast": True,
        "layer_04_api_probe": True,
        "layer_05_streaming": True,
        "layer_06_websocket": True,
        "layer_07_service_worker": True,
        "layer_08_infrastructure": True,
        "layer_09_auth_flow": True,
        "layer_10_dom_mutation": True,
        "layer_11_multi_server": True,
    })

    def ensure_output_dir(self) -> None:
        """Create the diagnostic output directory if needed."""

        Path(self.report.output_dir).mkdir(parents=True, exist_ok=True)


DEFAULT_CONFIG = DiagnosticConfig()


def setup_logging(config: Config, use_console: bool = True) -> None:
    """Setup logging based on configuration."""
    log_format = '%(asctime)s | %(levelname)s | %(name)s | %(message)s'

    handlers: List[logging.Handler] = []

    if use_console:
        handlers.append(logging.StreamHandler())

    if config.log_file:
        Path(config.log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(config.log_file))

    if not handlers:
        handlers.append(logging.NullHandler())

    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.WARNING),
        format=log_format,
        handlers=handlers,
        force=True,
    )
