"""
Base pipeline providing generic detection, tracking, recognition, and visualization.

Subclasses (HomePipeline, ShopPipeline) override hook methods to add
domain-specific logic (counting, alerts, etc.) without duplicating the core loop.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, List, Optional, Tuple

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
    crop_with_padding,
    draw_detection_zone,
    draw_info_overlay,
    draw_tracked_person,
)

if TYPE_CHECKING:
    from .pipeline import Config


class BasePipeline:
    """
    Base pipeline with generic detection, tracking, recognition, and drawing.

    Subclasses override hook methods to inject domain-specific behavior:
    - _init_extra_components(): initialize additional managers
    - _on_detections(): post-detection processing (counting, alerts)
    - _on_draw_animals(): animal drawing/alerting
    - _draw_extra_overlays(): additional visualizations

    Keyboard controls:
        q - Quit
        r - Refresh face database (reload known faces)

    Example:
        config = Config(source="0", known_dir="./known_faces")
        pipeline = HomePipeline(config)
        pipeline.run()
    """

    def __init__(self, config: Config):
        self.config = config

        self._print_banner(config)

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
            animal_conf=config.animal_confidence_threshold,
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

        # ZMQ publisher for broadcasting events to Logic Service
        from .zmq_publisher import ZMQPublisher
        self.zmq_publisher = ZMQPublisher(port=config.zmq_publish_port)
        self._prev_person_count: int = -1

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

        # Hook: let subclasses initialize extra components
        self._init_extra_components()

        print("-" * 60)
        print("Initialization complete!")
        print("=" * 60)

    def _print_banner(self, config: Config) -> None:
        """Print initialization banner."""
        print("=" * 60)
        print(f"Face Recognition System - Initializing ({config.pipeline_type})")
        print("=" * 60)
        print(f"Device: {config.device}")
        if config.video_source_type == "zmq":
            print(f"Source: ZMQ ({config.zmq_video_endpoint})")
        elif config.video_source_type == "shm":
            print(f"Source: SHM ({config.shm_video_name})")
        else:
            print(f"Source: {config.source}")
        print(f"Known faces: {config.known_dir}")
        print(f"Threshold: {config.threshold}")
        print(f"Mask detection: {'Enabled' if config.mask_detection_enabled else 'Disabled'}")
        print(f"Age/Gender detection: {'Enabled' if config.age_gender_enabled else 'Disabled'}")
        print(f"PPE detection (helmet/glove): {'Enabled' if config.ppe_detection_enabled else 'Disabled'}")
        print(f"Detection zone: {'Active' if config.detection_zone else 'Full frame'}")
        print(f"Display window: {'Enabled' if config.display_enabled else 'Disabled (headless)'}")
        print("-" * 60)

    # ------------------------------------------------------------------
    # Hook methods (override in subclasses)
    # ------------------------------------------------------------------

    def _init_extra_components(self) -> None:
        """Hook: initialize additional components (counter, alert managers, etc.)."""
        pass

    def _on_detections(
        self,
        tracked_persons: List[TrackedPerson],
        tracked_animals: List[TrackedAnimal],
        frame: np.ndarray,
    ) -> None:
        """Hook: post-detection processing (counting, stranger alerts, etc.)."""
        pass

    def _on_draw_animals(
        self,
        tracked_animals: List[TrackedAnimal],
        frame: np.ndarray,
    ) -> np.ndarray:
        """Hook: process and draw animals. Returns annotated frame."""
        return frame

    def _draw_extra_overlays(self, frame: np.ndarray) -> np.ndarray:
        """Hook: draw additional overlays (counting line, zone info, etc.)."""
        return frame

    # ------------------------------------------------------------------
    # Core methods
    # ------------------------------------------------------------------

    def _load_known_faces(self, force_refresh: bool = False) -> None:
        """Load or reload known faces database."""
        known_data = self.database.load(force_refresh=force_refresh)
        if known_data.count > 0:
            self.recognizer.set_known_faces(known_data.embeddings, known_data.labels)
        else:
            print("[Pipeline] No known faces loaded - all persons will be 'Unknown'")

    def _open_video_source(self):
        """Open video source based on config.video_source_type."""
        if self.config.video_source_type == "zmq":
            from .zmq_video_source import ZMQVideoSource

            endpoint = self.config.zmq_video_endpoint
            timeout = self.config.zmq_recv_timeout_ms
            print(f"[Pipeline] Opening ZMQ video source: {endpoint} (timeout={timeout}ms)")
            return ZMQVideoSource(
                endpoint=endpoint,
                recv_timeout_ms=timeout,
            )

        if self.config.video_source_type == "shm":
            from .shm_video_source import SharedMemoryVideoSource

            name = self.config.shm_video_name
            timeout = self.config.zmq_recv_timeout_ms
            print(f"[Pipeline] Opening SHM video source: {name} (timeout={timeout}ms)")
            return SharedMemoryVideoSource(
                shm_name=name,
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
        """Extract person region from frame with padding."""
        crop = crop_with_padding(frame, person.bbox, padding=padding)

        # Skip if crop is too small for reliable face detection
        if crop.shape[0] < 50 or crop.shape[1] < 30:
            return None

        return crop

    def _detect(
        self, frame: np.ndarray
    ) -> Tuple[List[TrackedPerson], List[TrackedAnimal]]:
        """Run detection with optional zone cropping and coordinate remapping."""
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

        return tracked_persons, tracked_animals

    def _submit_recognition_tasks(
        self, tracked_persons: List[TrackedPerson], frame: np.ndarray
    ) -> None:
        """Submit face recognition tasks to the background worker."""
        for person in tracked_persons:
            track_id = person.track_id

            # Mark track as seen (for cleanup logic)
            self.track_manager.mark_track_seen(track_id)

            # Check if this track needs face recognition (rate limiting)
            if self.track_manager.should_recognize(track_id):
                crop = self._extract_person_crop(frame, person)

                if crop is not None:
                    is_priority = self.track_manager.is_priority_track(track_id)

                    task = RecognitionTask(
                        priority=0 if is_priority else 1,
                        track_id=track_id,
                        crop=crop.copy(),
                        timestamp=time.time(),
                    )
                    self.recognition_worker.submit(task)
                    self.track_manager.mark_recognition_submitted(track_id)

    def _draw_person(self, frame: np.ndarray, person: TrackedPerson) -> np.ndarray:
        """Draw a single tracked person with all attributes."""
        label = self.track_manager.get_label(person.track_id)
        mask_status = self.track_manager.get_mask_status(person.track_id)
        age, gender = self.track_manager.get_age_gender(person.track_id)
        helmet_status = self.track_manager.get_helmet_status(person.track_id)
        glove_status = self.track_manager.get_glove_status(person.track_id)
        return draw_tracked_person(
            frame, person, label,
            mask_status=mask_status,
            age=age,
            gender=gender,
            helmet_status=helmet_status,
            glove_status=glove_status,
        )

    def _draw_info_and_publish_count(
        self, frame: np.ndarray, tracked_persons: List[TrackedPerson]
    ) -> np.ndarray:
        """Draw FPS/person count overlay and publish count changes via ZMQ."""
        fps = self.fps_counter.update()
        current_person_count = len(tracked_persons)
        queue_info = f"Q:{self.recognition_worker.queue_size}"
        frame = draw_info_overlay(frame, fps, current_person_count, queue_info)

        # Publish person count change via ZMQ
        if self.zmq_publisher is not None and current_person_count != self._prev_person_count:
            self.zmq_publisher.send_person_count({
                "timestamp": time.time(),
                "person_count": current_person_count,
            })
            self._prev_person_count = current_person_count

        return frame

    def _process_frame(self, frame: np.ndarray) -> np.ndarray:
        """Process single frame through the full pipeline (template method)."""
        self._frame_count += 1

        # 1. Detect and track persons (and animals if enabled)
        tracked_persons, tracked_animals = self._detect(frame)
        active_ids = [p.track_id for p in tracked_persons]

        # 2. Hook: post-detection processing (counting, alerts)
        self._on_detections(tracked_persons, tracked_animals, frame)

        # 3. Submit recognition tasks to worker queue (non-blocking)
        self._submit_recognition_tasks(tracked_persons, frame)

        # 4. Draw labels
        for person in tracked_persons:
            frame = self._draw_person(frame, person)

        # 4b. Hook: process and draw animals
        frame = self._on_draw_animals(tracked_animals, frame)

        # 5. Cleanup stale tracks
        self.track_manager.cleanup_stale_tracks(active_ids)

        # 6. FPS overlay + person count publish
        frame = self._draw_info_and_publish_count(frame, tracked_persons)

        # 7. Hook: extra overlays (counting line, zones)
        frame = self._draw_extra_overlays(frame)

        # 8. Draw detection zone boundary
        if self.config.detection_zone is not None:
            frame = draw_detection_zone(frame, self.config.detection_zone)

        return frame

    def run(self) -> None:
        """Run the main pipeline loop."""
        # Start recognition worker thread
        self.recognition_worker.start()

        cap = self._open_video_source()

        if not cap.isOpened():
            # SHM source may start before writer exists; read() handles auto-reconnect.
            if self.config.video_source_type == "shm":
                print("[Pipeline] SHM source not attached yet, waiting for writer...")
            else:
                self.recognition_worker.stop()
                raise RuntimeError(f"Failed to open video source: {self.config.source}")

        if self.config.display_enabled:
            print("\n[Pipeline] Running... Press 'q' to quit, 'r' to refresh database\n")
        else:
            print("\n[Pipeline] Running in headless mode (no OpenCV window)\n")

        frame_count = 0
        try:
            while True:
                ret, frame = cap.read()

                if not ret:
                    # ZMQ / SHM: False = timeout, keep waiting
                    if self.config.video_source_type in ("zmq", "shm"):
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

                if self.config.display_enabled:
                    # Display
                    cv2.imshow("Face Recognition", annotated_frame)

                    # Handle keyboard events
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord("q"):
                        print("[Pipeline] Quit requested")
                        break
                    elif key == ord("r"):
                        print("\n[Pipeline] Refreshing face database...")
                        self._load_known_faces(force_refresh=True)
                        print("[Pipeline] Database refreshed\n")

        except KeyboardInterrupt:
            print("\n[Pipeline] Interrupted by user")

        finally:
            self.recognition_worker.stop()
            cap.release()
            if self.config.display_enabled:
                cv2.destroyAllWindows()
            if self.zmq_publisher is not None:
                self.zmq_publisher.close()
            print(f"[Pipeline] Stopped after {frame_count} frames")

    def process_single_image(self, image_path: str) -> np.ndarray:
        """Process a single image (for testing)."""
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
