"""Aggregate all endpoint routers."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.endpoints import download, status, subtitle, upload, websocket

api_router = APIRouter()

api_router.include_router(upload.router)
api_router.include_router(subtitle.router)
api_router.include_router(status.router)
api_router.include_router(download.router)
api_router.include_router(websocket.router)