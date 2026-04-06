"""
Core processing engine.

* **VideoDetector** — FFprobe-based metadata extraction.
* **FrameExtractor** — FFmpeg subprocess wrapper with progress monitoring,
  single-video and batch-parallel extraction, and OpenCV-backed validation.
"""

import json
import logging
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from math import gcd
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("video2frames.engine")


# ── Data containers ───────────────────────────────────────────────────────────

@dataclass
class VideoMetadata:
    """Everything we know about a source video."""

    filepath: Path
    filename: str
    duration: float        # seconds
    fps: float
    width: int
    height: int
    codec: str
    total_frames: int
    file_size: int         # bytes
    pixel_format: str
    bitrate: Optional[int] = None

    @property
    def resolution(self) -> str:
        return f"{self.width}×{self.height}"

    @property
    def aspect_ratio(self) -> str:
        d = gcd(self.width, self.height)
        return f"{self.width // d}:{self.height // d}"


@dataclass
class ExtractionResult:
    """Outcome of a single extraction run."""

    video_path: Path
    output_dir: Path
    expected_frames: int
    extracted_frames: int
    processing_time: float   # seconds
    success: bool
    metadata: Optional[VideoMetadata] = None
    error: Optional[str] = None

    @property
    def speed_fps(self) -> float:
        return self.extracted_frames / self.processing_time if self.processing_time else 0.0

    @property
    def frames_match(self) -> bool:
        return abs(self.extracted_frames - self.expected_frames) <= 1


# ── Metadata detection ────────────────────────────────────────────────────────

class VideoDetector:
    """Extract metadata via *ffprobe*."""

    def __init__(self, ffprobe_path: str = "ffprobe"):
        self.ffprobe_path = ffprobe_path

    def detect(self, video_path: Path) -> VideoMetadata:
        video_path = Path(video_path)
        if not video_path.is_file():
            raise FileNotFoundError(f"Not found: {video_path}")

        base_cmd = [
            self.ffprobe_path,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            "-select_streams", "v:0",
        ]

        # Try with -count_frames first (accurate but slow on large files)
        for extra in (["-count_frames"], []):
            try:
                r = subprocess.run(
                    base_cmd + extra + [str(video_path)],
                    capture_output=True, text=True, timeout=120,
                )
                if r.returncode == 0:
                    break
            except subprocess.TimeoutExpired:
                continue
        else:
            raise RuntimeError(f"ffprobe failed for {video_path}")

        data = json.loads(r.stdout)
        streams = data.get("streams", [])
        if not streams:
            raise ValueError(f"No video stream in {video_path}")

        s = streams[0]
        fmt = data.get("format", {})

        # FPS
        fps_str = s.get("r_frame_rate", "30/1")
        try:
            if "/" in fps_str:
                n, d = map(int, fps_str.split("/"))
                fps = n / d if d else 30.0
            else:
                fps = float(fps_str)
        except (ValueError, ZeroDivisionError):
            fps = 30.0

        # Duration
        duration = float(s.get("duration", fmt.get("duration", 0)))

        # Frame count
        total = int(s.get("nb_read_frames", s.get("nb_frames", 0)))
        if total == 0 and duration > 0:
            total = int(round(fps * duration))

        width = int(s.get("width", 0))
        height = int(s.get("height", 0))
        if width == 0 or height == 0:
            raise ValueError(f"Cannot determine resolution for {video_path}")

        bitrate = None
        for src in (s, fmt):
            if src.get("bit_rate"):
                try:
                    bitrate = int(src["bit_rate"])
                    break
                except ValueError:
                    pass

        return VideoMetadata(
            filepath=video_path,
            filename=video_path.stem,
            duration=duration,
            fps=round(fps, 3),
            width=width,
            height=height,
            codec=s.get("codec_name", "unknown"),
            total_frames=total,
            file_size=video_path.stat().st_size,
            pixel_format=s.get("pix_fmt", "unknown"),
            bitrate=bitrate,
        )


# ── Frame extraction ─────────────────────────────────────────────────────────

