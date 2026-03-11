"""
Rule engine: processes crossing events with debounce and persists to DB.

Debounce strategy (TTLCache, 30 seconds):
  - Known persons  : "<person_id>_<direction>"
  - Unknown persons: "unknown_<track_id>_<direction>"

After processing each event, queries ai_rules to determine the rule code,
checks time window / weekday constraints, and sends a message to AWS SQS.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, time, timezone

import aiosqlite
from cachetools import TTLCache

from schemas.event_models import (
    AnimalAlertPayload,
    CrossingDetection,
    CrossingEventPayload,
    PasserbyEventPayload,
    StrangerAlertPayload,
)
from services.sqs_sender import send_detection_to_sqs

logger = logging.getLogger(__name__)

# Module-level debounce cache — survives across requests in the same process
_debounce_cache: TTLCache = TTLCache(maxsize=1000, ttl=30)  # 30s


# ---------------------------------------------------------------------------
# ai_rules helpers
# ---------------------------------------------------------------------------

async def _get_rule_by_code(code: str, db: aiosqlite.Connection) -> dict | None:
    """Fetch an ai_rule row by its code. Returns dict or None."""
    cursor = await db.execute(
        "SELECT * FROM ai_rules WHERE code = ?",
        (code,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    # aiosqlite.Row → dict
    return dict(row)


def _is_within_time_window(rule: dict, event_timestamp: float) -> bool:
    """
    Check whether the event falls within the rule's active time window
    and weekday constraints.

    Returns True if:
      1. is_active == 1
      2. Current weekday (1=Mon .. 7=Sun) is in the rule's weekdays list
      3. Current time is between start_time and end_time
    """
    # Check is_active
    if not rule.get("is_active", 0):
        logger.info(f"[SQS SKIP] Rule {rule.get('code')} is_active=0 — skipping")
        return False

    # Parse event time in UTC+9 (JST — Japan Standard Time)
    from zoneinfo import ZoneInfo
    jst = ZoneInfo("Asia/Tokyo")
    event_dt = datetime.fromtimestamp(event_timestamp, tz=jst)

    # Check weekdays: stored as JSON list like [1, 2, 3, 4, 5]
    # 1=Monday ... 7=Sunday  (ISO weekday format)
    weekdays_raw = rule.get("weekdays")
    if weekdays_raw:
        try:
            weekdays = json.loads(weekdays_raw) if isinstance(weekdays_raw, str) else weekdays_raw
            current_weekday = event_dt.isoweekday()  # 1=Mon, 7=Sun
            if current_weekday not in weekdays:
                logger.info(
                    f"[SQS SKIP] Rule {rule.get('code')}: weekday {current_weekday} not in {weekdays}"
                )
                return False
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"Rule {rule.get('code')}: invalid weekdays format: {weekdays_raw}")

    # Check time window
    start_time_str = rule.get("start_time")
    end_time_str = rule.get("end_time")
    if start_time_str and end_time_str:
        try:
            start_t = time.fromisoformat(start_time_str)
            end_t = time.fromisoformat(end_time_str)
            current_t = event_dt.time()

            if start_t <= end_t:
                # Normal window: e.g. 08:00 - 18:00
                if not (start_t <= current_t <= end_t):
                    logger.info(
                        f"[SQS SKIP] Rule {rule.get('code')}: time {current_t} not in [{start_t}, {end_t}]"
                    )
                    return False
            else:
                # Overnight window: e.g. 22:00 - 06:00
                if not (current_t >= start_t or current_t <= end_t):
                    logger.info(
                        f"[SQS SKIP] Rule {rule.get('code')}: time {current_t} not in [{start_t}, {end_t}] (overnight)"
                    )
                    return False
        except (ValueError, TypeError) as exc:
            logger.warning(f"Rule {rule.get('code')}: invalid time format: {exc}")

    return True


async def _send_sqs_for_rule(
    rule_code: str,
    member_id: str,
    detected_at: float,
    detection_image_url: str | None,
    confidence: float | None,
    db: aiosqlite.Connection,
) -> None:
    """
    Query ai_rules by code, check time window, and send to SQS if eligible.
    """
    rule = await _get_rule_by_code(rule_code, db)
    if rule is None:
        logger.warning(f"No ai_rule found for code={rule_code} — skipping SQS")
        return

    if not _is_within_time_window(rule, detected_at):
        return

    send_detection_to_sqs(
        rule_code=rule_code,
        member_id=member_id,
        camera_id=rule.get("camera_id", ""),
        detected_at=detected_at,
        detection_image_url=detection_image_url,
        confidence=confidence,
        object_attributes={},
    )


# ---------------------------------------------------------------------------
# Debounce helpers (crossing events only)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Event processors
# ---------------------------------------------------------------------------

async def process_event(payload: CrossingEventPayload, db: aiosqlite.Connection) -> dict:
    """
    Process all detections in a crossing event payload.
    Rule code: home_return_count

    Steps per detection:
      1. Check TTLCache — skip if already processed within the debounce window.
      2. Insert into DB.
      3. Log alert.
      4. Send to SQS (member_id = det.person_id).
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

        # Send to SQS — only for recognised members (skip Unknown / uncertain)
        if det.person_id and det.person_id != "Unknown" and not det.person_id.endswith("?"):
            await _send_sqs_for_rule(
                rule_code="home_return_count",
                member_id=det.person_id,
                detected_at=payload.timestamp,
                detection_image_url=det.detection_result,
                confidence=det.confidence,
                db=db,
            )

    return {"processed": processed, "skipped": skipped}


async def process_stranger_alert(payload: StrangerAlertPayload, db: aiosqlite.Connection) -> dict:
    """
    Process stranger alert events — save each alert to the stranger_alerts table.
    Rule code: unregistered_detection
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

        # Send to SQS — per detection
        await _send_sqs_for_rule(
            rule_code="unregistered_detection",
            member_id="",
            detected_at=payload.timestamp,
            detection_image_url=det.detection_result,
            confidence=det.confidence,
            db=db,
        )

    await db.commit()
    return {"processed": processed}


async def process_passerby_event(payload: PasserbyEventPayload, db: aiosqlite.Connection) -> dict:
    """
    Process passerby events — log + send to SQS.
    Rule code: daily_passerby
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

        # Send to SQS — per detection
        await _send_sqs_for_rule(
            rule_code="daily_passerby",
            member_id="",
            detected_at=payload.timestamp,
            detection_image_url=det.detection_result,
            confidence=det.confidence,
            db=db,
        )

    return {"processed": processed}


async def process_animal_alert(payload: AnimalAlertPayload, db: aiosqlite.Connection) -> dict:
    """
    Process animal alert events — log + send to SQS.
    Rule code: creature_detection
    """
    processed = 0
    for det in payload.detections:
        dt = datetime.fromtimestamp(payload.timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        logger.info(
            f"[ANIMAL ALERT] {dt} | {det.class_name} (class_id={det.class_id}) | "
            f"track_id={det.track_id} | confidence={det.confidence:.2f} | alert #{det.alert_count}"
        )
        processed += 1

        # Send to SQS — per detection
        await _send_sqs_for_rule(
            rule_code="creature_detection",
            member_id="",
            detected_at=payload.timestamp,
            detection_image_url=det.detection_result,
            confidence=det.confidence,
            db=db,
        )

    return {"processed": processed}
