"""Structured logging configuration."""

from __future__ import annotations

import logging
import sys
from typing import Optional

from app.core.config import get_settings


def setup_logging(level: Optional[str] = None) -> None:
    settings = get_settings()
    log_level = level or settings.log_level

    fmt = (
        "%(asctime)s │ %(levelname)-8s │ %(name)-30s │ %(message)s"
    )
    datefmt = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format=fmt,
        datefmt=datefmt,
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )

    # Quieten noisy third-party loggers
    for noisy in ("uvicorn.access", "httpcore", "httpx", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)