"""
Pipeline v5:
  Mode BESAR: parallel singles, always-album for split, caption = filename
  Mode KECIL: video largest→smallest as albums, then photos as albums, sequential
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from pathlib import Path

from config import AppConfig
from account_manager import AccountManager
from splitter import VideoSplitter
from uploader import FileUploader
from utils import (
    collect_files, collect_files_small_mode, FileEntry,
    compress_video, human_size, format_duration, probe_video,
    BandwidthLimiter, GlobalProgress, UploadHistory, FailedTracker,
)

logger = logging.getLogger("tg_uploader")

ALBUM_MAX = 10


@dataclass
class SmallFolderPlan:
    folder: Path
    caption: str
    videos: list[Path]
    photos: list[Path]
    skipped: list[Path]
    worker_slot: int

    @property
    def total_files(self) -> int:
        return len(self.videos) + len(self.photos)

    @property
    def total_size(self) -> int:
        return sum(f.stat().st_size for f in self.videos + self.photos)


class QueueManager:
    def __init__(self, cfg: AppConfig, accounts: AccountManager) -> None:
        self.cfg = cfg
        self.accounts = accounts
        self.splitter = VideoSplitter(cfg.max_size_bytes)
        self.limiter = BandwidthLimiter(cfg.speed_limit_bytes)
        self.history = UploadHistory(cfg.history_file) if cfg.skip_uploaded else None
        self.failed = FailedTracker()
        self.stats = {"ok": 0, "fail": 0, "skip": 0, "bytes": 0}
        self._acc_counter = 0
        self._acc_lock = asyncio.Lock()

    async def _next_acc(self) -> int:
        async with self._acc_lock:
            idx = self._acc_counter
            self._acc_counter += 1
            return idx

    async def run(self) -> dict:
        if self.cfg.upload_mode == "kecil":
            return await self._run_small()
        else:
            return await self._run_big()

    # ══════════════════════════════════════════════════════════════════
    # MODE BESAR
    # ══════════════════════════════════════════════════════════════════

    async def _run_big(self) -> dict:
        entries = collect_files(self.cfg.folder, self.cfg.recursive, self.cfg.sort)
        if not entries:
            logger.warning(f"Tidak ada file di {self.cfg.folder}")
            return self.stats

        total_size = sum(e.path.stat().st_size for e in entries)
        logger.info(f"Mode BESAR — {len(entries)} file ({human_size(total_size)}):")
        for e in entries:
            sz = e.path.stat().st_size
            if e.is_video:
                meta = probe_video(e.path)
                tags = []
                if sz > self.cfg.max_size_bytes:
                    tags.append("SPLIT")
                if self.history and self.history.is_uploaded(e.path, self.cfg.target):
                    tags.append("SKIP")
                if e.thumb_is_manual:
                    thumb_name = e.thumbnail.name if e.thumbnail else "manual"
                    tags.append(f"thumb: {thumb_name}")
                tag = f"  [{', '.join(tags)}]" if tags else ""
                logger.info(
                    f"  🎬 {e.path.name}  ({human_size(sz)}, {meta.resolution_str}, "
                    f"{format_duration(meta.duration) if meta.duration > 0 else '?'}){tag}"
                )
            elif e.is_image:
                skip = " [SKIP]" if self.history and self.history.is_uploaded(e.path, self.cfg.target) else ""
                logger.info(f"  🖼️  {e.path.name}  ({human_size(sz)}){skip}")
        print()

        # Filter skipped, only keep videos for big mode
        jobs: list[FileEntry] = []
        skipped_uploaded: list[str] = []
        for e in entries:
            if self.history and self.history.is_uploaded(e.path, self.cfg.target):
                self.stats["skip"] += 1
                skipped_uploaded.append(e.path.name)
                continue
            if e.is_video:
                jobs.append(e)

        if skipped_uploaded:
            logger.info(f"Dilewati (sudah pernah diupload): {len(skipped_uploaded)} file")
            for name in skipped_uploaded:
                logger.info(f"  ⊘ {name}")

        if not jobs:
            logger.info("Tidak ada job upload. Semua file video sudah pernah diupload atau tidak ada video.")
            return self.stats

        upload_size = sum(e.path.stat().st_size for e in jobs)
        progress = GlobalProgress(len(jobs), upload_size)
        t0 = time.monotonic()
        nw = min(self.cfg.workers, self.accounts.count)
        logger.info(f"Mulai upload — {nw} akun paralel, split → album")

        split_jobs = [e for e in jobs if self.splitter.needs_split(e.path)]
        single_jobs = [e for e in jobs if not self.splitter.needs_split(e.path)]

        tasks: list[asyncio.Task] = []

        # Singles → parallel queue
        if single_jobs:
            queue: asyncio.Queue[FileEntry | None] = asyncio.Queue()
            for j in single_jobs:
                await queue.put(j)
            num_workers = min(nw, len(single_jobs))
            for _ in range(num_workers):
                await queue.put(None)
            for i in range(num_workers):
                tasks.append(asyncio.create_task(self._big_single_worker(queue, progress)))

        # Splits → one task per file
        for e in split_jobs:
            tasks.append(asyncio.create_task(self._big_split_handler(e, progress)))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"Task error: {r}")

        self._print_report(time.monotonic() - t0)
        return self.stats

    async def _big_single_worker(self, queue: asyncio.Queue, progress: GlobalProgress) -> None:
        """Upload single videos in parallel."""
        while True:
            entry = await queue.get()
            if entry is None:
                queue.task_done()
                break

            acc_idx = await self._next_acc()
            label = self.accounts.get_label(acc_idx)
            filepath = entry.path
            file_size = filepath.stat().st_size

            # Optional compression
            compressed = None
            upload_path = filepath
            if self.cfg.compress:
                compressed = await compress_video(filepath)
                if compressed:
                    upload_path = compressed

            # Caption = filename (or custom)
            caption = self.cfg.caption if self.cfg.caption else filepath.name

            logger.info(f"📄 {filepath.name} → {label}")
            client = await self.accounts.ensure_connected(acc_idx)
            uploader = FileUploader(
                client, self.cfg.target, self.cfg.retries,
                self.cfg.thumb_size, self.limiter, progress_prefix=label,
            )
            ok, reason = await uploader.upload_video(
                upload_path, caption, manual_thumb=entry.thumbnail,
            )

            if compressed and compressed.exists():
                compressed.unlink()

            if ok:
                self.stats["ok"] += 1
                self.stats["bytes"] += file_size
                if self.history:
                    self.history.mark_uploaded(filepath, self.cfg.target)
            else:
                self.stats["fail"] += 1
                await self.failed.add(filepath.name, reason)

            await progress.file_done(file_size, ok)
            queue.task_done()

    async def _big_split_handler(self, entry: FileEntry, progress: GlobalProgress) -> None:
        """Split → always album."""
        filepath = entry.path

        # Optional compression
        compressed = None
        upload_path = filepath
        if self.cfg.compress:
            compressed = await compress_video(filepath)
            if compressed:
                upload_path = compressed

        # Check if still needs split after compression
        if not self.splitter.needs_split(upload_path):
            caption = self.cfg.caption if self.cfg.caption else filepath.name
            acc_idx = await self._next_acc()
            label = self.accounts.get_label(acc_idx)
            logger.info(f"📄 {filepath.name} → {label}")
            client = await self.accounts.ensure_connected(acc_idx)
            up = FileUploader(
                client, self.cfg.target, self.cfg.retries,
                self.cfg.thumb_size, self.limiter, progress_prefix=label,
            )
            ok, reason = await up.upload_video(upload_path, caption, manual_thumb=entry.thumbnail)
            if compressed and compressed.exists():
                compressed.unlink()
            if ok:
                self.stats["ok"] += 1
                self.stats["bytes"] += filepath.stat().st_size
                if self.history:
                    self.history.mark_uploaded(filepath, self.cfg.target)
            else:
                self.stats["fail"] += 1
                await self.failed.add(filepath.name, reason)
            await progress.file_done(filepath.stat().st_size, ok)
            return

        # Split
        logger.info(f"📂 Splitting: {filepath.name} ({human_size(filepath.stat().st_size)})")
        parts = await self.splitter.split(upload_path)

        if not parts:
            self.stats["fail"] += 1
            await self.failed.add(filepath.name, "Split gagal")
            await progress.file_done(filepath.stat().st_size, False)
            if compressed and compressed.exists():
                compressed.unlink()
            return

        # Always album — caption = original filename
        caption = self.cfg.caption if self.cfg.caption else filepath.name
        acc_idx = await self._next_acc()
        label = self.accounts.get_label(acc_idx)
        logger.info(f"  Album: {len(parts)} parts → {label}")

        client = await self.accounts.ensure_connected(acc_idx)
        up = FileUploader(
            client, self.cfg.target, self.cfg.retries,
            self.cfg.thumb_size, self.limiter, progress_prefix=label,
        )
        ok, reason = await up.upload_video_album(parts, caption)

        if ok:
            self.stats["ok"] += 1
            self.stats["bytes"] += filepath.stat().st_size
            if self.history:
                self.history.mark_uploaded(filepath, self.cfg.target)
            if self.cfg.cleanup:
                for p in parts:
                    try:
                        p.unlink()
                    except OSError:
                        pass
        else:
            self.stats["fail"] += 1
            await self.failed.add(f"{filepath.name} (album)", reason)

        if self.cfg.cleanup:
            self.splitter.cleanup_parts(upload_path)
        if compressed and compressed.exists():
            compressed.unlink()
        await progress.file_done(filepath.stat().st_size, ok)

    # ══════════════════════════════════════════════════════════════════
    # MODE KECIL
    # Auto sort: video largest→smallest, then photos
    # Sequential albums, 1 account sends all (no interleaving)
    # Caption only on last file of last album
    # ══════════════════════════════════════════════════════════════════

    async def _run_small(self) -> dict:
        if self.cfg.folder.is_file():
            logger.warning("Mode kecil butuh folder, bukan single file.")
            return self.stats

        videos, photos = collect_files_small_mode(self.cfg.folder, self.cfg.recursive)

        # Filter skipped
        skipped_uploaded: list[Path] = []
        if self.history:
            remaining_videos: list[Path] = []
            remaining_photos: list[Path] = []

            for v in videos:
                if self.history.is_uploaded(v, self.cfg.target):
                    skipped_uploaded.append(v)
                else:
                    remaining_videos.append(v)

            for p in photos:
                if self.history.is_uploaded(p, self.cfg.target):
                    skipped_uploaded.append(p)
                else:
                    remaining_photos.append(p)

            videos = remaining_videos
            photos = remaining_photos
            self.stats["skip"] = len(skipped_uploaded)

        if skipped_uploaded:
            logger.info(f"Dilewati (sudah pernah diupload): {len(skipped_uploaded)} file")
            for f in skipped_uploaded:
                logger.info(f"  ⊘ {f.name}")

        total_files = len(videos) + len(photos)
        if total_files == 0:
            logger.info("Tidak ada file baru untuk diupload.")
            return self.stats

        total_size = sum(f.stat().st_size for f in videos + photos)
        logger.info(f"Mode KECIL — {total_files} file ({human_size(total_size)}):")
        logger.info(f"  🎬 {len(videos)} video (terbesar dulu)")
        logger.info(f"  🖼️  {len(photos)} foto")
        for v in videos:
            logger.info(f"    {v.name}  ({human_size(v.stat().st_size)})")
        for p in photos:
            logger.info(f"    {p.name}  ({human_size(p.stat().st_size)})")
        print()

        progress = GlobalProgress(total_files, total_size)
        t0 = time.monotonic()

        # Use ONE account for sending (ensures order, no interleaving)
        send_acc = await self._next_acc()
        send_label = self.accounts.get_label(send_acc)
        logger.info(f"Kirim album via: {send_label}")

        # Determine what the very last file is (for caption)
        # Caption only on last file of last album
        has_photos = len(photos) > 0
        has_videos = len(videos) > 0
        caption = self.cfg.caption  # custom caption or ""

        # ── Video albums ──
        if has_videos:
            logger.info(f"── Video ({len(videos)} file) ──")
            video_chunks = [videos[i:i + ALBUM_MAX] for i in range(0, len(videos), ALBUM_MAX)]

            for ci, chunk in enumerate(video_chunks):
                is_last_video_chunk = (ci == len(video_chunks) - 1)
                # Caption only if this is THE last chunk overall (no photos after)
                if is_last_video_chunk and not has_photos:
                    # This is the very last album — caption on last file
                    last_file = chunk[-1]
                    chunk_caption = caption if caption else last_file.name
                else:
                    chunk_caption = ""

                client = await self.accounts.ensure_connected(send_acc)
                uploader = FileUploader(
                    client, self.cfg.target, self.cfg.retries,
                    self.cfg.thumb_size, self.limiter, progress_prefix=send_label,
                )
                ok, reason = await uploader.upload_small_album(
                    chunk, chunk_caption, is_video=True,
                )

                chunk_size = sum(f.stat().st_size for f in chunk)
                if ok:
                    self.stats["ok"] += len(chunk)
                    self.stats["bytes"] += chunk_size
                    for f in chunk:
                        if self.history:
                            self.history.mark_uploaded(f, self.cfg.target)
                    await progress.batch_done(chunk_size, len(chunk), True)
                else:
                    self.stats["fail"] += len(chunk)
                    await self.failed.add(f"Album video {ci + 1}", reason)
                    await progress.batch_done(chunk_size, len(chunk), False)

        # ── Photo albums ──
        if has_photos:
            logger.info(f"── Foto ({len(photos)} file) ──")
            photo_chunks = [photos[i:i + ALBUM_MAX] for i in range(0, len(photos), ALBUM_MAX)]

            for ci, chunk in enumerate(photo_chunks):
                is_last_photo_chunk = (ci == len(photo_chunks) - 1)
                # Last photo album = last overall → caption
                if is_last_photo_chunk:
                    last_file = chunk[-1]
                    chunk_caption = caption if caption else last_file.name
                else:
                    chunk_caption = ""

                client = await self.accounts.ensure_connected(send_acc)
                uploader = FileUploader(
                    client, self.cfg.target, self.cfg.retries,
                    self.cfg.thumb_size, self.limiter, progress_prefix=send_label,
                )
                ok, reason = await uploader.upload_small_album(
                    chunk, chunk_caption, is_video=False,
                    as_document=self.cfg.photo_as_document,
                )

                chunk_size = sum(f.stat().st_size for f in chunk)
                if ok:
                    self.stats["ok"] += len(chunk)
                    self.stats["bytes"] += chunk_size
                    for f in chunk:
                        if self.history:
                            self.history.mark_uploaded(f, self.cfg.target)
                    await progress.batch_done(chunk_size, len(chunk), True)
                else:
                    self.stats["fail"] += len(chunk)
                    await self.failed.add(f"Album foto {ci + 1}", reason)
                    await progress.batch_done(chunk_size, len(chunk), False)

        self._print_report(time.monotonic() - t0)
        return self.stats

    # ══════════════════════════════════════════════════════════════════
    # Report
    # ══════════════════════════════════════════════════════════════════

    def _print_report(self, elapsed: float) -> None:
        logger.info("═" * 55)
        logger.info(
            f"  Selesai dalam {format_duration(elapsed)}\n"
            f"  ✓ Berhasil   : {self.stats['ok']}\n"
            f"  ✗ Gagal      : {self.stats['fail']}\n"
            f"  ⊘ Dilewati   : {self.stats['skip']}\n"
            f"  📦 Total data : {human_size(self.stats['bytes'])}"
        )
        if elapsed > 0 and self.stats["bytes"] > 0:
            avg = self.stats["bytes"] / elapsed / 1048576
            logger.info(f"  ⚡ Rata-rata  : {avg:.1f} MB/s")
        logger.info("═" * 55)
        self.failed.print_report()


async def run_small_per_subfolder(cfg: AppConfig, accounts: AccountManager) -> dict:
    """Run small-mode upload by subfolder with concurrent preparation and ordered send."""
    stats = {"ok": 0, "fail": 0, "skip": 0, "bytes": 0}

    if not cfg.folder.is_dir():
        logger.warning("caption-per-subfolder butuh folder, bukan file.")
        return stats

    subfolders = sorted(
        d for d in cfg.folder.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )

    if not subfolders:
        logger.warning(f"Tidak ada subfolder di {cfg.folder}")
        return stats

    logger.info(f"Mode KECIL per-subfolder: {len(subfolders)} folder")

    active_workers = max(1, min(cfg.workers, accounts.count, len(subfolders)))
    logger.info(f"Worker aktif: {active_workers} (prepare paralel, kirim tetap berurutan)")

    history = UploadHistory(cfg.history_file) if cfg.skip_uploaded else None
    sem = asyncio.Semaphore(active_workers)

    async def prepare_one(folder: Path, index: int) -> SmallFolderPlan:
        slot = index % active_workers
        async with sem:
            logger.info(f"[W{slot + 1}] Prepare: {folder.name}")
            videos, photos = await asyncio.to_thread(collect_files_small_mode, folder, cfg.recursive)
            skipped: list[Path] = []
            if history:
                filtered_videos: list[Path] = []
                filtered_photos: list[Path] = []

                for v in videos:
                    if history.is_uploaded(v, cfg.target):
                        skipped.append(v)
                    else:
                        filtered_videos.append(v)

                for p in photos:
                    if history.is_uploaded(p, cfg.target):
                        skipped.append(p)
                    else:
                        filtered_photos.append(p)

                videos = filtered_videos
                photos = filtered_photos

            logger.info(
                f"[W{slot + 1}] Siap: {folder.name}  "
                f"(video={len(videos)}, foto={len(photos)}, skip={len(skipped)})"
            )
            return SmallFolderPlan(
                folder=folder,
                caption=folder.name,
                videos=videos,
                photos=photos,
                skipped=skipped,
                worker_slot=slot,
            )

    plans = await asyncio.gather(
        *(prepare_one(subfolder, idx) for idx, subfolder in enumerate(subfolders))
    )

    for idx, plan in enumerate(plans, 1):
        logger.info("─" * 55)
        logger.info(f"Subfolder {idx}/{len(subfolders)}: {plan.folder.name}")

        if plan.skipped:
            logger.info(f"  Dilewati (sudah pernah diupload): {len(plan.skipped)} file")
            for skipped in plan.skipped:
                logger.info(f"    ⊘ {skipped.name}")

        if plan.total_files == 0:
            logger.info("  Tidak ada file baru untuk diupload.")
            stats["skip"] += len(plan.skipped)
            continue

        send_acc = plan.worker_slot % max(accounts.count, 1)
        send_label = accounts.get_label(send_acc)
        logger.info(
            f"  Kirim berurutan via [W{plan.worker_slot + 1}] {send_label}  "
            f"({plan.total_files} file, {human_size(plan.total_size)})"
        )

        progress = GlobalProgress(plan.total_files, plan.total_size)
        sub_failed = FailedTracker()

        has_photos = len(plan.photos) > 0
        has_videos = len(plan.videos) > 0

        if has_videos:
            logger.info(f"  ── Video ({len(plan.videos)} file) ──")
            video_chunks = [plan.videos[i:i + ALBUM_MAX] for i in range(0, len(plan.videos), ALBUM_MAX)]
            for ci, chunk in enumerate(video_chunks):
                is_last_video_chunk = (ci == len(video_chunks) - 1)
                chunk_caption = plan.caption if is_last_video_chunk and not has_photos else ""
                if is_last_video_chunk and not has_photos and not plan.caption:
                    chunk_caption = chunk[-1].name

                client = await accounts.ensure_connected(send_acc)
                uploader = FileUploader(
                    client, cfg.target, cfg.retries,
                    cfg.thumb_size, BandwidthLimiter(cfg.speed_limit_bytes),
                    progress_prefix=send_label,
                )
                ok, reason = await uploader.upload_small_album(chunk, chunk_caption, is_video=True)

                chunk_size = sum(f.stat().st_size for f in chunk)
                if ok:
                    stats["ok"] += len(chunk)
                    stats["bytes"] += chunk_size
                    if history:
                        for f in chunk:
                            history.mark_uploaded(f, cfg.target)
                    await progress.batch_done(chunk_size, len(chunk), True)
                else:
                    stats["fail"] += len(chunk)
                    await sub_failed.add(f"Album video {ci + 1}", reason)
                    await progress.batch_done(chunk_size, len(chunk), False)

        if has_photos:
            logger.info(f"  ── Foto ({len(plan.photos)} file) ──")
            photo_chunks = [plan.photos[i:i + ALBUM_MAX] for i in range(0, len(plan.photos), ALBUM_MAX)]
            for ci, chunk in enumerate(photo_chunks):
                is_last_photo_chunk = (ci == len(photo_chunks) - 1)
                chunk_caption = plan.caption if is_last_photo_chunk else ""
                if is_last_photo_chunk and not plan.caption:
                    chunk_caption = chunk[-1].name

                client = await accounts.ensure_connected(send_acc)
                uploader = FileUploader(
                    client, cfg.target, cfg.retries,
                    cfg.thumb_size, BandwidthLimiter(cfg.speed_limit_bytes),
                    progress_prefix=send_label,
                )
                ok, reason = await uploader.upload_small_album(
                    chunk,
                    chunk_caption,
                    is_video=False,
                    as_document=cfg.photo_as_document,
                )

                chunk_size = sum(f.stat().st_size for f in chunk)
                if ok:
                    stats["ok"] += len(chunk)
                    stats["bytes"] += chunk_size
                    if history:
                        for f in chunk:
                            history.mark_uploaded(f, cfg.target)
                    await progress.batch_done(chunk_size, len(chunk), True)
                else:
                    stats["fail"] += len(chunk)
                    await sub_failed.add(f"Album foto {ci + 1}", reason)
                    await progress.batch_done(chunk_size, len(chunk), False)

        stats["skip"] += len(plan.skipped)
        sub_failed.print_report()

    logger.info("─" * 55)
    logger.info(
        f"Total per-subfolder  ✓{stats['ok']}  ✗{stats['fail']}  "
        f"⊘{stats['skip']}  📦 {human_size(stats['bytes'])}"
    )
    return stats
