#!/usr/bin/env python3
"""Telegram Video Uploader v5 — CLI mode."""

from __future__ import annotations

import asyncio
import sys

from config import parse_args
from account_manager import AccountManager
from queue_manager import QueueManager, run_small_per_subfolder
from utils import setup_logging, has_ffmpeg, has_ffprobe, logger


async def _main() -> None:
    setup_logging()
    cfg = parse_args()
    cfg.validate()

    logger.info("━" * 55)
    logger.info("  Telegram Video Uploader v5")
    logger.info("━" * 55)
    logger.info(f"  📁 Source      : {cfg.folder}")
    logger.info(f"  🎯 Target      : {cfg.target}")
    logger.info(f"  📦 Mode        : {cfg.upload_mode}")
    logger.info(f"  👥 Akun        : {len(cfg.accounts)}")
    logger.info(f"  ⚙️  Workers     : {cfg.workers}")
    logger.info(f"  📏 Max size    : {cfg.max_size_mb} MB")
    if cfg.upload_mode == "besar":
        logger.info(f"  📊 Sort        : {cfg.sort}")
    logger.info(f"  💬 Caption     : {cfg.caption or '(nama asli)'}")
    if cfg.upload_mode == "kecil":
        logger.info(f"  🖼️  Photo mode  : {cfg.photo_mode}")
        if cfg.caption_per_subfolder:
            logger.info("  🗂️  Subfolder   : caption otomatis nama folder")
    logger.info(f"  🗑️  Cleanup     : {'ya' if cfg.cleanup else 'tidak'}")
    if cfg.compress:
        logger.info(f"  🗜️  Compress    : ya")
    if cfg.speed_limit_mb > 0:
        logger.info(f"  🚦 Speed limit : {cfg.speed_limit_mb} MB/s")
    logger.info(f"  🎬 FFmpeg      : {'✓' if has_ffmpeg() else '✗'}")
    logger.info(f"  🔎 FFprobe     : {'✓' if has_ffprobe() else '✗'}")
    logger.info("━" * 55)

    accounts = AccountManager(cfg.api_id, cfg.api_hash, cfg.sessions_dir, cfg.accounts)
    if await accounts.initialize(max_clients=cfg.workers) == 0:
        return

    try:
        if cfg.upload_mode == "kecil" and cfg.caption_per_subfolder:
            await run_small_per_subfolder(cfg, accounts)
        else:
            pipeline = QueueManager(cfg, accounts)
            await pipeline.run()
    except KeyboardInterrupt:
        logger.info("Dihentikan.")
    except Exception as exc:
        logger.error(f"Error: {exc}", exc_info=True)
    finally:
        await accounts.disconnect_all()


def main() -> None:
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        pass
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print("\nDihentikan.")
        sys.exit(130)


if __name__ == "__main__":
    main()
