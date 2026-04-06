"""
Aria2c download accelerator.

Provides fast multi-connection downloads using aria2c.
Falls back to built-in downloader if aria2c is not installed.
"""

import asyncio
import shutil
import re
from pathlib import Path
from typing import Callable, Optional, Dict, List
import logging

from utils.progress import ProgressBar

logger = logging.getLogger(__name__)


class Aria2Error(Exception):
    """Aria2-related errors."""
    pass


class Aria2Downloader:
    """
    Fast download accelerator using aria2c.

    Features:
    - Multi-connection download (default 16 connections)
    - Auto-retry on failure
    - Resume support
    - Proxy support
    - Custom headers (Referer, User-Agent, etc.)
    """

    def __init__(
        self,
        aria2c_path: str = "aria2c",
        connections: int = 16,
        max_retries: int = 5,
        split: int = 16,
        min_split_size: str = "1M",
        timeout: int = 60,
        proxy: Optional[str] = None,
        user_agent: Optional[str] = None,
    ):
        self.aria2c_path = aria2c_path
        self.connections = min(connections, 16)  # aria2c max is 16
        self.max_retries = max_retries
        self.split = min(split, 16)  # match connections limit
        self.min_split_size = min_split_size
        self.timeout = timeout
        self.proxy = proxy
        self.user_agent = user_agent
        self._available = self._verify()

    def _verify(self) -> bool:
        """Check if aria2c is installed."""
        path = shutil.which(self.aria2c_path)
        if not path:
            logger.debug("aria2c not found")
            return False
        logger.debug(f"aria2c found at: {path}")
        return True

    @property
    def is_available(self) -> bool:
        return self._available

    async def download(
        self,
        url: str,
        output_path: str,
        headers: Optional[Dict[str, str]] = None,
        connections: Optional[int] = None,
        progress: Optional[ProgressBar] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        quiet: bool = False,
    ) -> None:
        """
        Download a file using aria2c.

        Args:
            url: Download URL
            output_path: Output file path
            headers: Optional HTTP headers
            connections: Override number of connections
        """
        if not self._available:
            raise Aria2Error("aria2c is not installed. Install with: brew install aria2")

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        num_conn = connections or self.connections

        cmd = [
            self.aria2c_path,
            url,
            '--dir', str(output.parent),
            '--out', output.name,
            # Multi-connection settings
            f'--max-connection-per-server={num_conn}',
            f'--split={self.split}',
            f'--min-split-size={self.min_split_size}',
            # Retry & timeout
            f'--max-tries={self.max_retries}',
            f'--retry-wait=3',
            f'--timeout={self.timeout}',
            f'--connect-timeout=10',
            # Resume
            '--continue=true',
            '--auto-file-renaming=false',
            '--allow-overwrite=true',
            # Progress
            '--summary-interval=1',
            '--console-log-level=notice',
            '--download-result=full',
            # Misc
            '--file-allocation=none',
            '--check-certificate=false',
        ]

        # User agent
        if self.user_agent:
            cmd.append(f'--user-agent={self.user_agent}')

        # Headers
        if headers:
            for key, value in headers.items():
                cmd.append(f'--header={key}: {value}')

        # Proxy
        if self.proxy:
            cmd.append(f'--all-proxy={self.proxy}')

        logger.debug(f"Running aria2c with {num_conn} connections")
        await self._run(
            cmd,
            progress=progress,
            progress_callback=progress_callback,
            detail=f"{num_conn} conn",
            quiet=quiet,
        )

    async def download_batch(
        self,
        urls: List[Dict],
        output_dir: str,
        connections: Optional[int] = None,
    ) -> None:
        """
        Download multiple files using aria2c input file.

        Args:
            urls: List of dicts with 'url', 'filename', 'headers'
            output_dir: Output directory
            connections: Override number of connections
        """
        if not self._available:
            raise Aria2Error("aria2c is not installed")

        import tempfile
        num_conn = connections or self.connections

        # Create aria2 input file
        input_file = Path(tempfile.mktemp(suffix='.txt'))
        with open(input_file, 'w') as f:
            for item in urls:
                f.write(f"{item['url']}\n")
                f.write(f"  out={item.get('filename', '')}\n")
                if 'headers' in item:
                    for key, value in item['headers'].items():
                        f.write(f"  header={key}: {value}\n")
                f.write("\n")

        cmd = [
            self.aria2c_path,
            f'--input-file={input_file}',
            f'--dir={output_dir}',
            f'--max-connection-per-server={num_conn}',
            f'--split={self.split}',
            f'--min-split-size={self.min_split_size}',
            f'--max-concurrent-downloads=4',
            f'--max-tries={self.max_retries}',
            '--continue=true',
            '--auto-file-renaming=false',
            '--allow-overwrite=true',
            '--file-allocation=none',
            '--check-certificate=false',
        ]

        if self.user_agent:
            cmd.append(f'--user-agent={self.user_agent}')
        if self.proxy:
            cmd.append(f'--all-proxy={self.proxy}')

        try:
            await self._run(cmd)
        finally:
            input_file.unlink(missing_ok=True)

    def _parse_progress_line(self, line: str) -> Optional[Dict[str, Optional[float]]]:
        """Parse aria2 progress output into structured values."""
        clean = line.strip()
        if 'DL:' not in clean and 'ETA:' not in clean and '%' not in clean:
            return None

        size_match = re.search(
            r'(?:SIZE:)?\s*(?P<current>[0-9.]+\s*[A-Za-z]+)\/(?P<total>[0-9.]+\s*[A-Za-z]+)',
            clean,
        )
        speed_match = re.search(r'DL:(?P<speed>[0-9.]+\s*[A-Za-z]+)', clean)
        eta_match = re.search(r'ETA:(?P<eta>[0-9A-Za-z:.-]+)', clean)
        conn_match = re.search(r'CN:(?P<conn>\d+)', clean)

        current_bytes = ProgressBar.parse_size_text(size_match.group('current')) if size_match else None
        total_bytes = ProgressBar.parse_size_text(size_match.group('total')) if size_match else None
        speed_bytes = ProgressBar.parse_size_text(speed_match.group('speed')) if speed_match else None
        eta_seconds = ProgressBar.parse_duration_text(eta_match.group('eta')) if eta_match else None
        connections = int(conn_match.group('conn')) if conn_match else None

        if all(value is None for value in (current_bytes, total_bytes, speed_bytes, eta_seconds, connections)):
            return None

        return {
            'current_bytes': current_bytes,
            'total_bytes': total_bytes,
            'speed_bytes': speed_bytes,
            'eta_seconds': eta_seconds,
            'connections': connections,
        }

    async def _run(
        self,
        cmd: List[str],
        progress: Optional[ProgressBar] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        detail: str = "",
        quiet: bool = False,
    ) -> None:
        """Run aria2c command with live output."""
        logger.debug(f"Running: {' '.join(cmd[:5])}...")

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout = process.stdout
        assert stdout is not None

        last_line = ""
        while True:
            line = await stdout.readline()
            if not line:
                break
            decoded = line.decode('utf-8', errors='replace').strip()
            if decoded:
                parsed = self._parse_progress_line(decoded)
                if parsed and progress is not None:
                    detail_parts = []
                    if detail:
                        detail_parts.append(detail)
                    connection_value = parsed.get('connections')
                    if isinstance(connection_value, (int, float)):
                        detail_parts.append(f"active {int(connection_value)}")
                    progress.set(
                        value=parsed.get('current_bytes'),
                        total=parsed.get('total_bytes'),
                        transferred_bytes=parsed.get('current_bytes'),
                        stage='aria2c',
                        detail=' | '.join(detail_parts),
                        speed=parsed.get('speed_bytes'),
                        eta=parsed.get('eta_seconds'),
                    )

                    current_bytes = parsed.get('current_bytes')
                    total_bytes = parsed.get('total_bytes')
                    if progress_callback and current_bytes is not None:
                        try:
                            progress_callback(int(current_bytes), int(total_bytes or 0))
                        except Exception:
                            pass

                    last_line = decoded
                elif not quiet and any(x in decoded for x in ['DL:', 'ETA:', '[', 'OK', 'Download complete']):
                    print(f"\r  {decoded[:100]}", end='', flush=True)
                    last_line = decoded
                if 'ERROR' in decoded.upper():
                    logger.error(f"aria2c: {decoded}")

        await process.wait()

        if last_line and progress is None and not quiet:
            print()  # New line after progress

        if process.returncode != 0:
            if progress is not None:
                progress.interrupt("aria2c failed, switching mode")
            raise Aria2Error(f"aria2c exited with code {process.returncode}")

        if progress is not None:
            progress.finish(detail=detail or "aria2c")

        logger.debug("aria2c download completed")
