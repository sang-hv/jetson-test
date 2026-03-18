"""Utility for saving detection images to disk."""

import os
from datetime import datetime

import cv2
import numpy as np


class DetectionImageSaver:
    """Saves detection images (full frame with bounding box) when events fire."""

    def __init__(self, base_dir: str = "detection"):
        self._base_dir = os.path.abspath(base_dir)

    def _build_path(self, event_type: str, track_id: int, person_id: str) -> str:
        """Build output file path and ensure directory exists."""
        now = datetime.now()
        date_dir = now.strftime("%Y-%m-%d")
        safe_person_id = person_id.replace("/", "_").replace("\\", "_").replace("?", "")
        filename = f"{now.strftime('%H%M%S_%f')}_{track_id}_{safe_person_id}.webp"

        dir_path = os.path.join(self._base_dir, event_type, date_dir)
        os.makedirs(dir_path, exist_ok=True)

        return os.path.join(dir_path, filename)

    def save_frame_with_box(
        self,
        frame: np.ndarray,
        bbox: np.ndarray,
        event_type: str,
        track_id: int,
        person_id: str = "Unknown",
    ) -> str:
        """Save full frame with bounding box drawn on the tracked object.

        Returns the absolute file path.
        """
        annotated = frame.copy()
        x1, y1, x2, y2 = [int(v) for v in bbox]
        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
        label = f"{person_id} (ID:{track_id})"
        cv2.putText(
            annotated, label, (x1, y1 - 10),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2,
        )

        file_path = self._build_path(event_type, track_id, person_id)
        cv2.imwrite(file_path, annotated, [cv2.IMWRITE_WEBP_QUALITY, 80])
        return file_path
