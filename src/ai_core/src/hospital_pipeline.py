"""
Hospital pipeline — fall detection only.

Standalone (does NOT extend BasePipeline) so it doesn't load FaceRecognizer,
PersonDetector, MaskDetector, or PPEDetector. Only the YOLO pose model is
loaded, keeping RAM minimal.

Detection is restricted to config.detection_zone (same DB-loaded ROI used by
other pipelines). On a confirmed fall, publishes a `fall_detected` ZMQ event.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import cv2
import numpy as np

from .fall_detector import FallDetector
from .utils import FPSCounter, draw_detection_zone, draw_info_overlay
from .zmq_publisher import ZMQPublisher

if TYPE_CHECKING:
    from .pipeline import Config


class HospitalPipeline:
    def __init__(self, config: Config):
        self.config = config

        print("=" * 60)
        print(f"Hospital Pipeline — fall detection only ({config.pipeline_type})")
        print("=" * 60)
        print(f"Device: {config.device}")
        if config.video_source_type == "zmq":
            print(f"Source: ZMQ ({config.zmq_video_endpoint})")
        elif config.video_source_type == "shm":
            print(f"Source: SHM ({config.shm_video_name})")
        else:
            print(f"Source: {config.source}")
        print(f"Detection zone: {'Active' if config.detection_zone else 'Full frame'}")
        print(f"Display window: {'Enabled' if config.display_enabled else 'Disabled (headless)'}")
        print("-" * 60)

        self.fall_detector = FallDetector(model_path=config.pose_model_path)
        self.zmq_publisher = ZMQPublisher(port=config.zmq_publish_port)
        self.fps_counter = FPSCounter()

        print("-" * 60)
        print("Initialization complete!")
        print("=" * 60)

    def _open_video_source(self):
        if self.config.video_source_type == "zmq":
            from .zmq_video_source import ZMQVideoSource

            endpoint = self.config.zmq_video_endpoint
            timeout = self.config.zmq_recv_timeout_ms
            print(f"[Pipeline] Opening ZMQ video source: {endpoint} (timeout={timeout}ms)")
            return ZMQVideoSource(endpoint=endpoint, recv_timeout_ms=timeout)

        if self.config.video_source_type == "shm":
            from .shm_video_source import SharedMemoryVideoSource

            name = self.config.shm_video_name
            timeout = self.config.zmq_recv_timeout_ms
            print(f"[Pipeline] Opening SHM video source: {name} (timeout={timeout}ms)")
            return SharedMemoryVideoSource(shm_name=name, recv_timeout_ms=timeout)

        source = self.config.source
        try:
            source_idx = int(source)
            print(f"[Pipeline] Opening camera {source_idx}...")
            cap = cv2.VideoCapture(source_idx)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.cam_width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.cam_height)
            cap.set(cv2.CAP_PROP_FPS, self.config.cam_fps)
            actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_fps = cap.get(cv2.CAP_PROP_FPS)
            print(f"[Pipeline] Camera: {actual_w}x{actual_h} @ {actual_fps:.1f}fps")
            return cap
        except ValueError:
            pass

        print(f"[Pipeline] Opening video source: {source}")
        return cv2.VideoCapture(source)

    def _process_frame(self, frame: np.ndarray) -> np.ndarray:
        zone_offset_x, zone_offset_y = 0, 0
        detect_frame = frame
        if self.config.detection_zone is not None:
            h, w = frame.shape[:2]
            min_x, min_y, max_x, max_y = self.config.detection_zone
            zx1 = int(min_x * w)
            zy1 = int(min_y * h)
            zx2 = int(max_x * w)
            zy2 = int(max_y * h)
            detect_frame = frame[zy1:zy2, zx1:zx2]
            zone_offset_x, zone_offset_y = zx1, zy1

        # process_frame mutates detect_frame in-place (it's a view of frame
        # when no zone, or a slice of frame when zoned — both update frame).
        _, fall_events = self.fall_detector.process_frame(detect_frame)

        for ev in fall_events:
            x1, y1, x2, y2 = ev["bbox"]
            ev_full = {
                "track_id": ev["track_id"],
                "bbox": [x1 + zone_offset_x, y1 + zone_offset_y,
                         x2 + zone_offset_x, y2 + zone_offset_y],
            }
            print(f"[Hospital] FALL CONFIRMED — track #{ev['track_id']}")
            self.zmq_publisher.send_fall_detected({
                "timestamp": ev["timestamp"],
                "detections": [ev_full],
            })

        if self.config.detection_zone is not None:
            frame = draw_detection_zone(frame, self.config.detection_zone)

        fps = self.fps_counter.update()
        # No tracker queue here; pass empty queue marker.
        frame = draw_info_overlay(frame, fps, 0, "")
        return frame

    def run(self) -> None:
        cap = self._open_video_source()

        if not cap.isOpened():
            if self.config.video_source_type == "shm":
                print("[Pipeline] SHM source not attached yet, waiting for writer...")
            else:
                raise RuntimeError(f"Failed to open video source: {self.config.source}")

        if self.config.display_enabled:
            print("\n[Pipeline] Running... Press 'q' to quit\n")
        else:
            print("\n[Pipeline] Running in headless mode (no OpenCV window)\n")

        frame_count = 0
        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    if self.config.video_source_type in ("zmq", "shm"):
                        continue
                    if self.config.source.isdigit():
                        continue
                    print("[Pipeline] End of video")
                    break

                frame_count += 1
                annotated_frame = self._process_frame(frame)

                if self.config.display_enabled:
                    cv2.imshow("Hospital — Fall Detection", annotated_frame)
                    if (cv2.waitKey(1) & 0xFF) == ord("q"):
                        print("[Pipeline] Quit requested")
                        break

        except KeyboardInterrupt:
            print("\n[Pipeline] Interrupted by user")

        finally:
            cap.release()
            if self.config.display_enabled:
                cv2.destroyAllWindows()
            self.zmq_publisher.close()
            print(f"[Pipeline] Stopped after {frame_count} frames")
