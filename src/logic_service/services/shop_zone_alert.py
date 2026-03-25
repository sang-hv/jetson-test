"""
Shop zone entry/exit: push detections to SQS with no rule or time-window checks.

Field mapping matches `send_detection_to_sqs` usage in `rule_engine` (member_id from
person_id, detection_image_url from detection_result, etc.). Camera id is taken from
`DEVICE_ID` via `send_detection_to_sqs` (callers do not pass camera_id).
"""

from __future__ import annotations

import logging
from typing import Literal

from schemas.shop_models import ShopPersonEventPayload
from services.sqs_sender import send_detection_to_sqs

logger = logging.getLogger(__name__)


async def process_shop_zone_sqs_event(
    payload: ShopPersonEventPayload,
    position: Literal["in", "out"],
) -> dict:
    """
    Send each detection to SQS. No DB rules, debounce, or time-window checks.
    """
    processed = 0
    

    for det in payload.detections:
        send_detection_to_sqs(
            rule_code="zone_entry" if position == "in" else "zone_exit",
            member_id=det.person_id or "",
            detected_at=payload.timestamp,
            detection_image_url=det.detection_result,
            confidence=det.confidence,
            object_attributes={
                "gender": det.gender,
                "age": det.age,
                "position": position,
            },
        )
        processed += 1

    logger.info(
        f"[SHOP ZONE] processed={processed} position={position}"
    )
    return {"processed": processed}
