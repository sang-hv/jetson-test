"""Utility for saving detection crop images to disk."""

import os
from datetime import datetime

import cv2
import numpy as np


class DetectionImageSaver:
    """Saves cropped person images when detection events fire."""

    def __init__(self, base_dir: str = "detection"):
        self._base_dir = os.path.abspath(base_dir)

    def save_crop(
        self,
        crop: np.ndarray,
        event_type: str,
        track_id: int,
        person_id: str = "Unknown",
    ) -> str:
        """Save a cropped image and return the absolute file path.

        Naming: {base_dir}/{event_type}/{YYYY-MM-DD}/{HHMMSS_ffffff}_{track_id}_{person_id}.webp
        """
        now = datetime.now()
        date_dir = now.strftime("%Y-%m-%d")
        safe_person_id = person_id.replace("/", "_").replace("\\", "_").replace("?", "")
        filename = f"{now.strftime('%H%M%S_%f')}_{track_id}_{safe_person_id}.webp"

        dir_path = os.path.join(self._base_dir, event_type, date_dir)
        os.makedirs(dir_path, exist_ok=True)

        file_path = os.path.join(dir_path, filename)
        cv2.imwrite(file_path, crop, [cv2.IMWRITE_WEBP_QUALITY, 80])
        return file_path
