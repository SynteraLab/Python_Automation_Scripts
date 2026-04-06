"""Job tracking backed by Redis hashes."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import redis.asyncio as aioredis

from app.core.exceptions import JobNotFoundError
from app.core.logging_config import get_logger
from app.models.schemas import JobStatus, JobStatusResponse

logger = get_logger(__name__)

_KEY_PREFIX = "subtitle:job:"
_TTL_SECONDS = 86400 * 7  # keep jobs for 7 days


class JobService:
    """CRUD operations for async jobs stored in Redis."""

    def __init__(self, redis: aioredis.Redis):
        self._r = redis

    async def create_job(self, metadata: Optional[Dict[str, Any]] = None) -> str:
        job_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        data = {
            "status": JobStatus.PENDING.value,
            "progress": "0",
            "message": "Job created",
            "result": "",
            "error": "",
            "created_at": now,
            "updated_at": now,
        }
        if metadata:
            data["metadata"] = json.dumps(metadata)
        await self._r.hset(self._key(job_id), mapping=data)
        await self._r.expire(self._key(job_id), _TTL_SECONDS)
        logger.info("Job created: %s", job_id)
        return job_id

    async def update(
        self,
        job_id: str,
        status: Optional[JobStatus] = None,
        progress: Optional[float] = None,
        message: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        key = self._key(job_id)
        if not await self._r.exists(key):
            raise JobNotFoundError(job_id)
        updates: Dict[str, str] = {
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        if status is not None:
            updates["status"] = status.value
        if progress is not None:
            updates["progress"] = str(progress)
        if message is not None:
            updates["message"] = message
        if result is not None:
            updates["result"] = json.dumps(result)
        if error is not None:
            updates["error"] = error
        await self._r.hset(key, mapping=updates)

    async def get(self, job_id: str) -> JobStatusResponse:
        key = self._key(job_id)
        data = await self._r.hgetall(key)
        if not data:
            raise JobNotFoundError(job_id)

        result_raw = data.get("result", "")
        result = json.loads(result_raw) if result_raw else None

        return JobStatusResponse(
            job_id=job_id,
            status=JobStatus(data.get("status", "pending")),
            progress=float(data.get("progress", 0)),
            message=data.get("message", ""),
            result=result,
            error=data.get("error") or None,
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    def _key(self, job_id: str) -> str:
        return f"{_KEY_PREFIX}{job_id}"


# ── Synchronous variant for Celery workers ──────────────────────

class JobServiceSync:
    """Same interface but with synchronous redis-py."""

    def __init__(self, redis_url: str):
        import redis as sync_redis
        self._r = sync_redis.Redis.from_url(redis_url, decode_responses=True)

    def update(
        self,
        job_id: str,
        status: Optional[JobStatus] = None,
        progress: Optional[float] = None,
        message: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        key = f"{_KEY_PREFIX}{job_id}"
        updates: Dict[str, str] = {
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        if status is not None:
            updates["status"] = status.value
        if progress is not None:
            updates["progress"] = str(progress)
        if message is not None:
            updates["message"] = message
        if result is not None:
            updates["result"] = json.dumps(result)
        if error is not None:
            updates["error"] = error
        self._r.hset(key, mapping=updates)