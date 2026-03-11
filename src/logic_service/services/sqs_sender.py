"""
AWS SQS message sender for AI detection results.

Sends detection events to the configured SQS queue.
Handles connection errors gracefully — detection processing continues
even if SQS is unreachable.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQS client (lazy-initialised)
# ---------------------------------------------------------------------------
_sqs_client = None


def _get_sqs_client():
    """Lazy-initialise and return the SQS client."""
    global _sqs_client
    if _sqs_client is not None:
        return _sqs_client

    region = os.getenv("AWS_SQS_REGION", "ap-northeast-1")
    access_key = os.getenv("AWS_SQS_ACCESS_KEY_ID", "")
    secret_key = os.getenv("AWS_SQS_SECRET_ACCESS_KEY", "")

    if not access_key or not secret_key:
        logger.warning("AWS SQS credentials not configured — messages will not be sent")
        return None

    _sqs_client = boto3.client(
        "sqs",
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    logger.info(f"SQS client initialised (region={region})")
    return _sqs_client


def _get_queue_url() -> str:
    return os.getenv("AWS_SQS_QUEUE_URL", "")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def send_detection_to_sqs(
    rule_code: str,
    member_id: str,
    camera_id: str,
    detected_at: float,
    detection_image_url: str | None,
    confidence: float | None,
    object_attributes: dict[str, Any] | None = None,
) -> bool:
    """
    Send a single detection result to the SQS queue.

    Returns True if the message was sent successfully, False otherwise.
    Never raises — all errors are logged and swallowed so that the main
    detection pipeline is not interrupted.
    """
    client = _get_sqs_client()
    queue_url = _get_queue_url()

    if client is None:
        logger.debug("SQS client not available — skipping send")
        return False

    if not queue_url:
        logger.warning("AWS_SQS_QUEUE_URL not configured — skipping send")
        return False

    # Convert Unix timestamp to ISO-8601 string
    detected_at_iso = datetime.fromtimestamp(detected_at, tz=timezone.utc).isoformat()

    message_body = {
        "rule_code": rule_code,
        "member_id": member_id or "",
        "camera_id": camera_id or "",
        "detected_at": detected_at_iso,
        "detection_image_url": detection_image_url or "",
        "confidence": confidence,
        "object_attributes": object_attributes or {},
    }

    try:
        response = client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(message_body, ensure_ascii=False),
        )
        message_id = response.get("MessageId", "?")
        logger.info(
            f"[SQS] Sent rule_code={rule_code} camera_id={camera_id} "
            f"member_id={member_id} → MessageId={message_id}"
        )
        return True
    except (BotoCoreError, ClientError) as exc:
        logger.error(f"[SQS] Failed to send message: {exc}")
        return False
    except Exception as exc:
        logger.error(f"[SQS] Unexpected error: {exc}")
        return False
