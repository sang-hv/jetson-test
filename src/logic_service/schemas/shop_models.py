"""Pydantic models for crossing event payloads exchanged over ZMQ."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ShopPersonEventDetection(BaseModel):
    """A single person who has crossed the counting line."""

    track_id: int = Field(..., description="ByteTrack persistent ID")
    person_id: str = Field(..., description="Recognised name, 'Unknown', or 'Name?' if uncertain")
    direction: Literal["in", "out"] = Field(..., description="Direction of crossing")
    age: Optional[int] = Field(None, description="Confirmed age, NULL if uncertain")
    gender: Optional[str] = Field(None, description='"M", "F", or NULL if uncertain')
    confidence: Optional[float] = Field(None, description="Recognition confidence 0.0-1.0")
    detection_result: Optional[str] = Field(None, description="Recognition result")


class ShopPersonEventPayload(BaseModel):
    """
    Top-level payload published by AI Service over ZMQ and accepted by the
    POST /api/test/mock-event endpoint.
    """

    timestamp: float = Field(..., description="Unix timestamp of the crossing event (seconds)")
    detections: List[ShopPersonEventDetection] = Field(..., description="One entry per person who crossed")
