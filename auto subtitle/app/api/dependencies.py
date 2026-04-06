"""FastAPI dependency injection helpers."""

from __future__ import annotations

from fastapi import Depends, Request
import redis.asyncio as aioredis

from app.services.job_service import JobService


async def get_redis(request: Request) -> aioredis.Redis:
    return request.app.state.redis


async def get_job_service(
    redis: aioredis.Redis = Depends(get_redis),
) -> JobService:
    return JobService(redis)