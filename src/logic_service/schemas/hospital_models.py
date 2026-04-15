"""Pydantic models for hospital fall detection events exchanged over ZMQ."""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class FallDetection(BaseModel):
    """A single confirmed fall event for one tracked person."""

    track_id: int = Field(..., description="ByteTrack persistent ID")
    bbox: List[int] = Field(
        ..., description="Bounding box [x1, y1, x2, y2] in full-frame coordinates"
    )


class FallDetectedPayload(BaseModel):
    """Payload published by AI Service over ZMQ topic 'fall_detected'."""

    timestamp: float = Field(..., description="Unix timestamp of the event (seconds)")
    detections: List[FallDetection] = Field(
        ..., description="One entry per confirmed fall"
    )
