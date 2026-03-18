"""
Main pipeline orchestrator for the face recognition system.

Coordinates the detector, tracker, recognizer, and database modules
to process video frames and display annotated results.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

from .database import FaceDatabase, FaceDatabaseSQLite
from .detector import PersonDetector, TrackedAnimal, TrackedPerson
from .mask_detector import MaskDetector
from .ppe_detector import ProtectiveEquipmentDetector
from .recognition_worker import RecognitionTask, RecognitionWorker
from .recognizer import FaceRecognizer
from .tracker import TrackManager
from .utils import (
    FPSCounter,
    compute_crop_score,
    crop_with_padding,
    draw_counting_info,
    draw_counting_line,
    draw_detection_zone,
    draw_in_zone_overlay,
    draw_info_overlay,
    draw_tracked_animal,
    draw_tracked_person,
)


def load_env_file(env_path: str = ".env") -> dict:
    """
    Load environment variables from .env file.

    Args:
        env_path: Path to .env file

    Returns:
        Dict of environment variables
    """
    env_vars = {}
    env_file = Path(env_path)

    if env_file.exists():
        with open(env_file, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip()

    return env_vars


@dataclass
class Config:
    """Pipeline configuration from CLI arguments."""

    source: str
    known_dir: str
    threshold: float = 0.45
    device: str = "cpu"
    min_confirm_frames: int = 3
    recognize_interval_ms: float = 500.0
    person_conf: float = 0.4
    cam_width: int = 1280
    cam_height: int = 720
    cam_fps: int = 30

    # Model configuration (adjusted based on device)
    yolo_model: str = "yolo11n.pt"
    insightface_model: str = "buffalo_l"
    det_size: Tuple[int, int] = field(default=(640, 640))

    # Mask detection settings (loaded from .env)
    mask_detection_enabled: bool = False
    mask_confidence_threshold: float = 0.5
    mask_model_path: str = "yolov8n-face-mask.pt"

    # Age/Gender detection settings (loaded from .env)
    age_gender_enabled: bool = False

    # PPE (Protective Equipment) detection settings (loaded from .env)
    ppe_detection_enabled: bool = False
    ppe_confidence_threshold: float = 0.5
    ppe_model_path: str = "yolov8m-protective-equipment-detection.pt"

    # People counting (line crossing) settings
    # Line + in_direction_point loaded from DB (detection_zones, code='entry_exit')
    counting_enabled: bool = False
    counting_line_start: Tuple[float, float] = (0.0, 0.5)
    counting_line_end: Tuple[float, float] = (1.0, 0.5)
    counting_in_direction_point: Tuple[float, float] = (0.5, 0.25)
    counting_cleanup_max_age: int = 150
    zmq_publish_port: int = 5555

    # Stranger alert settings (loaded from .env)
    stranger_alert_enabled: bool = False
    stranger_alert_interval: float = 10.0
    stranger_alert_grace_period: float = 0.0

    # Animal detection settings (loaded from .env)
    animal_detection_enabled: bool = False
    animal_alert_interval: float = 10.0
    animal_confidence_threshold: float = 0.4

    # Face database source (loaded from .env)
    face_db_source: str = "folder"  # "folder" or "sqlite"
    face_db_path: str = "logic_service/logic_service.db"

    # Tracker type (loaded from .env): "bytetrack", "botsort", "botsort_reid"
    tracker_type: str = "bytetrack"

    # Video source type (loaded from .env): "opencv" or "zmq"
    video_source_type: str = "opencv"
    zmq_video_endpoint: str = "ipc:///tmp/ai_frames.sock"
    zmq_recv_timeout_ms: int = 2000

    # Detection image saving directory
    detection_image_dir: str = "detection"

    # Detection zone: restrict YOLO inference to this rectangle (normalized coords)
    # Loaded from DB (detection_zones, code='detection')
    # None = use full frame
    detection_zone: Optional[Tuple[float, float, float, float]] = None  # (min_x, min_y, max_x, max_y)

    @classmethod
    def from_args(cls, args) -> Config:
        """
        Create Config from argparse namespace.

        Adjusts settings for CPU efficiency when needed.
        Also loads mask detection settings from .env file.
        """
        # Load .env file for mask detection settings
        env_vars = load_env_file(".env")

        # Parse mask detection settings from .env
        mask_enabled = env_vars.get("MASK_DETECTION_ENABLED", "false").lower() == "true"
        mask_threshold = float(env_vars.get("MASK_CONFIDENCE_THRESHOLD", "0.5"))
        mask_model = env_vars.get("MASK_MODEL_PATH", "yolov8n-face-mask.pt")

        # Parse age/gender detection settings from .env
        age_gender_enabled = env_vars.get("AGE_GENDER_ENABLED", "false").lower() == "true"

        # Parse PPE detection settings from .env
        ppe_enabled = env_vars.get("PPE_DETECTION_ENABLED", "false").lower() == "true"
        ppe_threshold = float(env_vars.get("PPE_CONFIDENCE_THRESHOLD", "0.5"))
        ppe_model = env_vars.get("PPE_MODEL_PATH", "yolov8m-protective-equipment-detection.pt")

        # Parse counting settings from .env
        counting_enabled = env_vars.get("COUNTING_ENABLED", "false").lower() == "true"
        counting_line_start: Tuple[float, float] = (0.0, 0.5)
        counting_line_end: Tuple[float, float] = (1.0, 0.5)
        counting_in_direction_point: Tuple[float, float] = (0.5, 0.25)
        counting_cleanup_max_age = int(env_vars.get("COUNTING_CLEANUP_MAX_AGE", "150"))
        zmq_publish_port = int(env_vars.get("ZMQ_PUBLISH_PORT", "5555"))

        # Load detection zones from DB
        import json as _json
        import sqlite3 as _sqlite3
        _db_path = env_vars.get("FACE_DB_PATH", "logic_service/logic_service.db")
        detection_zone: Optional[Tuple[float, float, float, float]] = None

        try:
            _conn = _sqlite3.connect(_db_path)

            # Load detection zone (restrict YOLO inference area)
            _det_row = _conn.execute(
                "SELECT coordinates FROM detection_zones WHERE code = 'detection' LIMIT 1"
            ).fetchone()
            if _det_row:
                _coords = _json.loads(_det_row[0])
                if len(_coords) >= 4:
                    _xs = [float(pt["x"]) for pt in _coords]
                    _ys = [float(pt["y"]) for pt in _coords]
                    detection_zone = (min(_xs), min(_ys), max(_xs), max(_ys))
                    print(f"[Config] Detection zone loaded: {detection_zone}")
                else:
                    print("Warning: detection zone needs 4 corner points")
            else:
                print("[Config] No detection zone found in DB, using full frame")

            # Load counting line (entry_exit zone)
            if counting_enabled:
                _row = _conn.execute(
                    "SELECT coordinates, in_direction_point FROM detection_zones WHERE code = 'entry_exit' LIMIT 1"
                ).fetchone()
                if _row:
                    _coords = _json.loads(_row[0])
                    _in_pt = _json.loads(_row[1]) if _row[1] else None
                    if len(_coords) >= 2 and _in_pt:
                        counting_line_start = (float(_coords[0]["x"]), float(_coords[0]["y"]))
                        counting_line_end = (float(_coords[1]["x"]), float(_coords[1]["y"]))
                        counting_in_direction_point = (float(_in_pt["x"]), float(_in_pt["y"]))
                    else:
                        print("Warning: entry_exit needs 2 line points and in_direction_point")
                else:
                    print("Warning: No entry_exit detection zone found in DB, using defaults")

            _conn.close()
        except Exception as e:
            print(f"Warning: Failed to load detection zones from DB: {e}")

        # Parse stranger alert settings from .env
        stranger_alert_enabled = env_vars.get("STRANGER_ALERT_ENABLED", "false").lower() == "true"
        stranger_alert_interval = float(env_vars.get("STRANGER_ALERT_INTERVAL", "10"))
        stranger_alert_grace_period = float(env_vars.get("STRANGER_ALERT_GRACE_PERIOD", "0"))

        # Parse animal detection settings from .env
        animal_detection_enabled = env_vars.get("ANIMAL_DETECTION_ENABLED", "false").lower() == "true"
        animal_alert_interval = float(env_vars.get("ANIMAL_ALERT_INTERVAL", "10"))
        animal_confidence_threshold = float(env_vars.get("ANIMAL_CONFIDENCE_THRESHOLD", "0.4"))

        # Parse face database source from .env
        face_db_source = env_vars.get("FACE_DB_SOURCE", "folder").lower()
        face_db_path = env_vars.get("FACE_DB_PATH", "logic_service/logic_service.db")

        # Parse tracker type from .env
        tracker_type = env_vars.get("TRACKER_TYPE", "bytetrack").lower()

        # Parse video source type from .env
        video_source_type = env_vars.get("VIDEO_SOURCE_TYPE", "opencv").lower()
        zmq_video_endpoint = env_vars.get("ZMQ_VIDEO_ENDPOINT", "ipc:///tmp/ai_frames.sock")
        zmq_recv_timeout_ms = int(env_vars.get("ZMQ_RECV_TIMEOUT_MS", "2000"))

        # Parse detection image directory from .env
        detection_image_dir = env_vars.get("DETECTION_IMAGE_DIR", "detection")

        config = cls(
            source=str(args.source),
            known_dir=args.known_dir,
            threshold=args.threshold,
            device=args.device,
            min_confirm_frames=args.min_confirm_frames,
            recognize_interval_ms=float(args.recognize_interval_ms),
            person_conf=float(env_vars.get("PERSON_CONFIDENCE_THRESHOLD", str(args.person_conf))),
            cam_width=args.cam_width,
            cam_height=args.cam_height,
            cam_fps=args.cam_fps,
            # Mask detection from .env
            mask_detection_enabled=mask_enabled,
            mask_confidence_threshold=mask_threshold,
            mask_model_path=mask_model,
            # Age/Gender detection from .env
            age_gender_enabled=age_gender_enabled,
            # PPE detection from .env
            ppe_detection_enabled=ppe_enabled,
            ppe_confidence_threshold=ppe_threshold,
            ppe_model_path=ppe_model,
            # Counting from .env
            counting_enabled=counting_enabled,
            counting_line_start=counting_line_start,
            counting_line_end=counting_line_end,
            counting_in_direction_point=counting_in_direction_point,
            counting_cleanup_max_age=counting_cleanup_max_age,
            zmq_publish_port=zmq_publish_port,
            # Stranger alert from .env
            stranger_alert_enabled=stranger_alert_enabled,
            stranger_alert_interval=stranger_alert_interval,
            stranger_alert_grace_period=stranger_alert_grace_period,
            # Animal detection from .env
            animal_detection_enabled=animal_detection_enabled,
            animal_alert_interval=animal_alert_interval,
            animal_confidence_threshold=animal_confidence_threshold,
            # Face database source from .env
            face_db_source=face_db_source,
            face_db_path=face_db_path,
            # Tracker type from .env
            tracker_type=tracker_type,
            # Video source from .env
            video_source_type=video_source_type,
            zmq_video_endpoint=zmq_video_endpoint,
            zmq_recv_timeout_ms=zmq_recv_timeout_ms,
            # Detection image saving
            detection_image_dir=detection_image_dir,
            # Detection zone from DB
            detection_zone=detection_zone,
        )

        # Optimize for CPU inference
        if config.device == "cpu":
            # Smaller detection size for faster face detection
            config.det_size = (320, 320)

            # On macOS, may need further optimization
            if sys.platform == "darwin":
                print("[Config] macOS detected, using optimized settings")

        return config

    def __post_init__(self):
        """Validate configuration."""
        if self.threshold < 0 or self.threshold > 1:
            raise ValueError(f"threshold must be 0-1, got {self.threshold}")
        if self.person_conf < 0 or self.person_conf > 1:
            raise ValueError(f"person_conf must be 0-1, got {self.person_conf}")
        if self.min_confirm_frames < 1:
            raise ValueError(f"min_confirm_frames must be >= 1")
        if self.recognize_interval_ms < 0:
            raise ValueError(f"recognize_interval_ms must be >= 0")


class Pipeline:
    """
    Main face recognition pipeline.

    Orchestrates video capture, person detection, face recognition,
    and visualization in a real-time loop.

    Keyboard controls:
        q - Quit
        r - Refresh face database (reload known faces)

    Example:
        config = Config(source="0", known_dir="./known_faces")
        pipeline = Pipeline(config)
        pipeline.run()
    """

    def __init__(self, config: Config):
        """
        Initialize pipeline with configuration.

        This loads all models and the face database, which may take
        several seconds on first run.
        """
        self.config = config

        print("=" * 60)
        print("Face Recognition System - Initializing")
        print("=" * 60)
        print(f"Device: {config.device}")
        if config.video_source_type == "zmq":
            print(f"Source: ZMQ ({config.zmq_video_endpoint})")
        else:
            print(f"Source: {config.source}")
        print(f"Known faces: {config.known_dir}")
        print(f"Threshold: {config.threshold}")
        print(f"Mask detection: {'Enabled' if config.mask_detection_enabled else 'Disabled'}")
        print(f"Age/Gender detection: {'Enabled' if config.age_gender_enabled else 'Disabled'}")
        print(f"PPE detection (helmet/glove): {'Enabled' if config.ppe_detection_enabled else 'Disabled'}")
        print(f"People counting: {'Enabled' if config.counting_enabled else 'Disabled'}")
        print(f"Animal detection: {'Enabled' if config.animal_detection_enabled else 'Disabled'}")
        print(f"Detection zone: {'Active' if config.detection_zone else 'Full frame'}")
        print("-" * 60)

        # Map tracker type to config file
        tracker_config_map = {
            "bytetrack": "bytetrack.yaml",
            "botsort": "botsort_no_reid.yaml",
            "botsort_reid": "botsort_reid.yaml",
        }
        tracker_config = tracker_config_map.get(config.tracker_type, "bytetrack.yaml")
        print(f"Tracker: {config.tracker_type} ({tracker_config})")

        # Initialize person detector (YOLO + tracker)
        self.detector = PersonDetector(
            model_name=config.yolo_model,
            device=config.device,
            person_conf=config.person_conf,
            tracker_config=tracker_config,
            animal_detection_enabled=config.animal_detection_enabled,
        )

        # Initialize track manager
        self.track_manager = TrackManager(
            recognize_interval_ms=config.recognize_interval_ms,
            min_confirm_frames=config.min_confirm_frames,
        )

        # Initialize face recognizer (InsightFace)
        self.recognizer = FaceRecognizer(
            model_name=config.insightface_model,
            device=config.device,
            det_size=config.det_size,
            age_gender_enabled=config.age_gender_enabled,
        )

        # Initialize mask detector if enabled
        self.mask_detector: Optional[MaskDetector] = None
        if config.mask_detection_enabled:
            print("-" * 60)
            self.mask_detector = MaskDetector(
                model_path=config.mask_model_path,
                device=config.device,
                conf_threshold=config.mask_confidence_threshold,
            )
            if not self.mask_detector.is_enabled:
                print("[Pipeline] WARNING: Mask detection requested but model not loaded")
                self.mask_detector = None

        # Initialize PPE detector if enabled (helmet/glove)
        self.ppe_detector: Optional[ProtectiveEquipmentDetector] = None
        if config.ppe_detection_enabled:
            print("-" * 60)
            self.ppe_detector = ProtectiveEquipmentDetector(
                model_path=config.ppe_model_path,
                device=config.device,
                conf_threshold=config.ppe_confidence_threshold,
            )
            if not self.ppe_detector.is_enabled:
                print("[Pipeline] WARNING: PPE detection requested but model not loaded")
                self.ppe_detector = None

        # Initialize ZMQ publisher if any feature needs it
        self.zmq_publisher = None
        needs_zmq = config.counting_enabled or config.animal_detection_enabled
        if needs_zmq:
            from .zmq_publisher import ZMQPublisher
            self.zmq_publisher = ZMQPublisher(port=config.zmq_publish_port)

        # Initialize line crossing counter if enabled
        self.counter = None
        if config.counting_enabled:
            from .counter import ZoneCounter

            self.counter = ZoneCounter(
                line_start=config.counting_line_start,
                line_end=config.counting_line_end,
                in_direction_point=config.counting_in_direction_point,
            )
            print(f"People counting: Enabled (line {config.counting_line_start} -> {config.counting_line_end})")

        # Initialize stranger alert manager if enabled (requires counting)
        self.stranger_alert_manager = None
        if config.stranger_alert_enabled and config.counting_enabled:
            from .counter import StrangerAlertManager

            self.stranger_alert_manager = StrangerAlertManager(
                alert_interval=config.stranger_alert_interval,
                grace_period=config.stranger_alert_grace_period,
            )
            print(f"Stranger alert: Enabled (interval={config.stranger_alert_interval}s, grace_period={config.stranger_alert_grace_period}s)")
        elif config.stranger_alert_enabled and not config.counting_enabled:
            print("[Pipeline] WARNING: Stranger alert requires COUNTING_ENABLED=true")

        # Initialize animal alert manager if enabled
        self.animal_alert_manager = None
        if config.animal_detection_enabled:
            from .animal_alert import AnimalAlertManager

            self.animal_alert_manager = AnimalAlertManager(
                alert_interval=config.animal_alert_interval,
            )
            print(f"Animal detection: Enabled (alert interval={config.animal_alert_interval}s)")

        # Initialize detection image saver
        from .detection_saver import DetectionImageSaver
        self.detection_saver = DetectionImageSaver(base_dir=config.detection_image_dir)

        self._frame_count = 0

        # Load known faces database
        print("-" * 60)
        if config.face_db_source == "sqlite":
            print(f"[Pipeline] Using SQLite face database: {config.face_db_path}")
            self.database = FaceDatabaseSQLite(config.face_db_path)
        else:
            self.database = FaceDatabase(config.known_dir, self.recognizer)
        self._load_known_faces()

        # Initialize recognition worker (background thread)
        self.recognition_worker = RecognitionWorker(
            recognizer=self.recognizer,
            track_manager=self.track_manager,
            threshold=config.threshold,
            max_queue_size=10,
            mask_detector=self.mask_detector,
            ppe_detector=self.ppe_detector,
        )

        # FPS counter
        self.fps_counter = FPSCounter()

        print("-" * 60)
        print("Initialization complete!")
        print("=" * 60)

    def _load_known_faces(self, force_refresh: bool = False) -> None:
        """Load or reload known faces database."""
        known_data = self.database.load(force_refresh=force_refresh)
        if known_data.count > 0:
            self.recognizer.set_known_faces(known_data.embeddings, known_data.labels)
        else:
            print("[Pipeline] No known faces loaded - all persons will be 'Unknown'")

    def _open_video_source(self):
        """
        Open video source based on config.video_source_type.

        Returns cv2.VideoCapture (opencv mode) or ZMQVideoSource (zmq mode).
        Both expose read(), isOpened(), release(), get().
        """
        if self.config.video_source_type == "zmq":
            from .zmq_video_source import ZMQVideoSource

            endpoint = self.config.zmq_video_endpoint
            timeout = self.config.zmq_recv_timeout_ms
            print(f"[Pipeline] Opening ZMQ video source: {endpoint} (timeout={timeout}ms)")
            return ZMQVideoSource(
                endpoint=endpoint,
                recv_timeout_ms=timeout,
            )

        source = self.config.source

        # Try to parse as camera index
        try:
            source_idx = int(source)
            print(f"[Pipeline] Opening camera {source_idx}...")
            cap = cv2.VideoCapture(source_idx)

            # Set camera properties
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.cam_width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.cam_height)
            cap.set(cv2.CAP_PROP_FPS, self.config.cam_fps)

            # Report actual values (may differ from requested)
            actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_fps = cap.get(cv2.CAP_PROP_FPS)
            print(f"[Pipeline] Camera: {actual_w}x{actual_h} @ {actual_fps:.1f}fps")

            return cap
        except ValueError:
            pass

        # Treat as file path or URL
        print(f"[Pipeline] Opening video source: {source}")
        cap = cv2.VideoCapture(source)
        return cap

    def _extract_person_crop(
        self,
        frame: np.ndarray,
        person: TrackedPerson,
        padding: float = 0.1,
    ) -> Optional[np.ndarray]:
        """
        Extract person region from frame with padding.

        Returns None if the crop would be too small for face detection.
        """
        crop = crop_with_padding(frame, person.bbox, padding=padding)

        # Skip if crop is too small for reliable face detection
        if crop.shape[0] < 50 or crop.shape[1] < 30:
            return None

        return crop

    def _process_frame(self, frame: np.ndarray) -> np.ndarray:
        """
        Process single frame through the full pipeline.

        Multi-threaded architecture:
        - Main thread: Detection, tracking, drawing (fast, non-blocking)
        - Worker thread: Face recognition (slow, runs in background)

        Steps:
        1. Detect and track persons with YOLO + ByteTrack
        2. Submit recognition tasks to worker queue (non-blocking)
        3. Draw visualization using cached/confirmed labels
        """
        self._frame_count += 1

        # 1. Detect and track persons (and animals if enabled)
        # If a detection zone is configured, crop frame to that region for YOLO
        detect_frame = frame
        zone_offset_x, zone_offset_y = 0, 0
        if self.config.detection_zone is not None:
            h, w = frame.shape[:2]
            min_x, min_y, max_x, max_y = self.config.detection_zone
            zx1 = int(min_x * w)
            zy1 = int(min_y * h)
            zx2 = int(max_x * w)
            zy2 = int(max_y * h)
            detect_frame = frame[zy1:zy2, zx1:zx2]
            zone_offset_x, zone_offset_y = zx1, zy1

        tracked_persons, tracked_animals = self.detector.detect_and_track(detect_frame)

        # Remap bboxes from crop space back to full-frame coordinates
        if zone_offset_x or zone_offset_y:
            for p in tracked_persons:
                p.bbox[0] += zone_offset_x
                p.bbox[1] += zone_offset_y
                p.bbox[2] += zone_offset_x
                p.bbox[3] += zone_offset_y
            for a in tracked_animals:
                a.bbox[0] += zone_offset_x
                a.bbox[1] += zone_offset_y
                a.bbox[2] += zone_offset_x
                a.bbox[3] += zone_offset_y

        # 2. Get active track IDs for cleanup
        active_ids = [p.track_id for p in tracked_persons]

        # 2b. Update zone counter and process lost tracks
        if self.counter is not None:
            track_infos = {}
            track_scores = {}
            for person in tracked_persons:
                tid = person.track_id
                age, gender = self.track_manager.get_age_gender(tid)
                track_infos[tid] = {
                    "person_id": self.track_manager.get_label(tid),
                    "age": age,
                    "gender": gender,
                }
                track_scores[tid] = compute_crop_score(person.bbox, frame.shape)
            self.counter.update(tracked_persons, frame, self._frame_count, track_infos, track_scores)
            crossings, passerby_events = self.counter.process_lost_tracks(
                active_ids, self._frame_count, self.config.counting_cleanup_max_age
            )
            if crossings and self.zmq_publisher is not None:
                self._publish_crossings(crossings)
            if passerby_events and self.zmq_publisher is not None:
                self._publish_passerby_events(passerby_events)

            # 2c. Check for stranger alerts in IN zone
            if self.stranger_alert_manager is not None:
                in_zone_tracks = self.counter.get_tracks_in_zone("in")
                stranger_in_zone = {}
                for tid in in_zone_tracks:
                    if tid not in track_infos:
                        continue  # Skip stale/inactive tracks
                    info = track_infos[tid]
                    pid = info.get("person_id", "Unknown")
                    if pid == "Unknown":
                        created_at = self.track_manager.get_track_created_at(tid)
                        if created_at is not None:
                            info["created_at"] = created_at
                        stranger_in_zone[tid] = info
                alerts = self.stranger_alert_manager.update(stranger_in_zone)
                if alerts and self.zmq_publisher is not None:
                    # Build bbox lookup for stranger alert saving
                    track_bboxes = {p.track_id: p.bbox for p in tracked_persons}
                    self._publish_stranger_alerts(alerts, frame, track_bboxes)

        # 3. Submit recognition tasks (non-blocking)
        for person in tracked_persons:
            track_id = person.track_id

            # Mark track as seen (for cleanup logic)
            self.track_manager.mark_track_seen(track_id)

            # Check if this track needs face recognition (rate limiting)
            if self.track_manager.should_recognize(track_id):
                # Extract person crop
                crop = self._extract_person_crop(frame, person)

                if crop is not None:
                    # Determine priority: 0 = high (new/unconfirmed), 1 = low (confirmed)
                    is_priority = self.track_manager.is_priority_track(track_id)

                    # Submit to worker queue (non-blocking)
                    task = RecognitionTask(
                        priority=0 if is_priority else 1,
                        track_id=track_id,
                        crop=crop.copy(),  # Copy for thread safety
                        timestamp=time.time(),
                    )
                    self.recognition_worker.submit(task)

                    # Mark that we submitted (prevents duplicate submissions)
                    self.track_manager.mark_recognition_submitted(track_id)

        # 4. Draw labels (uses cached/confirmed labels from worker)
        for person in tracked_persons:
            label = self.track_manager.get_label(person.track_id)
            mask_status = self.track_manager.get_mask_status(person.track_id)
            age, gender = self.track_manager.get_age_gender(person.track_id)
            helmet_status = self.track_manager.get_helmet_status(person.track_id)
            glove_status = self.track_manager.get_glove_status(person.track_id)
            frame = draw_tracked_person(
                frame, person, label,
                mask_status=mask_status,
                age=age,
                gender=gender,
                helmet_status=helmet_status,
                glove_status=glove_status,
            )

        # 4b. Process and draw animals
        if self.animal_alert_manager is not None and tracked_animals:
            alerts = self.animal_alert_manager.update(tracked_animals)
            if alerts and self.zmq_publisher is not None:
                self._publish_animal_alerts(alerts)
            for animal in tracked_animals:
                frame = draw_tracked_animal(frame, animal)

        # 5. Cleanup stale tracks
        self.track_manager.cleanup_stale_tracks(active_ids)

        # 6. Draw info overlay with queue size
        fps = self.fps_counter.update()
        queue_info = f"Q:{self.recognition_worker.queue_size}"
        frame = draw_info_overlay(frame, fps, len(tracked_persons), queue_info)

        # 7. Draw counting line, IN zone overlay, and info
        if self.counter is not None:
            pt1, pt2 = self.counter.get_line_points_px(frame.shape)
            in_pt = self.counter.get_in_direction_point_px(frame.shape)
            frame = draw_in_zone_overlay(frame, pt1, pt2, in_pt)
            frame = draw_counting_line(frame, pt1, pt2)
            in_count, out_count = self.counter.get_counts()
            frame = draw_counting_info(frame, in_count, out_count)

        # 8. Draw detection zone boundary
        if self.config.detection_zone is not None:
            frame = draw_detection_zone(frame, self.config.detection_zone)

        return frame

    def _publish_crossings(self, crossings) -> None:
        """Build ZMQ payload from crossing events and publish."""
        import time as _time
        detections = []
        for event in crossings:
            detection_result = None
            if event.frame is not None and event.bbox is not None:
                detection_result = self.detection_saver.save_frame_with_box(
                    event.frame, event.bbox, "crossing", event.track_id, event.person_id,
                )
            detections.append({
                "track_id": event.track_id,
                "person_id": event.person_id,
                "direction": event.direction,
                "age": event.age,
                "gender": event.gender,
                "detection_result": detection_result,
            })
        payload = {"timestamp": _time.time(), "detections": detections}
        self.zmq_publisher.send_detection(payload)

    def _publish_passerby_events(self, events) -> None:
        """Build ZMQ payload from passerby events and publish."""
        import time as _time
        detections = []
        for event in events:
            detection_result = None
            if event.frame is not None and event.bbox is not None:
                detection_result = self.detection_saver.save_frame_with_box(
                    event.frame, event.bbox, "passerby", event.track_id, event.person_id,
                )
            detections.append({
                "track_id": event.track_id,
                "person_id": event.person_id,
                "age": event.age,
                "gender": event.gender,
                "detection_result": detection_result,
            })
        payload = {"timestamp": _time.time(), "detections": detections}
        self.zmq_publisher.send_passerby_event(payload)

    def _publish_animal_alerts(self, alerts) -> None:
        """Build ZMQ payload from animal alert events and publish."""
        import time as _time
        detections = []
        for alert in alerts:
            detections.append({
                "track_id": alert.track_id,
                "class_id": alert.class_id,
                "class_name": alert.class_name,
                "confidence": alert.confidence,
                "alert_count": alert.alert_count,
            })
        payload = {"timestamp": _time.time(), "detections": detections}
        self.zmq_publisher.send_animal_alert(payload)

    def _publish_stranger_alerts(self, alerts, frame=None, track_bboxes=None) -> None:
        """Build ZMQ payload from stranger alert events and publish."""
        import time as _time
        detections = []
        for alert in alerts:
            detection_result = None
            bbox = track_bboxes.get(alert.track_id) if track_bboxes else None
            if frame is not None and bbox is not None:
                detection_result = self.detection_saver.save_frame_with_box(
                    frame, bbox, "stranger_alert", alert.track_id, alert.person_id,
                )
            detections.append({
                "track_id": alert.track_id,
                "person_id": alert.person_id,
                "age": alert.age,
                "gender": alert.gender,
                "alert_count": alert.alert_count,
                "detection_result": detection_result,
            })
        payload = {"timestamp": _time.time(), "detections": detections}
        self.zmq_publisher.send_stranger_alert(payload)

    def run(self) -> None:
        """
        Run the main pipeline loop.

        Opens video source, processes frames, and displays results
        until user quits or video ends.

        Multi-threaded: Recognition runs in background worker thread.
        """
        # Start recognition worker thread
        self.recognition_worker.start()

        cap = self._open_video_source()

        if not cap.isOpened():
            self.recognition_worker.stop()
            raise RuntimeError(f"Failed to open video source: {self.config.source}")

        print("\n[Pipeline] Running... Press 'q' to quit, 'r' to refresh database\n")

        frame_count = 0
        try:
            while True:
                ret, frame = cap.read()

                if not ret:
                    # ZMQ source: False = timeout/reconnecting, keep waiting
                    if self.config.video_source_type == "zmq":
                        continue
                    # Camera index: might be temporary, retry
                    if self.config.source.isdigit():
                        continue
                    # Video file: end of file
                    print("[Pipeline] End of video")
                    break

                frame_count += 1

                # Process frame (detection in main thread, recognition in worker)
                annotated_frame = self._process_frame(frame)

                # Display
                cv2.imshow("Face Recognition", annotated_frame)

                # Handle keyboard events
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    print("[Pipeline] Quit requested")
                    break
                elif key == ord("r"):
                    # Refresh face database
                    print("\n[Pipeline] Refreshing face database...")
                    self._load_known_faces(force_refresh=True)
                    print("[Pipeline] Database refreshed\n")

        except KeyboardInterrupt:
            print("\n[Pipeline] Interrupted by user")

        finally:
            # Stop worker thread gracefully
            self.recognition_worker.stop()
            cap.release()
            cv2.destroyAllWindows()
            if self.zmq_publisher is not None:
                self.zmq_publisher.close()
            print(f"[Pipeline] Stopped after {frame_count} frames")

    def process_single_image(self, image_path: str) -> np.ndarray:
        """
        Process a single image (for testing).

        Args:
            image_path: Path to image file

        Returns:
            Annotated image
        """
        frame = cv2.imread(image_path)
        if frame is None:
            raise ValueError(f"Could not read image: {image_path}")

        return self._process_frame(frame)

    def get_stats(self) -> dict:
        """Get current pipeline statistics."""
        return {
            "active_tracks": self.track_manager.active_track_count,
            "confirmed_tracks": self.track_manager.confirmed_track_count,
            "known_faces": self.recognizer.known_face_count,
            "unique_persons": self.recognizer.unique_person_count,
            "worker": self.recognition_worker.stats,
        }