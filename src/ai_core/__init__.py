# Face Recognition System - Source Package
"""
This package contains the face recognition system modules:
- detector: YOLO-based person detection with ByteTrack tracking
- tracker: Track state management with temporal smoothing
- recognizer: InsightFace face detection and embedding matching
- recognition_worker: Background thread for face recognition
- database: Known faces loading with NPZ embedding cache
- pipeline: Main orchestrator loop
- mask_detector: YOLO-based face mask detection
- ppe_detector: YOLO-based helmet and glove detection
- utils: Drawing utilities and helpers
"""

from .detector import PersonDetector, TrackedPerson
from .tracker import TrackManager, TrackState
from .recognizer import FaceRecognizer, FaceMatch
from .recognition_worker import RecognitionWorker, RecognitionTask
from .database import FaceDatabase, KnownFacesData
from .mask_detector import MaskDetector
from .ppe_detector import ProtectiveEquipmentDetector, PPEDetectionResult
from .pipeline import Pipeline, Config

__all__ = [
    "PersonDetector",
    "TrackedPerson",
    "TrackManager",
    "TrackState",
    "FaceRecognizer",
    "FaceMatch",
    "RecognitionWorker",
    "RecognitionTask",
    "FaceDatabase",
    "KnownFacesData",
    "MaskDetector",
    "ProtectiveEquipmentDetector",
    "PPEDetectionResult",
    "Pipeline",
    "Config",
]
