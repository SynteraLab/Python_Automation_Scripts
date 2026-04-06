"""
Helpers for EroMe album downloads.

Photos are downloaded in parallel with the standard downloader.
Videos stay sequential and use aria2c when available.
"""

import asyncio
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from config import Config
from core.downloader import Downloader
from models.media import MediaInfo, MediaType
from utils.helpers import sanitize_filename


@dataclass(frozen=True)
class EromeDownloadJob:
    """Represents one prepared EroMe album download."""

    order: int
    total: int
    item: Any
    media_info: MediaInfo
    output_path: Path


@dataclass(frozen=True)
class EromeDownloadResult:
    """Represents the final result for one EroMe album item."""

    job: EromeDownloadJob
    mode: str
    output_path: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


def erome_video_uses_aria2() -> bool:
    """Return True when aria2c is available for EroMe videos."""
    return shutil.which("aria2c") is not None


def erome_photo_parallel_workers(config: Config, photo_count: int = 0) -> int:
    """Resolve a safe worker count for parallel EroMe photo downloads."""
    configured = getattr(config.download, 'max_concurrent', 8)
    try:
        workers = int(configured)
    except Exception:
        workers = 8

    workers = max(1, min(workers, 8))
    if photo_count > 0:
        workers = min(workers, photo_count)
    return workers


def prepare_erome_download_jobs(
    album: Dict[str, Any],
    selected_items: Sequence[Any],
    album_dir: Path,
) -> List[EromeDownloadJob]:
    """Build normalized download jobs for EroMe album items."""
    total = len(selected_items)
    jobs: List[EromeDownloadJob] = []

    for order, item in enumerate(selected_items, 1):
        fmt = item.format
        ext = fmt.ext or 'bin'
        safe_title = sanitize_filename(item.title) or f"{item.media_type}_{item.index:03d}"
        filename = f"{item.index:03d}_{safe_title}.{ext}"
        output_path = album_dir / filename

        item_media_type = MediaType.VIDEO if item.media_type == 'video' else MediaType.UNKNOWN
        media_info = MediaInfo(
            id=f"{album['id']}-{item.media_type}-{item.index}",
            title=item.title,
            url=fmt.url,
            formats=[fmt],
            media_type=item_media_type,
            extractor='erome',
            thumbnail=item.thumbnail,
            uploader=album.get('uploader'),
        )

        jobs.append(
            EromeDownloadJob(
                order=order,
                total=total,
                item=item,
                media_info=media_info,
                output_path=output_path,
            )
        )

    return jobs


async def download_erome_jobs(
    jobs: Sequence[EromeDownloadJob],
    config: Config,
    on_item_start: Optional[Callable[[EromeDownloadJob, str], None]] = None,
    on_item_success: Optional[Callable[[EromeDownloadResult], None]] = None,
    on_item_failure: Optional[Callable[[EromeDownloadResult], None]] = None,
    on_photo_batch_start: Optional[Callable[[Sequence[EromeDownloadJob], int], None]] = None,
) -> List[EromeDownloadResult]:
    """Download EroMe jobs with photo parallelism and aria2 video handling."""
    if not jobs:
        return []

    video_with_aria2 = erome_video_uses_aria2()
    standard_config = replace(config, download=replace(config.download, use_aria2=False))
    standard_downloader = Downloader(standard_config)

    video_downloader: Optional[Downloader] = None
    if any(job.item.media_type == 'video' for job in jobs):
        video_config = replace(config, download=replace(config.download, use_aria2=video_with_aria2))
        video_downloader = Downloader(video_config)

    async def _run_job(job: EromeDownloadJob) -> EromeDownloadResult:
        is_video = job.item.media_type == 'video'
        mode = 'aria2' if is_video and video_with_aria2 else 'standard'
        downloader = video_downloader if is_video and video_downloader is not None else standard_downloader

        if on_item_start is not None:
            on_item_start(job, mode)

        try:
            output_path = await downloader.download(
                job.media_info,
                job.item.format,
                output_path=str(job.output_path),
                show_progress=is_video,
            )
            result = EromeDownloadResult(job=job, mode=mode, output_path=output_path)
            if on_item_success is not None:
                on_item_success(result)
            return result
        except Exception as e:
            result = EromeDownloadResult(job=job, mode=mode, error=str(e))
            if on_item_failure is not None:
                on_item_failure(result)
            return result

    async def _flush_photo_batch(batch: List[EromeDownloadJob]) -> List[EromeDownloadResult]:
        if not batch:
            return []

        workers = erome_photo_parallel_workers(config, len(batch))
        if on_photo_batch_start is not None:
            on_photo_batch_start(batch, workers)

        semaphore = asyncio.Semaphore(workers)

        async def _run_limited(job: EromeDownloadJob) -> EromeDownloadResult:
            async with semaphore:
                return await _run_job(job)

        tasks = [asyncio.create_task(_run_limited(job)) for job in batch]
        results: List[EromeDownloadResult] = []
        for task in asyncio.as_completed(tasks):
            results.append(await task)
        return results

    results: List[EromeDownloadResult] = []
    pending_photos: List[EromeDownloadJob] = []

    for job in jobs:
        if job.item.media_type == 'photo':
            pending_photos.append(job)
            continue

        results.extend(await _flush_photo_batch(pending_photos))
        pending_photos = []
        results.append(await _run_job(job))

    results.extend(await _flush_photo_batch(pending_photos))
    return sorted(results, key=lambda result: result.job.order)
