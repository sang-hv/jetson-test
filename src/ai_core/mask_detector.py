"""
Face mask detection using YOLO model.

Detects whether a person is wearing a face mask in the given image crop.
Uses YOLOv8 model trained on face mask detection dataset.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import numpy as np

try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False


class MaskDetector:
    """
    YOLO-based face mask detector.

    Detects face masks in person crops and returns probability/classification.

    Example:
        detector = MaskDetector(model_path="yolov8n-face-mask.pt")
        is_masked, confidence = detector.detect(person_crop)
        if is_masked:
            print(f"Wearing mask: {confidence:.2f}")
    """

    # Common class name mappings for different mask detection models
    MASK_CLASS_NAMES = {"mask", "with_mask", "face_mask", "masked"}
    NO_MASK_CLASS_NAMES = {"no_mask", "without_mask", "no_face_mask", "unmasked"}

    def __init__(
        self,
        model_path: str = "yolov8n-face-mask.pt",
        device: str = "cpu",
        conf_threshold: float = 0.5,
    ):
        """
        Initialize mask detector.

        Args:
            model_path: Path to YOLO model file (.pt)
            device: 'cpu' or 'cuda'
            conf_threshold: Minimum confidence for detection
        """
        self.device = device
        self.conf_threshold = conf_threshold
        self.model: Optional[YOLO] = None
        self.is_enabled = False

        # Class indices (will be determined after model loads)
        self._mask_class_idx: Optional[int] = None
        self._no_mask_class_idx: Optional[int] = None

        self._load_model(model_path)

    def _load_model(self, model_path: str) -> None:
        """Load YOLO model for mask detection."""
        if not YOLO_AVAILABLE:
            print("[MaskDetector] WARNING: ultralytics not installed, mask detection disabled")
            return

        # Check if model file exists
        if not Path(model_path).exists():
            print(f"[MaskDetector] WARNING: Model not found at {model_path}")
            print("[MaskDetector] Mask detection disabled")
            return

        try:
            print(f"[MaskDetector] Loading YOLO model: {model_path}")
            self.model = YOLO(model_path)

            # Get class names and find mask/no_mask indices
            class_names = self.model.names  # Dict: {0: 'class0', 1: 'class1', ...}
            print(f"[MaskDetector] Model classes: {class_names}")

            for idx, name in class_names.items():
                name_lower = name.lower().replace(" ", "_").replace("-", "_")
                if name_lower in self.MASK_CLASS_NAMES:
                    self._mask_class_idx = idx
                    print(f"[MaskDetector] Mask class: '{name}' (idx={idx})")
                elif name_lower in self.NO_MASK_CLASS_NAMES:
                    self._no_mask_class_idx = idx
                    print(f"[MaskDetector] No-mask class: '{name}' (idx={idx})")

            if self._mask_class_idx is None and self._no_mask_class_idx is None:
                print("[MaskDetector] WARNING: Could not find mask/no_mask classes")
                print("[MaskDetector] Expected class names containing: mask, no_mask, with_mask, without_mask")
                return

            self.is_enabled = True
            print("[MaskDetector] Ready")

        except Exception as e:
            print(f"[MaskDetector] ERROR loading model: {e}")
            self.model = None

    def detect(self, image: np.ndarray) -> Tuple[Optional[bool], float]:
        """
        Detect if face mask is present in image.

        Args:
            image: BGR image (person or face crop)

        Returns:
            Tuple of (is_masked, confidence):
            - is_masked: True if wearing mask, False if not, None if uncertain
            - confidence: Detection confidence (0.0-1.0)
        """
        if not self.is_enabled or self.model is None:
            return None, 0.0

        if image is None or image.size == 0:
            return None, 0.0

        try:
            # Run inference
            results = self.model(image, verbose=False, device=self.device)

            if not results or len(results) == 0:
                return None, 0.0

            result = results[0]

            if result.boxes is None or len(result.boxes) == 0:
                return None, 0.0

            # Find the detection with highest confidence
            boxes = result.boxes
            best_mask_conf = 0.0
            best_no_mask_conf = 0.0

            for i in range(len(boxes)):
                cls_id = int(boxes.cls[i].item())
                conf = float(boxes.conf[i].item())

                if cls_id == self._mask_class_idx and conf > best_mask_conf:
                    best_mask_conf = conf
                elif cls_id == self._no_mask_class_idx and conf > best_no_mask_conf:
                    best_no_mask_conf = conf

            # Determine result based on confidence threshold
            if best_mask_conf >= self.conf_threshold and best_mask_conf > best_no_mask_conf:
                return True, best_mask_conf
            elif best_no_mask_conf >= self.conf_threshold and best_no_mask_conf > best_mask_conf:
                return False, best_no_mask_conf
            elif best_mask_conf > 0 or best_no_mask_conf > 0:
                # Below threshold but still detected something
                if best_mask_conf > best_no_mask_conf:
                    return True, best_mask_conf
                else:
                    return False, best_no_mask_conf

            return None, 0.0

        except Exception as e:
            print(f"[MaskDetector] Detection error: {e}")
            return None, 0.0

    def get_mask_probability(self, image: np.ndarray) -> float:
        """
        Get mask probability for image.

        Args:
            image: BGR image

        Returns:
            Probability of wearing mask (0.0-1.0)
            Returns 0.5 if detection is uncertain
        """
        is_masked, confidence = self.detect(image)

        if is_masked is None:
            return 0.5  # Uncertain

        if is_masked:
            return confidence  # High = likely masked
        else:
            return 1.0 - confidence  # Low = likely not masked
