"""
Enterprise zone alerts: push detections to SQS with no rule or time-window checks.

Field mapping matches `send_detection_to_sqs` usage in `rule_engine` (member_id from
person_id, detection_image_url from detection_result, etc.). Camera id is taken from
`DEVICE_ID` via `send_detection_to_sqs` (callers do not pass camera_id).
"""

from __future__ import annotations

import logging

from schemas.enterprise_models import RestrictedZoneAlertPayload, EmployeeCrossingPayload
from services.sqs_sender import send_detection_to_sqs

logger = logging.getLogger(__name__)


def _send_detections(detections, timestamp: float, position: str) -> int:
    """Send each detection to SQS and return the count of processed items."""
    processed = 0
    for det in detections:
        send_detection_to_sqs(
            rule_code="enterprise",
            member_id=det.person_id or "",
            detected_at=timestamp,
            detection_image_url=det.detection_result,
            confidence=det.confidence,
            object_attributes={
                "position": position,
                "helmet": None,
                "mask": None,
                "glove": None,
            },
        )
        processed += 1
    return processed


async def process_restricted_zone_alert(payload: RestrictedZoneAlertPayload) -> dict:
    """Send each detection to SQS. No DB rules, debounce, or time-window checks."""
    processed = _send_detections(payload.detections, payload.timestamp, position="restricted")
    logger.info(f"[ENTERPRISE ZONE] processed={processed} position='restricted'")
    return {"processed": processed}


async def process_employee_crossing_zone_alert(payload: EmployeeCrossingPayload) -> dict:
    """Send each crossing detection to SQS. No DB rules, debounce, or time-window checks."""
    processed = 0
    for det in payload.detections:
        send_detection_to_sqs(
            rule_code="enterprise",
            member_id=det.person_id or "",
            detected_at=payload.timestamp,
            detection_image_url=det.detection_result,
            confidence=det.confidence,
            object_attributes={
                "position": det.direction,
                "helmet": None,
                "mask": None,
                "glove": None,
            },
        )
        processed += 1
    logger.info(f"[ENTERPRISE ZONE] processed={processed} position='crossing'")
    return {"processed": processed}
