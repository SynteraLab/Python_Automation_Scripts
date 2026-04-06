"""Telegram uploader: video, album, photo."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, cast

from telethon import TelegramClient, utils as tg_utils
from telethon.errors import (
    FloodWaitError, FilePartsInvalidError, PhotoSaveFileInvalidError,
    MediaEmptyError, ChatWriteForbiddenError, ChannelPrivateError,
    FileReferenceExpiredError,
)
from telethon.tl import functions, types
from telethon.tl.types import DocumentAttributeVideo, DocumentAttributeFilename

from utils import (
    AlbumProgress, UploadProgress, VideoMeta, BandwidthLimiter,
    human_size, probe_video, resolve_thumbnail, cleanup_thumbnail,
)

logger = logging.getLogger("tg_uploader")

ALBUM_MAX = 10


class FileUploader:
    def __init__(
        self, client: TelegramClient, target: str | int,
        max_retries: int = 5, thumb_size: int = 720,
        bandwidth_limiter: BandwidthLimiter | None = None,
        progress_prefix: str = "",
    ) -> None:
        self.client = client
        self.target = target
        self.max_retries = max_retries
        self.thumb_size = thumb_size
        self.limiter = bandwidth_limiter
        self.progress_prefix = progress_prefix
        self._entity: Any = None

    async def _limit_upload(self, total_bytes: int) -> None:
        if self.limiter and self.limiter.rate > 0 and total_bytes > 0:
            await self.limiter.acquire(total_bytes)

    # ══════════════════════════════════════════════════════════════════
    # Single video upload (with metadata + thumbnail)
    # ══════════════════════════════════════════════════════════════════

    async def upload_video(
        self, filepath: Path, caption: str,
        manual_thumb: Path | None = None,
    ) -> tuple[bool, str]:
        entity = await self._resolve_target()
        if not entity:
            return False, "Resolve target gagal"
        if not filepath.exists() or filepath.stat().st_size == 0:
            return False, "File kosong"

        meta = probe_video(filepath)
        attrs = self._video_attrs(filepath, meta)

        if manual_thumb and manual_thumb.exists():
            thumb_path, thumb_is_manual = manual_thumb, True
        else:
            thumb_path, thumb_is_manual = resolve_thumbnail(filepath, meta, self.thumb_size)

        logger.info(
            f"  Upload: {filepath.name}  ({human_size(filepath.stat().st_size)}"
            f"{f', {meta.resolution_str}' if meta.width else ''}"
            f"{f', {meta.duration_int}s' if meta.duration > 0 else ''})"
            f"{'  [thumb: manual]' if thumb_is_manual else ''}"
        )

        progress = UploadProgress(filepath.name, self.progress_prefix)
        backoff = 1
        use_thumb = thumb_path
        try:
            for attempt in range(1, self.max_retries + 1):
                try:
                    if not self.client.is_connected():
                        await self.client.connect()
                    await self._limit_upload(filepath.stat().st_size)

                    kwargs: dict = dict(
                        entity=entity, file=str(filepath), caption=caption,
                        supports_streaming=True, progress_callback=progress,
                        attributes=attrs, force_document=False,
                    )
                    if use_thumb and use_thumb.exists():
                        kwargs["thumb"] = str(use_thumb)

                    await self.client.send_file(**kwargs)
                    logger.info(f"  ✓ {filepath.name}")
                    return True, ""

                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 2)
                    continue
                except (ChatWriteForbiddenError, ChannelPrivateError) as exc:
                    return False, f"Akses ditolak: {exc}"
                except (FilePartsInvalidError, PhotoSaveFileInvalidError):
                    use_thumb = None  # retry without thumb
                except MediaEmptyError:
                    return False, "Media kosong"
                except (ConnectionError, OSError, asyncio.TimeoutError, FileReferenceExpiredError) as exc:
                    logger.warning(f"  ↻ ({attempt}/{self.max_retries}): {type(exc).__name__}")
                except Exception as exc:
                    logger.error(f"  ✗ ({attempt}/{self.max_retries}): {exc}")

                if attempt < self.max_retries:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30)

            return False, "Upload gagal"
        finally:
            progress.close()
            if not thumb_is_manual:
                cleanup_thumbnail(thumb_path)

    # ══════════════════════════════════════════════════════════════════
    # Single photo upload
    # ══════════════════════════════════════════════════════════════════

    async def upload_photo(
        self, filepath: Path, caption: str, as_document: bool = False,
    ) -> tuple[bool, str]:
        entity = await self._resolve_target()
        if not entity:
            return False, "Resolve target gagal"

        mode_str = "dokumen" if as_document else "foto"
        logger.info(f"  Upload foto: {filepath.name}  [{mode_str}]")

        progress = UploadProgress(filepath.name, self.progress_prefix)
        backoff = 1
        try:
            for attempt in range(1, self.max_retries + 1):
                try:
                    if not self.client.is_connected():
                        await self.client.connect()
                    await self._limit_upload(filepath.stat().st_size)
                    await self.client.send_file(
                        entity, file=str(filepath),
                        caption=caption, force_document=as_document,
                        progress_callback=progress,
                    )
                    logger.info(f"  ✓ {filepath.name}")
                    return True, ""
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 2)
                    continue
                except (ChatWriteForbiddenError, ChannelPrivateError) as exc:
                    return False, f"Akses ditolak: {exc}"
                except Exception as exc:
                    logger.warning(f"  ↻ ({attempt}/{self.max_retries}): {type(exc).__name__}")
                if attempt < self.max_retries:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30)
            return False, "Upload foto gagal"
        finally:
            progress.close()

    # ══════════════════════════════════════════════════════════════════
    # Album: video parts (for split files in big mode)
    # Uses raw grouped-media request so metadata stays intact
    # ══════════════════════════════════════════════════════════════════

    async def upload_video_album(
        self, parts: list[Path], caption: str,
    ) -> tuple[bool, str]:
        """Upload split video parts as real Telegram album(s)."""
        entity = await self._resolve_target()
        if not entity:
            return False, "Resolve target gagal"

        if len(parts) == 1:
            return await self.upload_video(parts[0], caption)

        chunks = [parts[i:i + ALBUM_MAX] for i in range(0, len(parts), ALBUM_MAX)]
        all_ok = True
        last_err = ""

        for ci, chunk in enumerate(chunks):
            is_last_chunk = (ci == len(chunks) - 1)
            chunk_caption = caption if is_last_chunk else ""

            if len(chunk) == 1:
                ok, reason = await self.upload_video(chunk[0], chunk_caption)
            else:
                ok, reason = await self._send_split_video_album(entity, chunk, chunk_caption)

            if not ok:
                all_ok = False
                last_err = reason

        return all_ok, last_err

    async def _send_split_video_album(
        self, entity: Any, files: list[Path], caption: str,
    ) -> tuple[bool, str]:
        return await self._send_raw_video_album(
            entity, files, caption,
            log_prefix="Album split",
            progress_name=f"album split ({len(files)} file)",
            success_label=f"Album split terkirim ({len(files)} parts)",
        )

    # ══════════════════════════════════════════════════════════════════
    # Album: small files (for small mode)
    # ══════════════════════════════════════════════════════════════════

    async def upload_small_album(
        self, files: list[Path], caption: str,
        is_video: bool = False, as_document: bool = False,
    ) -> tuple[bool, str]:
        """Upload small files as album(s). Caption on last file of last album."""
        entity = await self._resolve_target()
        if not entity:
            return False, "Resolve target gagal"

        if len(files) == 1:
            if is_video:
                return await self.upload_video(files[0], caption)
            else:
                return await self.upload_photo(files[0], caption, as_document=as_document)

        chunks = [files[i:i + ALBUM_MAX] for i in range(0, len(files), ALBUM_MAX)]
        all_ok = True
        last_err = ""

        for ci, chunk in enumerate(chunks):
            is_last_chunk = (ci == len(chunks) - 1)
            chunk_caption = caption if is_last_chunk else ""

            if len(chunk) == 1:
                if is_video:
                    ok, reason = await self.upload_video(chunk[0], chunk_caption)
                else:
                    ok, reason = await self.upload_photo(chunk[0], chunk_caption, as_document=as_document)
            else:
                ok, reason = await self._send_album(
                    entity, chunk, chunk_caption,
                    is_video=is_video, as_document=as_document,
                )

            if not ok:
                all_ok = False
                last_err = reason

        return all_ok, last_err

    # ══════════════════════════════════════════════════════════════════
    # Core album senders
    # ══════════════════════════════════════════════════════════════════

    async def _send_album(
        self, entity: Any, files: list[Path], caption: str,
        is_video: bool = False, as_document: bool = False,
    ) -> tuple[bool, str]:
        """Route to video or photo album sender."""
        if is_video:
            return await self._send_video_album(entity, files, caption)
        else:
            return await self._send_photo_album(entity, files, caption, as_document)

    async def _send_video_album(
        self, entity: Any, files: list[Path], caption: str,
    ) -> tuple[bool, str]:
        return await self._send_raw_video_album(
            entity, files, caption,
            log_prefix="Album video",
            progress_name=f"album video ({len(files)} file)",
            success_label=f"Album video terkirim ({len(files)} files)",
        )

    async def _send_photo_album(
        self, entity: Any, files: list[Path], caption: str,
        as_document: bool = False,
    ) -> tuple[bool, str]:
        """Send photo album using Telethon's native list support."""
        logger.info(f"  Album foto: mengirim {len(files)} files...")

        captions = [""] * len(files)
        if caption:
            captions[-1] = caption

        file_paths = [str(f) for f in files]
        backoff = 1

        for attempt in range(1, self.max_retries + 1):
            try:
                if not self.client.is_connected():
                    await self.client.connect()
                await self._limit_upload(sum(f.stat().st_size for f in files))

                await self.client.send_file(
                    entity,
                    file=file_paths,
                    caption=captions,
                    force_document=as_document,
                )
                logger.info(f"  ✓ Album foto terkirim ({len(files)} files)")
                return True, ""

            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + 2)
                continue
            except (ChatWriteForbiddenError, ChannelPrivateError) as exc:
                return False, f"Akses ditolak: {exc}"
            except Exception as exc:
                logger.warning(f"  ↻ Album foto ({attempt}): {type(exc).__name__}: {exc}")
                if attempt < self.max_retries:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30)

        return False, f"Album foto gagal setelah {self.max_retries} percobaan"

    # ══════════════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════════════

    def _video_attrs(self, filepath: Path, meta: VideoMeta) -> list:
        """NEVER send w=0 h=0 — causes Telegram crash."""
        attrs = []
        if meta.width > 0 and meta.height > 0:
            attrs.append(DocumentAttributeVideo(
                duration=meta.duration_int, w=meta.width, h=meta.height,
                supports_streaming=True,
            ))
        elif meta.duration > 0:
            # Have duration but no resolution — safe default
            attrs.append(DocumentAttributeVideo(
                duration=meta.duration_int, w=1280, h=720,
                supports_streaming=True,
            ))
        # else: skip video attr entirely, let Telegram auto-detect
        attrs.append(DocumentAttributeFilename(file_name=filepath.name))
        return attrs

    async def _send_raw_video_album(
        self, entity: Any, files: list[Path], caption: str,
        *, log_prefix: str, progress_name: str, success_label: str,
    ) -> tuple[bool, str]:
        logger.info(f"  {log_prefix}: mengirim {len(files)} files...")

        captions = [""] * len(files)
        if caption:
            captions[-1] = caption

        total_size = sum(f.stat().st_size for f in files)
        progress = AlbumProgress(progress_name, self.progress_prefix)
        backoff = 1
        use_thumbs = True

        try:
            for attempt in range(1, self.max_retries + 1):
                try:
                    if not self.client.is_connected():
                        await self.client.connect()
                    await self._limit_upload(total_size)

                    input_entity = await self.client.get_input_entity(entity)
                    media = await self._build_video_album_media(
                        input_entity, files, captions, progress, use_thumbs,
                    )
                    await self.client(functions.messages.SendMultiMediaRequest(
                        peer=input_entity,
                        multi_media=media,
                    ))
                    logger.info(f"  ✓ {success_label}")
                    return True, ""

                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 2)
                    continue
                except (ChatWriteForbiddenError, ChannelPrivateError) as exc:
                    return False, f"Akses ditolak: {exc}"
                except (FilePartsInvalidError, PhotoSaveFileInvalidError):
                    if use_thumbs:
                        logger.warning(f"  ↻ {log_prefix}: retry tanpa thumbnail")
                        use_thumbs = False
                        continue
                    logger.warning(f"  ↻ {log_prefix} ({attempt}): thumbnail/part invalid")
                except MediaEmptyError:
                    return False, "Media kosong"
                except Exception as exc:
                    logger.warning(f"  ↻ {log_prefix} ({attempt}): {type(exc).__name__}: {exc}")

                if attempt < self.max_retries:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30)

            return False, f"{log_prefix} gagal setelah {self.max_retries} percobaan"
        finally:
            progress.close()

    async def _build_video_album_media(
        self,
        input_entity: Any,
        files: list[Path],
        captions: list[str],
        progress: AlbumProgress,
        use_thumbs: bool,
    ) -> list[types.InputSingleMedia]:
        total_files = len(files)
        cleanup_paths: list[Path] = []
        media: list[types.InputSingleMedia] = []

        try:
            for sent_count, filepath in enumerate(files):
                meta = probe_video(filepath)
                attrs = self._video_attrs(filepath, meta)
                thumb_path: Path | None = None
                thumb_is_manual = False

                if use_thumbs:
                    thumb_path, thumb_is_manual = resolve_thumbnail(filepath, meta, self.thumb_size)
                    if thumb_path and not thumb_is_manual:
                        cleanup_paths.append(thumb_path)

                callback = lambda sent, total, index=sent_count: progress(  # noqa: E731
                    index + 1 if sent == total else index + sent / total,
                    total_files,
                )

                _, file_media, _ = await self.client._file_to_media(
                    filepath,
                    supports_streaming=True,
                    force_document=False,
                    progress_callback=callback,
                    attributes=attrs,
                    thumb=thumb_path,
                    nosound_video=True,
                )

                if file_media is None:
                    raise ValueError(f"Gagal menyiapkan media: {filepath.name}")

                if isinstance(file_media, types.InputMediaUploadedDocument):
                    uploaded: Any = await self.client(functions.messages.UploadMediaRequest(
                        input_entity,
                        media=file_media,
                    ))
                    if not isinstance(uploaded, types.MessageMediaDocument):
                        raise ValueError(f"Gagal upload media album: {filepath.name}")
                    document_media = cast(types.MessageMediaDocument, uploaded)
                    file_media = tg_utils.get_input_media(
                        document_media.document,
                        supports_streaming=True,
                    )

                caption_text = captions[sent_count] if sent_count < len(captions) else ""
                parsed_caption, entities = await self.client._parse_message_text(caption_text, ())
                media.append(types.InputSingleMedia(
                    media=file_media,
                    message=parsed_caption,
                    entities=entities,
                ))

            return media
        finally:
            for path in cleanup_paths:
                cleanup_thumbnail(path)

    async def _resolve_target(self):
        if self._entity:
            return self._entity
        try:
            if not self.client.is_connected():
                await self.client.connect()
            if self.target == "me":
                self._entity = await self.client.get_me()
            else:
                try:
                    self._entity = await self.client.get_entity(int(self.target))
                except ValueError:
                    self._entity = await self.client.get_entity(self.target)
            return self._entity
        except Exception as exc:
            logger.error(f"  Resolve target gagal: {exc}")
            return None
