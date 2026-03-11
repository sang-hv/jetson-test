"""
Logic Service — FastAPI application.

Responsibilities:
  1. Subscribe to the AI Service via ZMQ PUB/SUB and process crossing events.
  2. Expose POST /api/test/mock-event for manual Swagger UI testing.

ZMQ subscriber runs as a background asyncio task inside the FastAPI lifespan,
so it never blocks the HTTP server.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the same directory as this file
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)

from contextlib import asynccontextmanager

import zmq
import zmq.asyncio
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from database.connection import close_db, get_db, init_db
from schemas.event_models import AnimalAlertPayload, CrossingEventPayload, PasserbyEventPayload, StrangerAlertPayload
from services.rule_engine import process_animal_alert, process_event, process_passerby_event, process_stranger_alert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

ZMQ_SUB_ADDRESS = os.getenv("ZMQ_SUB_ADDRESS", "tcp://localhost:5555")
ZMQ_TOPIC = b"crossing_event"
ZMQ_STRANGER_TOPIC = b"stranger_alert"
ZMQ_PASSERBY_TOPIC = b"passerby_event"
ZMQ_ANIMAL_TOPIC = b"animal_alert"
DB_PATH = os.getenv("LOGIC_DB_PATH", "logic_service.db")

_zmq_task: asyncio.Task | None = None


async def _zmq_subscriber_loop() -> None:
    """
    Continuously receive ZMQ messages and forward to rule_engine.

    Uses zmq.asyncio so recv() is non-blocking and co-operative with the
    event loop — Uvicorn keeps processing HTTP requests concurrently.
    """
    ctx = zmq.asyncio.Context.instance()
    socket = ctx.socket(zmq.SUB)
    socket.connect(ZMQ_SUB_ADDRESS)
    socket.setsockopt(zmq.SUBSCRIBE, ZMQ_TOPIC)
    socket.setsockopt(zmq.SUBSCRIBE, ZMQ_STRANGER_TOPIC)
    socket.setsockopt(zmq.SUBSCRIBE, ZMQ_PASSERBY_TOPIC)
    socket.setsockopt(zmq.SUBSCRIBE, ZMQ_ANIMAL_TOPIC)
    logger.info(f"ZMQ subscriber connected to {ZMQ_SUB_ADDRESS}, topics=[{ZMQ_TOPIC.decode()}, {ZMQ_STRANGER_TOPIC.decode()}, {ZMQ_PASSERBY_TOPIC.decode()}, {ZMQ_ANIMAL_TOPIC.decode()}]")

    try:
        while True:
            try:
                # recv_multipart returns [topic_bytes, payload_bytes]
                parts = await socket.recv_multipart()
                if len(parts) < 2:
                    continue
                topic = parts[0]
                raw = parts[1].decode("utf-8")
                db = get_db()
                if topic == ZMQ_TOPIC:
                    payload = CrossingEventPayload.model_validate_json(raw)
                    result = await process_event(payload, db)
                elif topic == ZMQ_STRANGER_TOPIC:
                    payload = StrangerAlertPayload.model_validate_json(raw)
                    result = await process_stranger_alert(payload, db)
                elif topic == ZMQ_PASSERBY_TOPIC:
                    payload = PasserbyEventPayload.model_validate_json(raw)
                    result = await process_passerby_event(payload, db)
                elif topic == ZMQ_ANIMAL_TOPIC:
                    payload = AnimalAlertPayload.model_validate_json(raw)
                    result = await process_animal_alert(payload, db)
                else:
                    logger.warning(f"Unknown ZMQ topic: {topic}")
                    continue
                logger.debug(f"ZMQ event processed: {result}")
            except ValidationError as exc:
                logger.warning(f"Invalid ZMQ payload: {exc}")
            except asyncio.CancelledError:
                raise  # propagate cancellation
            except Exception as exc:
                logger.error(f"ZMQ subscriber error: {exc}")
    finally:
        socket.close(linger=0)
        logger.info("ZMQ subscriber stopped")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB + start ZMQ subscriber. Shutdown: cancel task + close DB."""
    global _zmq_task

    await init_db(DB_PATH)
    logger.info(f"Database initialised at {DB_PATH}")

    _zmq_task = asyncio.create_task(_zmq_subscriber_loop())

    yield  # --- application is running ---

    if _zmq_task and not _zmq_task.done():
        _zmq_task.cancel()
        try:
            await _zmq_task
        except asyncio.CancelledError:
            pass

    await close_db()
    logger.info("Logic Service shutdown complete")


app = FastAPI(
    title="Logic Service",
    description="Receives crossing events from AI Service, applies debounce and persists to DB.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.post(
    "/api/test/mock-event",
    summary="Inject a mock crossing event",
    description=(
        "Directly inject a `CrossingEventPayload` to test rule_engine logic "
        "without needing the AI Service running. Useful via Swagger UI."
    ),
)
async def mock_event(payload: CrossingEventPayload) -> dict:
    try:
        db = get_db()
        result = await process_event(payload, db)
        return {"status": "ok", **result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    "/api/test/mock-stranger-alert",
    summary="Inject a mock stranger alert",
    description=(
        "Directly inject a `StrangerAlertPayload` to test stranger alert processing "
        "without needing the AI Service running. Useful via Swagger UI."
    ),
)
async def mock_stranger_alert(payload: StrangerAlertPayload) -> dict:
    try:
        db = get_db()
        result = await process_stranger_alert(payload, db)
        return {"status": "ok", **result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/health", summary="Health check")
async def health() -> dict:
    return {"status": "ok"}
