"""
Pipeline configuration and factory.

Contains the Config dataclass and create_pipeline() factory function.
Pipeline implementations live in base_pipeline.py, home_pipeline.py, and shop_pipeline.py.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple


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
    yolo_model: str = "yolo11l.engine"
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

    # PPE violation alert settings (enterprise pipeline only, loaded from .env)
    ppe_violation_alert_enabled: bool = False
    ppe_violation_alert_mask: bool = True
    ppe_violation_alert_helmet: bool = True
    ppe_violation_alert_glove: bool = True

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

    # Video source type (loaded from .env): "opencv", "zmq", or "shm"
    video_source_type: str = "opencv"
    zmq_video_endpoint: str = "ipc:///tmp/ai_frames.sock"
    zmq_recv_timeout_ms: int = 2000
    # Shared-memory mmap file (raw BGR from start-stream.py; must match STREAM_SHM_PATH)
    shm_video_name: str = "/dev/shm/mini_pc_ai_frames.bin"

    # Detection image saving directory
    detection_image_dir: str = "detection"
    # Show OpenCV GUI window (set false for SSH/headless runtime)
    display_enabled: bool = True

    # Detection zone: restrict YOLO inference to this rectangle (normalized coords)
    # Loaded from DB (detection_zones, code='detection')
    # None = use full frame
    detection_zone: Optional[Tuple[float, float, float, float]] = None  # (min_x, min_y, max_x, max_y)

    # Restricted zone: persons entering this area trigger an alert (normalized coords)
    # Loaded from DB (detection_zones, code='restricted')
    restricted_zone: Optional[Tuple[float, float, float, float]] = None  # (min_x, min_y, max_x, max_y)

    # Pipeline type: "home" or "shop"
    pipeline_type: str = "home"

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

        # Parse PPE violation alert settings from .env (enterprise only)
        ppe_violation_alert_enabled = env_vars.get("PPE_VIOLATION_ALERT_ENABLED", "false").lower() == "true"
        ppe_violation_alert_mask = env_vars.get("PPE_VIOLATION_ALERT_MASK", "true").lower() == "true"
        ppe_violation_alert_helmet = env_vars.get("PPE_VIOLATION_ALERT_HELMET", "true").lower() == "true"
        ppe_violation_alert_glove = env_vars.get("PPE_VIOLATION_ALERT_GLOVE", "true").lower() == "true"

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
        restricted_zone: Optional[Tuple[float, float, float, float]] = None

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

            # Load restricted zone (alert when person enters this area)
            _res_row = _conn.execute(
                "SELECT coordinates FROM detection_zones WHERE code = 'restricted' LIMIT 1"
            ).fetchone()
            if _res_row:
                _coords = _json.loads(_res_row[0])
                if len(_coords) >= 4:
                    _xs = [float(pt["x"]) for pt in _coords]
                    _ys = [float(pt["y"]) for pt in _coords]
                    restricted_zone = (min(_xs), min(_ys), max(_xs), max(_ys))
                    print(f"[Config] Restricted zone loaded: {restricted_zone}")
                else:
                    print("Warning: restricted zone needs 4 corner points")
            else:
                print("[Config] No restricted zone found in DB")

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
        shm_video_name = env_vars.get(
            "SHM_VIDEO_PATH",
            env_vars.get("SHM_VIDEO_NAME", "/dev/shm/mini_pc_ai_frames.bin"),
        )

        # Parse detection image directory from .env
        detection_image_dir = env_vars.get("DETECTION_IMAGE_DIR", "detection")
        display_enabled = env_vars.get("DISPLAY_ENABLED", "true").lower() == "true"

        # Parse pipeline type from .env
        pipeline_type = env_vars.get("PIPELINE_TYPE", "home").lower()

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
            # PPE violation alert from .env
            ppe_violation_alert_enabled=ppe_violation_alert_enabled,
            ppe_violation_alert_mask=ppe_violation_alert_mask,
            ppe_violation_alert_helmet=ppe_violation_alert_helmet,
            ppe_violation_alert_glove=ppe_violation_alert_glove,
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
            shm_video_name=shm_video_name,
            # Detection image saving
            detection_image_dir=detection_image_dir,
            display_enabled=display_enabled,
            # Detection zone from DB
            detection_zone=detection_zone,
            restricted_zone=restricted_zone,
            # Pipeline type from .env
            pipeline_type=pipeline_type,
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
        if self.pipeline_type not in ("home", "shop", "enterprise"):
            raise ValueError(f"pipeline_type must be 'home', 'shop', or 'enterprise', got {self.pipeline_type}")
        if self.video_source_type not in ("opencv", "zmq", "shm"):
            raise ValueError(
                f"video_source_type must be opencv, zmq, or shm, got {self.video_source_type}"
            )


def create_pipeline(config: Config):
    """Factory function to create the appropriate pipeline based on config.pipeline_type."""
    if config.pipeline_type == "shop":
        from .shop_pipeline import ShopPipeline
        return ShopPipeline(config)
    elif config.pipeline_type == "enterprise":
        from .enterprise_pipeline import EnterprisePipeline
        return EnterprisePipeline(config)
    else:
        from .home_pipeline import HomePipeline
        return HomePipeline(config)
