"""Pydantic models for enterprise employee crossing events exchanged over ZMQ."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class EmployeeCrossingDetection(BaseModel):
    """A single recognized employee who has crossed the entry/exit line."""

    track_id: int = Field(..., description="ByteTrack persistent ID")
    person_id: str = Field(..., description="Recognised employee name")
    direction: Literal["in", "out"] = Field(
        ..., description='"in" = checkin, "out" = checkout'
    )
    confidence: Optional[float] = Field(None, description="Recognition confidence 0.0-1.0")
    detection_result: Optional[str] = Field(None, description="Path to saved detection image")


class EmployeeCrossingPayload(BaseModel):
    """
    Top-level payload published by AI Service over ZMQ topic 'employee_crossing'.
    Contains only recognized employees — Unknown persons are never included.
    """

    timestamp: float = Field(..., description="Unix timestamp of the event (seconds)")
    detections: List[EmployeeCrossingDetection] = Field(
        ..., description="One entry per employee crossing"
    )
