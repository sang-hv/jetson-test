"""
Hospital fall alerts: push detections to SQS with no rule or time-window checks.

Mirrors the Enterprise zone pattern — callers do not pass camera_id
(resolved from DEVICE_ID inside `send_detection_to_sqs`).
"""

from __future__ import annotations

import logging

from schemas.hospital_models import FallDetectedPayload
from services.sqs_sender import send_detection_to_sqs

logger = logging.getLogger(__name__)


async def process_fall_detected_alert(payload: FallDetectedPayload) -> dict:
    """Send each confirmed fall detection to SQS."""
    processed = 0
    for det in payload.detections:
        send_detection_to_sqs(
            rule_code="hospital",
            member_id="",
            detected_at=payload.timestamp,
            detection_image_url=det.detection_result,
            confidence=det.confidence,
            object_attributes={
                "position": "unexpected_incident"
            },
        )
        processed += 1
    logger.info(f"[HOSPITAL ZONE] processed={processed} position='fall_detected'")
    return {"processed": processed}
