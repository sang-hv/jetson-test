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
from schemas.enterprise_models import EmployeeCrossingPayload, RestrictedZoneAlertPayload
from schemas.family_models import AnimalAlertPayload, CrossingEventPayload, PasserbyEventPayload, StrangerAlertPayload
from schemas.shop_models import ShopPersonEventPayload
from services.family_zone_alert import process_animal_alert, process_event, process_passerby_event, process_stranger_alert
from services.shop_zone_alert import process_shop_zone_sqs_event
from services.enterprise_zone_alert import process_employee_crossing_zone_alert, process_restricted_zone_alert

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
ZMQ_PERSON_COUNT_TOPIC = b"person_count"

# shop topics
ZMQ_ZONE_ENTRY_TOPIC = b"zone_entry"
ZMQ_ZONE_EXIT_TOPIC = b"zone_exit"

# enterprise topics
ZMQ_EMPLOYEE_CROSSING_TOPIC = b"employee_crossing"
ZMQ_RESTRICTED_ZONE_ALERT_TOPIC = b"restricted_zone_alert"

DB_PATH = os.getenv("LOGIC_DB_PATH", "logic_service.db")

_zmq_task: asyncio.Task | None = None

async def _get_camera_facility(db) -> str | None:
    """
    Read `camera_settings` with key='facility' (value='Family' or 'Store').
    Table structure is: (key TEXT UNIQUE, value TEXT NOT NULL).
    """
    cursor = await db.execute(
        "SELECT value FROM camera_settings WHERE key = ?",
        ("facility",),
    )
    row = await cursor.fetchone()
    return row[0] if row is not None else None


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
    socket.setsockopt(zmq.SUBSCRIBE, ZMQ_PERSON_COUNT_TOPIC)
    socket.setsockopt(zmq.SUBSCRIBE, ZMQ_ZONE_ENTRY_TOPIC)
    socket.setsockopt(zmq.SUBSCRIBE, ZMQ_ZONE_EXIT_TOPIC)
    socket.setsockopt(zmq.SUBSCRIBE, ZMQ_EMPLOYEE_CROSSING_TOPIC)
    socket.setsockopt(zmq.SUBSCRIBE, ZMQ_RESTRICTED_ZONE_ALERT_TOPIC)
    all_topics = [
        ZMQ_TOPIC, ZMQ_STRANGER_TOPIC, ZMQ_PASSERBY_TOPIC,
        ZMQ_ANIMAL_TOPIC, ZMQ_PERSON_COUNT_TOPIC,
        ZMQ_ZONE_ENTRY_TOPIC, ZMQ_ZONE_EXIT_TOPIC,
        ZMQ_EMPLOYEE_CROSSING_TOPIC, ZMQ_RESTRICTED_ZONE_ALERT_TOPIC,
    ]
    logger.info(f"ZMQ subscriber connected to {ZMQ_SUB_ADDRESS}, topics={[t.decode() for t in all_topics]}")

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

                # Decide which pipeline to run based on sqlite camera_settings.
                facility = await _get_camera_facility(db)
                handled = False
                result = None

                if facility == "Family":
                    if topic == ZMQ_TOPIC:
                        payload = CrossingEventPayload.model_validate_json(raw)
                        result = await process_event(payload, db)
                        handled = True
                    elif topic == ZMQ_STRANGER_TOPIC:
                        payload = StrangerAlertPayload.model_validate_json(raw)
                        result = await process_stranger_alert(payload, db)
                        handled = True
                    elif topic == ZMQ_PASSERBY_TOPIC:
                        payload = PasserbyEventPayload.model_validate_json(raw)
                        result = await process_passerby_event(payload, db)
                        handled = True
                    elif topic == ZMQ_ANIMAL_TOPIC:
                        payload = AnimalAlertPayload.model_validate_json(raw)
                        result = await process_animal_alert(payload, db)
                        handled = True
                    elif topic in (ZMQ_ZONE_ENTRY_TOPIC, ZMQ_ZONE_EXIT_TOPIC):
                        # Store-only topics — ignore for Family cameras.
                        handled = False
                    else:
                        logger.warning(f"Unknown ZMQ topic: {topic}")
                        continue
                elif facility == "Store":
                    if topic in (ZMQ_ZONE_ENTRY_TOPIC, ZMQ_ZONE_EXIT_TOPIC):
                        payload = ShopPersonEventPayload.model_validate_json(raw)
                        position = "in" if topic == ZMQ_ZONE_ENTRY_TOPIC else "out"
                        result = await process_shop_zone_sqs_event(payload, position)
                        handled = True
                    else:
                        # Family-only topics — ignore for Store cameras.
                        handled = False
                elif facility == "Enterprise":
                    if topic == ZMQ_EMPLOYEE_CROSSING_TOPIC:
                        payload = EmployeeCrossingPayload.model_validate_json(raw)
                        await process_employee_crossing_zone_alert(payload)
                        handled = True
                    elif topic == ZMQ_RESTRICTED_ZONE_ALERT_TOPIC:
                        payload = RestrictedZoneAlertPayload.model_validate_json(raw)
                        await process_restricted_zone_alert(payload)
                        handled = True
                    else:
                        handled = False
                else:
                    # Only run Family/Store/Enterprise pipelines as requested.
                    logger.warning(
                        f"camera_settings.facility is not set to 'Family'/'Store'/'Enterprise' (got {facility!r}) — skipping"
                    )
                    continue

                if handled:
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
