"""
YOLO-based person detection with integrated ByteTrack tracking.

Uses Ultralytics YOLO v11 with built-in ByteTrack for real-time
person detection and tracking.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

import numpy as np
from ultralytics import YOLO


@dataclass
class TrackedPerson:
    """Represents a tracked person detection."""

    track_id: int
    bbox: np.ndarray  # [x1, y1, x2, y2] format
    confidence: float

    @property
    def width(self) -> float:
        """Bounding box width."""
        return float(self.bbox[2] - self.bbox[0])

    @property
    def height(self) -> float:
        """Bounding box height."""
        return float(self.bbox[3] - self.bbox[1])

    @property
    def area(self) -> float:
        """Bounding box area in pixels."""
        return self.width * self.height

    @property
    def center(self) -> tuple:
        """Center point (x, y) of bounding box."""
        cx = (self.bbox[0] + self.bbox[2]) / 2
        cy = (self.bbox[1] + self.bbox[3]) / 2
        return (cx, cy)


class PersonDetector:
    """
    YOLO-based person detector with integrated ByteTrack tracking.

    Uses Ultralytics YOLO v11 for detection and the built-in ByteTrack
    tracker for persistent track IDs across frames.

    Example:
        detector = PersonDetector(device="cpu")
        persons = detector.detect_and_track(frame)
        for person in persons:
            print(f"Track {person.track_id}: {person.bbox}")
    """

    PERSON_CLASS_ID = 0  # COCO class index for "person"

    def __init__(
        self,
        model_name: str = "yolo11n.pt",
        device: str = "cpu",
        person_conf: float = 0.4,
        tracker_config: str = "bytetrack.yaml",
    ):
        """
        Initialize the person detector.

        Args:
            model_name: YOLO model name. Options:
                - "yolo11n.pt" (nano, fastest, ~6MB)
                - "yolo11s.pt" (small, good balance)
                - "yolo11m.pt" (medium, more accurate)
                - "yolov8n.pt", "yolov8s.pt" (older but stable)
            device: Device for inference ('cpu' or 'cuda')
            person_conf: Minimum confidence threshold for person detection (0.0-1.0)
            tracker_config: ByteTrack configuration file (built into Ultralytics)
        """
        self.device = device
        self.person_conf = person_conf
        self.tracker_config = tracker_config

        print(f"[Detector] Loading YOLO model: {model_name} on {device}")
        self.model = YOLO(model_name)

        # Warm up the model with a dummy inference
        self._warmup()

    def _warmup(self) -> None:
        """Warm up the model with a dummy image."""
        dummy = np.zeros((640, 640, 3), dtype=np.uint8)
        try:
            self.model.predict(
                dummy,
                device=self.device,
                verbose=False,
            )
            print("[Detector] Model warmup complete")
        except Exception as e:
            print(f"[Detector] Warmup warning: {e}")

    def detect_and_track(
        self,
        frame: np.ndarray,
        persist: bool = True,
    ) -> List[TrackedPerson]:
        """
        Detect persons in frame and track them across frames.

        Uses YOLO for detection and ByteTrack for tracking. The `persist=True`
        flag maintains tracking state across frames for consistent track IDs.

        Args:
            frame: BGR image as numpy array (from cv2.VideoCapture)
            persist: Whether to maintain track state between frames.
                    Set to True for continuous video, False to reset tracking.

        Returns:
            List of TrackedPerson objects with track_id, bbox, and confidence
        """
        # Run YOLO detection with ByteTrack tracking
        results = self.model.track(
            frame,
            persist=persist,
            tracker=self.tracker_config,
            conf=self.person_conf,
            classes=[self.PERSON_CLASS_ID],  # Only detect persons
            device=self.device,
            verbose=False,
        )

        tracked_persons: List[TrackedPerson] = []

        # Check if we have valid detections with track IDs
        if results and len(results) > 0:
            boxes = results[0].boxes

            # boxes.id is None if no tracks are assigned yet
            if boxes is not None and boxes.id is not None:
                for i in range(len(boxes)):
                    track_id = int(boxes.id[i].item())
                    bbox = boxes.xyxy[i].cpu().numpy()  # [x1, y1, x2, y2]
                    conf = float(boxes.conf[i].item())

                    tracked_persons.append(
                        TrackedPerson(
                            track_id=track_id,
                            bbox=bbox,
                            confidence=conf,
                        )
                    )

        return tracked_persons

    def detect_only(
        self,
        frame: np.ndarray,
    ) -> List[TrackedPerson]:
        """
        Detect persons without tracking (no persistent track IDs).

        Useful for single-image inference or when tracking is not needed.
        Track IDs will be sequential within each frame starting from 0.

        Args:
            frame: BGR image as numpy array

        Returns:
            List of TrackedPerson objects (track_id is just detection index)
        """
        results = self.model.predict(
            frame,
            conf=self.person_conf,
            classes=[self.PERSON_CLASS_ID],
            device=self.device,
            verbose=False,
        )

        tracked_persons: List[TrackedPerson] = []

        if results and len(results) > 0:
            boxes = results[0].boxes

            if boxes is not None and len(boxes) > 0:
                for i in range(len(boxes)):
                    bbox = boxes.xyxy[i].cpu().numpy()
                    conf = float(boxes.conf[i].item())

                    tracked_persons.append(
                        TrackedPerson(
                            track_id=i,  # Just use detection index
                            bbox=bbox,
                            confidence=conf,
                        )
                    )

        return tracked_persons

    def reset_tracker(self) -> None:
        """
        Reset the tracker state.

        Call this when switching to a new video source or when you want
        to clear all existing tracks.
        """
        # Reset by calling track with persist=False
        # This clears the tracker's internal state
        dummy = np.zeros((64, 64, 3), dtype=np.uint8)
        try:
            self.model.track(
                dummy,
                persist=False,
                tracker=self.tracker_config,
                device=self.device,
                verbose=False,
            )
        except Exception:
            pass  # Ignore errors during reset

    @property
    def model_info(self) -> dict:
        """Get information about the loaded model."""
        return {
            "model": self.model.model_name if hasattr(self.model, "model_name") else "unknown",
            "device": self.device,
            "person_conf": self.person_conf,
            "tracker": self.tracker_config,
        }
