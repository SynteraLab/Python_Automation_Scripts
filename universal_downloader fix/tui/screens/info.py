"""
Video info and extractor info screens.
"""

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static, Input, Button, RichLog, DataTable
from textual.containers import Vertical, Horizontal
from textual import work


class VideoInfoView(Widget):
    """Video info display."""

    DEFAULT_CSS = """
    VideoInfoView {
        height: 100%;
        padding: 1;
    }
    VideoInfoView #info-header {
        text-style: bold; color: $primary; height: 2;
    }
    VideoInfoView #info-output {
        height: 1fr; min-height: 10;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("ℹ️ Video Info", id="info-header")
        with Horizontal():
            yield Input(placeholder="Paste URL...", id="info-url")
            yield Button("Get Info", variant="primary", id="btn-get-info")
        yield RichLog(id="info-output", highlight=True, markup=True, wrap=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-get-info":
            url = self.query_one("#info-url", Input).value.strip()
            if url:
                self._fetch_info(url)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "info-url" and event.value.strip():
            self._fetch_info(event.value.strip())

    @work(thread=True)
    def _fetch_info(self, url: str) -> None:
        self.app.call_from_thread(self._log, f"[cyan]Fetching info: {url}[/cyan]")

        from utils.network import SessionManager
        from extractors.base import registry

        config = self.app.config
        session = SessionManager(
            user_agent=config.extractor.user_agent,
            proxy=config.proxy.to_dict(),
            cookies_file=config.cookies_file,
            cookies_from_browser=config.cookies_from_browser,
        )

        try:
            extractor_class = registry.find_extractor(url)
            if extractor_class:
                try:
                    ext = extractor_class(session, config=vars(config))
                    info = ext.extract(url)
                    self.app.call_from_thread(self._log, f"\n[bold]Title:[/bold] {info.title}")
                    self.app.call_from_thread(
                        self._log,
                        f"[dim]Extractor: {info.extractor} | Formats: {len(info.formats)}[/dim]"
                    )
                    if info.duration:
                        m, s = divmod(info.duration, 60)
                        self.app.call_from_thread(self._log, f"[dim]Duration: {m}:{s:02d}[/dim]")
                    if info.best_format:
                        self.app.call_from_thread(
                            self._log, f"[dim]Best: {info.best_format.format_note}[/dim]"
                        )
                    return
                except Exception:
                    pass

            from extractors.ytdlp import YtdlpExtractor, YTDLP_AVAILABLE
            if YTDLP_AVAILABLE:
                try:
                    ext = YtdlpExtractor(session, config=vars(config))
                    info = ext.extract(url)
                    self.app.call_from_thread(self._log, f"\n[bold]Title:[/bold] {info.title}")
                    self.app.call_from_thread(
                        self._log,
                        f"[dim]Extractor: {info.extractor} | Formats: {len(info.formats)}[/dim]"
                    )
                    if info.duration:
                        m, s = divmod(info.duration, 60)
                        self.app.call_from_thread(self._log, f"[dim]Duration: {m}:{s:02d}[/dim]")
                    return
                except Exception:
                    pass

            self.app.call_from_thread(self._log, "[red]Could not get video info[/red]")
        finally:
            session.close()

    def _log(self, msg: str) -> None:
        try:
            self.query_one("#info-output", RichLog).write(msg)
        except Exception:
            pass


class ExtractorsView(Widget):
    """Show available extractors."""

    DEFAULT_CSS = """
    ExtractorsView { height: 100%; padding: 1; }
    ExtractorsView #ext-header { text-style: bold; color: $primary; height: 2; }
    """

    def compose(self) -> ComposeResult:
        yield Static("🔧 Available Extractors", id="ext-header")
        yield DataTable(id="ext-table")

    def on_mount(self) -> None:
        from extractors.base import registry
        from extractors.ytdlp import YTDLP_AVAILABLE

        dt = self.query_one("#ext-table", DataTable)
        dt.add_column("Extractor", key="name", width=15)
        dt.add_column("Description", key="desc")
        dt.add_column("Status", key="status", width=14)

        for ext in registry.get_all():
            dt.add_row(ext.EXTRACTOR_NAME, ext.EXTRACTOR_DESCRIPTION, "✓ Active")

        yt_status = "✓ Active" if YTDLP_AVAILABLE else "✗ Not installed"
        dt.add_row("yt-dlp", "Fallback for 1000+ sites", yt_status)


class SupportedSitesView(Widget):
    """Show supported sites."""

    DEFAULT_CSS = """
    SupportedSitesView { height: 100%; padding: 1; }
    SupportedSitesView #sites-header { text-style: bold; color: $primary; height: 2; }
    SupportedSitesView #sites-content { height: 1fr; }
    """

    def compose(self) -> ComposeResult:
        yield Static("🌐 Supported Sites", id="sites-header")
        yield RichLog(id="sites-content", highlight=True, markup=True, wrap=True)

    def on_mount(self) -> None:
        from extractors.ytdlp import YTDLP_AVAILABLE

        log = self.query_one("#sites-content", RichLog)
        log.write("[bold cyan]Custom Extractors (built-in):[/bold cyan]")
        log.write("  • Social Media (YouTube, TikTok, Instagram, Facebook, X, Reddit, etc.)")
        log.write("  • PubJav.com (multi-server, VidHide, HLS)")
        log.write("  • SupJav.com + TurboVidHLS (Cloudflare, HLS)")
        log.write("  • Any JWPlayer site (auto-detected)")
        log.write("  • HLS streams (.m3u8 direct)")
        log.write("  • Generic HTML (video tags, meta tags)")

        if YTDLP_AVAILABLE:
            log.write("\n[bold cyan]yt-dlp Supported Sites (1000+):[/bold cyan]")
            for s in [
                "YouTube, Vimeo, Dailymotion, Twitch",
                "Instagram, Facebook, Twitter/X, TikTok",
                "Reddit, Pinterest, LinkedIn, Threads",
                "SoundCloud, Bandcamp, Mixcloud",
                "Bilibili, Niconico, VK, Weibo",
                "Pornhub, Xvideos, XHamster",
                "And 1000+ more...",
            ]:
                log.write(f"  • {s}")
        else:
            log.write("\n[yellow]Install yt-dlp for 1000+ more sites: pip install yt-dlp[/yellow]")