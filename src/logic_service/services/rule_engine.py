"""
Rule engine: processes crossing events with debounce and persists to DB.

Debounce strategy (TTLCache, 5 minutes):
  - Known persons  : "<person_id>_<direction>"
  - Unknown persons: "unknown_<track_id>_<direction>"

This prevents the same person triggering repeated alerts within the window.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import aiosqlite
from cachetools import TTLCache

from schemas.event_models import AnimalAlertPayload, CrossingDetection, CrossingEventPayload, PasserbyEventPayload, StrangerAlertPayload

logger = logging.getLogger(__name__)

# Module-level debounce cache — survives across requests in the same process
_debounce_cache: TTLCache = TTLCache(maxsize=1000, ttl=300)  # 5 minutes


def _debounce_key(det: CrossingDetection) -> str:
    if det.person_id in ("Unknown",) or det.person_id.endswith("?"):
        return f"unknown_{det.track_id}_{det.direction}"
    return f"{det.person_id}_{det.direction}"


async def _save_to_db(det: CrossingDetection, timestamp: float, db: aiosqlite.Connection) -> None:
    event_id = str(uuid.uuid4())
    await db.execute(
        """
        INSERT OR IGNORE INTO crossing_events
            (event_id, track_id, person_id, direction, age, gender, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (event_id, det.track_id, det.person_id, det.direction, det.age, det.gender, timestamp),
    )
    await db.commit()


def _log_alert(det: CrossingDetection, timestamp: float) -> None:
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    is_stranger = det.person_id == "Unknown" or det.person_id.endswith("?")
    status = "STRANGER" if is_stranger else "KNOWN"
    arrow = "→ IN" if det.direction == "in" else "← OUT"
    age_str = str(det.age) if det.age is not None else "?"
    gender_str = det.gender if det.gender is not None else "?"
    logger.info(f"[CROSSING] {dt} | {arrow} | {det.person_id} ({status}) | age={age_str} gender={gender_str} | track_id={det.track_id}")


async def process_event(payload: CrossingEventPayload, db: aiosqlite.Connection) -> dict:
    """
    Process all detections in a crossing event payload.

    Steps per detection:
      1. Check TTLCache — skip if already processed within the debounce window.
      2. Insert into DB.
      3. Log alert.

    Returns a summary dict indicating how many detections were processed vs skipped.
    """
    processed = 0
    skipped = 0

    for det in payload.detections:
        key = _debounce_key(det)
        if key in _debounce_cache:
            skipped += 1
            logger.debug(f"[DEBOUNCE] Skipped duplicate: {key}")
            continue

        # Mark as seen before await to prevent race condition in async context
        _debounce_cache[key] = 1

        await _save_to_db(det, payload.timestamp, db)
        _log_alert(det, payload.timestamp)
        processed += 1

    return {"processed": processed, "skipped": skipped}


async def process_stranger_alert(payload: StrangerAlertPayload, db: aiosqlite.Connection) -> dict:
    """
    Process stranger alert events — save each alert to the stranger_alerts table.

    No debounce needed: the AI service controls the repeat interval.
    """
    processed = 0
    for det in payload.detections:
        event_id = str(uuid.uuid4())
        await db.execute(
            """
            INSERT OR IGNORE INTO stranger_alerts
                (event_id, track_id, person_id, age, gender, alert_count, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, det.track_id, det.person_id, det.age, det.gender, det.alert_count, payload.timestamp),
        )
        dt = datetime.fromtimestamp(payload.timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        age_str = str(det.age) if det.age is not None else "?"
        gender_str = det.gender if det.gender is not None else "?"
        logger.info(
            f"[STRANGER ALERT] {dt} | track_id={det.track_id} | alert #{det.alert_count} | "
            f"age={age_str} gender={gender_str}"
        )
        processed += 1
    await db.commit()
    return {"processed": processed}


async def process_passerby_event(payload: PasserbyEventPayload) -> dict:
    """
    Process passerby events — log only, no DB storage.

    A passerby is a stranger who appeared and disappeared in the OUT zone.
    """
    processed = 0
    for det in payload.detections:
        dt = datetime.fromtimestamp(payload.timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        age_str = str(det.age) if det.age is not None else "?"
        gender_str = det.gender if det.gender is not None else "?"
        logger.info(
            f"[PASSERBY] {dt} | track_id={det.track_id} | {det.person_id} | "
            f"age={age_str} gender={gender_str}"
        )
        processed += 1
    return {"processed": processed}


async def process_animal_alert(payload: AnimalAlertPayload) -> dict:
    """
    Process animal alert events — log only, no DB storage.
    """
    processed = 0
    for det in payload.detections:
        dt = datetime.fromtimestamp(payload.timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        logger.info(
            f"[ANIMAL ALERT] {dt} | {det.class_name} (class_id={det.class_id}) | "
            f"track_id={det.track_id} | confidence={det.confidence:.2f} | alert #{det.alert_count}"
        )
        processed += 1
    return {"processed": processed}