class FrameExtractor:
    """
    High-performance frame extractor backed by FFmpeg.

    * Single-video extraction with real-time progress.
    * Batch-parallel mode via ``ThreadPoolExecutor``.
    * Optional post-extraction validation with OpenCV.
    """

    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        threads: int = 4,
        compression_level: int = 3,
    ):
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.threads = threads
        self.compression_level = compression_level
        self.detector = VideoDetector(ffprobe_path)
        self._cancel = threading.Event()

    # ── lifecycle ─────────────────────────────────────────────────────────
    def cancel(self) -> None:
        self._cancel.set()

    def reset(self) -> None:
        self._cancel.clear()

    # ── single video ──────────────────────────────────────────────────────
    def extract_single(
        self,
        video_path: Path,
        output_dir: Path,
        output_format: str = "png",
        overwrite: bool = False,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> ExtractionResult:
        """
        Extract **every** frame at full FPS and original resolution.

        Parameters
        ----------
        video_path : Path
            Source video.
        output_dir : Path
            Destination folder (created automatically).
        output_format : str
            ``png`` (lossless, default), ``bmp``, ``tiff``, or ``jpg``.
        overwrite : bool
            Replace existing frames without asking.
        progress_callback : callable(current, total)
            Invoked as frames are written.

        Returns
        -------
        ExtractionResult
        """
        video_path, output_dir = Path(video_path), Path(output_dir)
        t0 = time.time()

        try:
            # 1) metadata
            meta = self.detector.detect(video_path)
            logger.info(
                "Detected %s  %s @ %.3f fps  %d frames  codec=%s",
                meta.filename, meta.resolution, meta.fps,
                meta.total_frames, meta.codec,
            )

            # 2) output dir
            output_dir.mkdir(parents=True, exist_ok=True)
            ext = output_format.lower()
            existing = list(output_dir.glob(f"*.{ext}"))
            if existing and not overwrite:
                raise FileExistsError(
                    f"Directory already has {len(existing)} .{ext} files. "
                    "Use --overwrite to replace them."
                )

            # 3) build command
            pattern = str(output_dir / f"frame_%06d.{ext}")
            cmd: list = [
                self.ffmpeg_path,
                "-nostdin",
                "-hide_banner",
                "-loglevel", "error",
                "-i", str(video_path),
                "-vsync", "0",
                "-threads", str(self.threads),
            ]
            if ext == "png":
                cmd += ["-compression_level", str(self.compression_level)]
            elif ext in ("jpg", "jpeg"):
                cmd += ["-qscale:v", "2"]
            elif ext == "tiff":
                cmd += ["-compression_algo", "lzw"]

            cmd += ["-progress", "pipe:1"]
            cmd += ["-y" if overwrite else "-n", pattern]

            logger.info("CMD: %s", " ".join(cmd))

            # 4) run with progress monitoring
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                bufsize=1,
            )

            current_frame = 0
            for line in proc.stdout:  # type: ignore[union-attr]
                if self._cancel.is_set():
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    raise InterruptedError("Cancelled by user")

                line = line.strip()
                if line.startswith("frame="):
                    try:
                        current_frame = int(line.split("=", 1)[1].strip())
                        if progress_callback:
                            progress_callback(current_frame, meta.total_frames)
                    except (ValueError, IndexError):
                        pass

            proc.wait(timeout=600)

            if proc.returncode != 0:
                stderr = proc.stderr.read().strip()  # type: ignore[union-attr]
                raise RuntimeError(
                    f"FFmpeg exited with code {proc.returncode}: {stderr}"
                )

            # 5) count what landed on disk
            extracted = len(list(output_dir.glob(f"frame_*.{ext}")))

            elapsed = time.time() - t0
            result = ExtractionResult(
                video_path=video_path,
                output_dir=output_dir,
                expected_frames=meta.total_frames,
                extracted_frames=extracted,
                processing_time=elapsed,
                success=True,
                metadata=meta,
            )
            logger.info(
                "Done: %d frames in %s (%.1f fps)",
                extracted, f"{elapsed:.1f}s", result.speed_fps,
            )
            return result

        except Exception as exc:
            elapsed = time.time() - t0
            logger.error("Extraction failed for %s: %s", video_path, exc)
            return ExtractionResult(
                video_path=video_path,
                output_dir=output_dir,
                expected_frames=0,
                extracted_frames=0,
                processing_time=elapsed,
                success=False,
                error=str(exc),
            )

    # ── batch ─────────────────────────────────────────────────────────────
    def extract_batch(
        self,
        video_paths: List[Path],
        output_base: Path,
        output_format: str = "png",
        overwrite: bool = False,
        max_parallel: int = 2,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        video_done_callback: Optional[Callable[[ExtractionResult], None]] = None,
    ) -> List[ExtractionResult]:
        """Process multiple videos with a thread-pool queue."""
        output_base = Path(output_base)
        output_base.mkdir(parents=True, exist_ok=True)
        results: List[ExtractionResult] = []

        def _job(vp: Path) -> ExtractionResult:
            return self.extract_single(
                vp,
                output_base / vp.stem,
                output_format,
                overwrite,
                progress_callback,
            )

        with ThreadPoolExecutor(max_workers=max_parallel) as pool:
            futs = {pool.submit(_job, vp): vp for vp in video_paths}
            for fut in as_completed(futs):
                vp = futs[fut]
                try:
                    res = fut.result()
                except Exception as exc:
                    res = ExtractionResult(
                        video_path=vp,
                        output_dir=output_base / vp.stem,
                        expected_frames=0,
                        extracted_frames=0,
                        processing_time=0,
                        success=False,
                        error=str(exc),
                    )
                results.append(res)
                if video_done_callback:
                    video_done_callback(res)

        return results

    # ── validation ────────────────────────────────────────────────────────
    def validate_extraction(
        self,
        output_dir: Path,
        video_path: Optional[Path] = None,
        metadata: Optional[VideoMetadata] = None,
        output_format: str = "png",
    ) -> Dict[str, Any]:
        """
        Verify frame count, naming continuity, and image integrity.

        Uses OpenCV to spot-check a sample of frame files.
        """
        try:
            import cv2
        except ImportError:
            return {"error": "opencv-python is required for validation"}

        output_dir = Path(output_dir)
        ext = output_format.lower()

        if metadata is None and video_path:
            metadata = self.detector.detect(video_path)
        expected = metadata.total_frames if metadata else None

        frame_files = sorted(output_dir.glob(f"frame_*.{ext}"))
        total = len(frame_files)

        # spot-check integrity
        corrupted: List[str] = []
        sample_n = min(20, total)
        step = max(1, total // sample_n) if sample_n else 1
        for i in range(0, total, step):
            img = cv2.imread(str(frame_files[i]))
            if img is None:
                corrupted.append(frame_files[i].name)

        # naming continuity
        missing: List[str] = []
        for i in range(1, total + 1):
            if not (output_dir / f"frame_{i:06d}.{ext}").exists():
                missing.append(f"frame_{i:06d}.{ext}")
                if len(missing) >= 50:
                    break

        report: Dict[str, Any] = {
            "output_dir": str(output_dir),
            "total_files": total,
            "expected_frames": expected,
            "count_match": abs(total - expected) <= 1 if expected else None,
            "corrupted_samples": corrupted,
            "missing_names": missing[:20],
            "naming_continuous": len(missing) == 0,
        }

        # resolution spot-check
        if total > 0:
            img = cv2.imread(str(frame_files[0]))
            if img is not None:
                h, w = img.shape[:2]
                report["frame_resolution"] = f"{w}×{h}"
                if metadata:
                    report["resolution_match"] = (
                        w == metadata.width and h == metadata.height
                    )

        report["valid"] = (
            len(corrupted) == 0
            and len(missing) == 0
            and (report.get("count_match") is not False)
            and report.get("resolution_match", True)
        )
        return report