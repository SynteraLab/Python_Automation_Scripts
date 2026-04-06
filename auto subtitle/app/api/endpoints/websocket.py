"""WebSocket endpoint for real-time subtitle generation."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.logging_config import get_logger
from app.models.schemas import RealtimeConfig
from app.services.realtime_service import RealtimeServiceFactory

logger = get_logger(__name__)

router = APIRouter(tags=["Realtime"])


@router.websocket("/ws/realtime-subtitle")
async def realtime_subtitle(ws: WebSocket) -> None:
    """
    WebSocket protocol:
      1. Client sends JSON config: {"language": "en", "model_size": "base", "sample_rate": 16000}
      2. Client sends binary frames of raw PCM int16 mono audio
      3. Server sends JSON subtitle messages back
      4. Client sends text "STOP" to end session
    """
    await ws.accept()
    logger.info("WebSocket client connected")

    session = None
    try:
        # 1 — Receive configuration
        config_raw = await ws.receive_text()
        config = RealtimeConfig.model_validate_json(config_raw)
        logger.info("RT config: %s", config)

        session = RealtimeServiceFactory.create_session(
            model_size=config.model_size,
            language=config.language,
            sample_rate=config.sample_rate,
        )

        await ws.send_json({"status": "ready", "message": "Send audio data"})

        # 2 — Receive audio loop
        while True:
            message = await ws.receive()

            # Text message = control
            if "text" in message:
                text = message["text"].strip().upper()
                if text == "STOP":
                    # Flush remaining audio
                    msgs = session.flush()
                    for m in msgs:
                        await ws.send_json(m.model_dump())
                    await ws.send_json({"status": "stopped"})
                    break
                continue

            # Binary message = audio data
            if "bytes" in message:
                pcm_data: bytes = message["bytes"]
                results = session.feed_audio(pcm_data)
                for msg in results:
                    await ws.send_json(msg.model_dump())

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as exc:
        logger.exception("WebSocket error")
        try:
            await ws.send_json({"status": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        if session:
            session.reset()
        logger.info("WebSocket session ended")