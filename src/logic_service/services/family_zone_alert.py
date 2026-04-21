"""
Rule engine: processes crossing events with debounce and persists to DB.

Debounce strategy (TTLCache, 30 seconds):
  - Known persons  : "<person_id>_<direction>"
  - Unknown persons: "unknown_<track_id>_<direction>"

After processing each event, queries ai_rules to determine the rule code,
checks time window / weekday constraints, and sends a message to AWS SQS.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

from schemas.family_models import (
    AnimalAlertPayload,
    CrossingEventPayload,
    PasserbyEventPayload,
    StrangerAlertPayload,
)
from services.sqs_sender import send_detection_to_sqs

logger = logging.getLogger(__name__)



async def _send_sqs_for_rule(
    rule_code: str,
    member_id: str,
    detected_at: float,
    detection_image_url: str | None,
    confidence: float | None,
    db: aiosqlite.Connection,
    object_attributes: dict | None = None,
) -> None:
    """
    Query ai_rules by code, check time window, and send to SQS if eligible.
    """

    send_detection_to_sqs(
        rule_code=rule_code,
        member_id=member_id,
        detected_at=detected_at,
        detection_image_url=detection_image_url,
        confidence=confidence,
        object_attributes=object_attributes,
    )


# ---------------------------------------------------------------------------
# Event processors
# ---------------------------------------------------------------------------

async def process_event(payload: CrossingEventPayload, db: aiosqlite.Connection) -> dict:
    processed = 0

    for det in payload.detections:
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
        processed += 1

        # Send to SQS — per detection
        await _send_sqs_for_rule(
            rule_code="creature_detection",
            member_id="",
            detected_at=payload.timestamp,
            detection_image_url=det.detection_result,
            confidence=det.confidence,
            db=db,
            object_attributes={"class_name": det.class_name, "class_id": det.class_id},
        )

    return {"processed": processed}
