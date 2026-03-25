"""Pydantic models for crossing event payloads exchanged over ZMQ."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class CrossingDetection(BaseModel):
    """A single person who has crossed the counting line."""

    track_id: int = Field(..., description="ByteTrack persistent ID")
    person_id: str = Field(..., description="Recognised name, 'Unknown', or 'Name?' if uncertain")
    direction: Literal["in", "out"] = Field(..., description="Direction of crossing")
    age: Optional[int] = Field(None, description="Confirmed age, NULL if uncertain")
    gender: Optional[str] = Field(None, description='"M", "F", or NULL if uncertain')
    confidence: Optional[float] = Field(None, description="Recognition confidence 0.0-1.0")
    detection_result: Optional[str] = Field(None, description="Recognition result")


class CrossingEventPayload(BaseModel):
    """
    Top-level payload published by AI Service over ZMQ and accepted by the
    POST /api/test/mock-event endpoint.
    """

    timestamp: float = Field(..., description="Unix timestamp of the crossing event (seconds)")
    detections: List[CrossingDetection] = Field(..., description="One entry per person who crossed")


class StrangerAlertDetection(BaseModel):
    """A single stranger alert for an Unknown person in the IN zone."""

    track_id: int = Field(..., description="ByteTrack persistent ID")
    person_id: str = Field("Unknown", description="Always 'Unknown' or 'Name?' (uncertain)")
    age: Optional[int] = Field(None, description="Confirmed age, NULL if uncertain")
    gender: Optional[str] = Field(None, description='"M", "F", or NULL if uncertain')
    alert_count: int = Field(1, description="Number of alerts sent for this track so far")
    confidence: Optional[float] = Field(None, description="Recognition confidence 0.0-1.0")
    detection_result: Optional[str] = Field(None, description="Recognition result")


class StrangerAlertPayload(BaseModel):
    """Payload for stranger alert events published over ZMQ."""

    timestamp: float = Field(..., description="Unix timestamp (seconds)")
    detections: List[StrangerAlertDetection] = Field(..., description="One entry per stranger alert")


class PasserbyDetection(BaseModel):
    """A stranger who appeared and disappeared in the OUT zone (passerby)."""

    track_id: int = Field(..., description="ByteTrack persistent ID")
    person_id: str = Field("Unknown", description="Always 'Unknown' or 'Name?' (uncertain)")
    age: Optional[int] = Field(None, description="Confirmed age, NULL if uncertain")
    gender: Optional[str] = Field(None, description='"M", "F", or NULL if uncertain')
    confidence: Optional[float] = Field(None, description="Recognition confidence 0.0-1.0")
    detection_result: Optional[str] = Field(None, description="Recognition result")
    

class PasserbyEventPayload(BaseModel):
    """Payload for passerby events published over ZMQ."""

    timestamp: float = Field(..., description="Unix timestamp (seconds)")
    detections: List[PasserbyDetection] = Field(..., description="One entry per passerby")

class AnimalAlertDetection(BaseModel):
    """A single animal detection alert."""

    track_id: int = Field(..., description="ByteTrack persistent ID")
    class_id: int = Field(..., description="COCO class ID (14-23)")
    class_name: str = Field(..., description="Animal class name (e.g., 'dog', 'cat')")
    confidence: float = Field(..., description="Detection confidence 0.0-1.0")
    alert_count: int = Field(1, description="Number of alerts sent for this track so far")
    detection_result: Optional[str] = Field(None, description="Recognition result")

class AnimalAlertPayload(BaseModel):
    """Payload for animal alert events published over ZMQ."""

    timestamp: float = Field(..., description="Unix timestamp (seconds)")
    detections: List[AnimalAlertDetection] = Field(..., description="One entry per animal alert")
