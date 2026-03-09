"""
Protective equipment detection using YOLO model.

Detects helmet and glove status in person crops using YOLOv8 model
trained on protective equipment detection dataset.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False


@dataclass
class PPEDetectionResult:
    """Result from PPE detection."""
    helmet_status: Optional[bool]  # True=wearing, False=not wearing, None=uncertain
    helmet_confidence: float
    glove_status: Optional[bool]
    glove_confidence: float


class ProtectiveEquipmentDetector:
    """
    YOLO-based protective equipment detector.

    Detects helmet and glove status in person crops using a single model inference.

    Example:
        detector = ProtectiveEquipmentDetector(
            model_path="yolov8m-protective-equipment-detection.pt"
        )
        result = detector.detect(person_crop)
        if result.helmet_status:
            print(f"Wearing helmet: {result.helmet_confidence:.2f}")
    """

    # Class name mappings for protective equipment detection model
    HELMET_CLASS_NAMES = {"helmet", "hard_hat", "safety_helmet"}
    NO_HELMET_CLASS_NAMES = {"no_helmet", "no_hard_hat"}
    GLOVE_CLASS_NAMES = {"glove", "gloves", "safety_glove"}
    NO_GLOVE_CLASS_NAMES = {"no_glove", "no_gloves"}

    def __init__(
        self,
        model_path: str = "yolov8m-protective-equipment-detection.pt",
        device: str = "cpu",
        conf_threshold: float = 0.5,
    ):
        """
        Initialize PPE detector.

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
        self._helmet_class_idx: Optional[int] = None
        self._no_helmet_class_idx: Optional[int] = None
        self._glove_class_idx: Optional[int] = None
        self._no_glove_class_idx: Optional[int] = None

        self._load_model(model_path)

    def _load_model(self, model_path: str) -> None:
        """Load YOLO model for PPE detection."""
        if not YOLO_AVAILABLE:
            print("[PPEDetector] WARNING: ultralytics not installed, PPE detection disabled")
            return

        # Check if model file exists
        if not Path(model_path).exists():
            print(f"[PPEDetector] WARNING: Model not found at {model_path}")
            print("[PPEDetector] PPE detection disabled")
            return

        try:
            print(f"[PPEDetector] Loading YOLO model: {model_path}")
            self.model = YOLO(model_path)

            # Get class names and find helmet/glove indices
            class_names = self.model.names  # Dict: {0: 'class0', 1: 'class1', ...}
            print(f"[PPEDetector] Model classes: {class_names}")

            for idx, name in class_names.items():
                name_lower = name.lower().replace(" ", "_").replace("-", "_")

                # Check helmet classes
                if name_lower in self.HELMET_CLASS_NAMES:
                    self._helmet_class_idx = idx
                    print(f"[PPEDetector] Helmet class: '{name}' (idx={idx})")
                elif name_lower in self.NO_HELMET_CLASS_NAMES:
                    self._no_helmet_class_idx = idx
                    print(f"[PPEDetector] No-helmet class: '{name}' (idx={idx})")

                # Check glove classes
                if name_lower in self.GLOVE_CLASS_NAMES:
                    self._glove_class_idx = idx
                    print(f"[PPEDetector] Glove class: '{name}' (idx={idx})")
                elif name_lower in self.NO_GLOVE_CLASS_NAMES:
                    self._no_glove_class_idx = idx
                    print(f"[PPEDetector] No-glove class: '{name}' (idx={idx})")

            # Check if at least helmet or glove detection is available
            has_helmet = self._helmet_class_idx is not None or self._no_helmet_class_idx is not None
            has_glove = self._glove_class_idx is not None or self._no_glove_class_idx is not None

            if not has_helmet and not has_glove:
                print("[PPEDetector] WARNING: Could not find helmet/glove classes")
                print("[PPEDetector] Expected: helmet, no_helmet, glove, no_glove")
                return

            self.is_enabled = True
            print(f"[PPEDetector] Ready (helmet={has_helmet}, glove={has_glove})")

        except Exception as e:
            print(f"[PPEDetector] ERROR loading model: {e}")
            self.model = None

    def detect(self, image: np.ndarray) -> PPEDetectionResult:
        """
        Detect helmet and glove status in image.

        Args:
            image: BGR image (person crop)

        Returns:
            PPEDetectionResult with helmet and glove status
        """
        if not self.is_enabled or self.model is None:
            return PPEDetectionResult(
                helmet_status=None, helmet_confidence=0.0,
                glove_status=None, glove_confidence=0.0,
            )

        if image is None or image.size == 0:
            return PPEDetectionResult(
                helmet_status=None, helmet_confidence=0.0,
                glove_status=None, glove_confidence=0.0,
            )

        try:
            # Run inference
            results = self.model(image, verbose=False, device=self.device)

            if not results or len(results) == 0:
                return PPEDetectionResult(
                    helmet_status=None, helmet_confidence=0.0,
                    glove_status=None, glove_confidence=0.0,
                )

            result = results[0]

            if result.boxes is None or len(result.boxes) == 0:
                return PPEDetectionResult(
                    helmet_status=None, helmet_confidence=0.0,
                    glove_status=None, glove_confidence=0.0,
                )

            # Find best confidence for each class
            best_helmet_conf = 0.0
            best_no_helmet_conf = 0.0
            best_glove_conf = 0.0
            best_no_glove_conf = 0.0

            boxes = result.boxes
            for i in range(len(boxes)):
                cls_id = int(boxes.cls[i].item())
                conf = float(boxes.conf[i].item())

                # Helmet detection
                if cls_id == self._helmet_class_idx and conf > best_helmet_conf:
                    best_helmet_conf = conf
                elif cls_id == self._no_helmet_class_idx and conf > best_no_helmet_conf:
                    best_no_helmet_conf = conf

                # Glove detection
                if cls_id == self._glove_class_idx and conf > best_glove_conf:
                    best_glove_conf = conf
                elif cls_id == self._no_glove_class_idx and conf > best_no_glove_conf:
                    best_no_glove_conf = conf

            # Determine helmet status
            helmet_status, helmet_conf = self._determine_status(
                best_helmet_conf, best_no_helmet_conf
            )

            # Determine glove status
            glove_status, glove_conf = self._determine_status(
                best_glove_conf, best_no_glove_conf
            )

            return PPEDetectionResult(
                helmet_status=helmet_status,
                helmet_confidence=helmet_conf,
                glove_status=glove_status,
                glove_confidence=glove_conf,
            )

        except Exception as e:
            print(f"[PPEDetector] Detection error: {e}")
            return PPEDetectionResult(
                helmet_status=None, helmet_confidence=0.0,
                glove_status=None, glove_confidence=0.0,
            )

    def _determine_status(
        self,
        positive_conf: float,
        negative_conf: float,
    ) -> Tuple[Optional[bool], float]:
        """
        Determine status based on positive/negative confidence scores.

        Args:
            positive_conf: Confidence for positive class (e.g., helmet)
            negative_conf: Confidence for negative class (e.g., no_helmet)

        Returns:
            Tuple of (status, confidence)
        """
        if positive_conf >= self.conf_threshold and positive_conf > negative_conf:
            return True, positive_conf
        elif negative_conf >= self.conf_threshold and negative_conf > positive_conf:
            return False, negative_conf
        elif positive_conf > 0 or negative_conf > 0:
            # Below threshold but detected something
            if positive_conf > negative_conf:
                return True, positive_conf
            else:
                return False, negative_conf
        return None, 0.0

    def get_helmet_probability(self, image: np.ndarray) -> float:
        """
        Get helmet probability for temporal smoothing.

        Args:
            image: BGR image

        Returns:
            Probability of wearing helmet (0.0-1.0)
            Returns 0.5 if uncertain
        """
        result = self.detect(image)

        if result.helmet_status is None:
            return 0.5  # Uncertain

        if result.helmet_status:
            return result.helmet_confidence  # High = wearing helmet
        else:
            return 1.0 - result.helmet_confidence  # Low = not wearing helmet

    def get_glove_probability(self, image: np.ndarray) -> float:
        """
        Get glove probability for temporal smoothing.

        Args:
            image: BGR image

        Returns:
            Probability of wearing glove (0.0-1.0)
            Returns 0.5 if uncertain
        """
        result = self.detect(image)

        if result.glove_status is None:
            return 0.5  # Uncertain

        if result.glove_status:
            return result.glove_confidence  # High = wearing glove
        else:
            return 1.0 - result.glove_confidence  # Low = not wearing glove

    def get_probabilities(self, image: np.ndarray) -> Tuple[float, float]:
        """
        Get both helmet and glove probabilities in single inference.

        Args:
            image: BGR image

        Returns:
            Tuple of (helmet_probability, glove_probability)
            Both in range 0.0-1.0, 0.5 means uncertain
        """
        result = self.detect(image)

        # Calculate helmet probability
        if result.helmet_status is None:
            helmet_prob = 0.5
        elif result.helmet_status:
            helmet_prob = result.helmet_confidence
        else:
            helmet_prob = 1.0 - result.helmet_confidence

        # Calculate glove probability
        if result.glove_status is None:
            glove_prob = 0.5
        elif result.glove_status:
            glove_prob = result.glove_confidence
        else:
            glove_prob = 1.0 - result.glove_confidence

        return helmet_prob, glove_prob
