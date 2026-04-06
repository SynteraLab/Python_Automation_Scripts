"""FastAPI lifespan events — startup / shutdown hooks."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import redis.asyncio as aioredis
from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging_config import get_logger, setup_logging

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle."""
    settings = get_settings()
    setup_logging()
    settings.ensure_dirs()

    # Redis connection pool
    pool = aioredis.ConnectionPool.from_url(
        settings.redis_url, decode_responses=True
    )
    app.state.redis = aioredis.Redis(connection_pool=pool)
    logger.info("Redis connected (%s)", settings.redis_url)

    logger.info(
        "%s v%s started — debug=%s",
        settings.app_name,
        settings.app_version,
        settings.debug,
    )

    yield  # ── application runs here ──

    await app.state.redis.aclose()
    await pool.disconnect()
    logger.info("Shutdown complete.")