"""Pydantic models for hospital fall detection events exchanged over ZMQ."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class FallDetection(BaseModel):
    """A single confirmed fall event for one tracked person."""

    track_id: int = Field(..., description="ByteTrack persistent ID")
    bbox: List[int] = Field(
        ..., description="Bounding box [x1, y1, x2, y2] in full-frame coordinates"
    )
    confidence: Optional[float] = Field(
        None, description="Fall confidence 0.0-1.0 (fraction of geometric flags triggered)"
    )
    detection_result: Optional[str] = Field(
        None, description="Path to saved detection image"
    )


class FallDetectedPayload(BaseModel):
    """Payload published by AI Service over ZMQ topic 'fall_detected'."""

    timestamp: float = Field(..., description="Unix timestamp of the event (seconds)")
    detections: List[FallDetection] = Field(
        ..., description="One entry per confirmed fall"
    )
