"""
Fall Detection — Improved Algorithm
=====================================
Detects human falls in real-time using YOLOv8/YOLO11 Pose Estimation.

Improvements over basic heuristics:
  - 4 features instead of 3 (added Head-Hip vertical ratio)
  - All pixel-based thresholds normalized by body height → camera-invariant
  - Hip center velocity replaces noisy nose velocity
  - State machine (STANDING → FALLING → FALLEN) requires ≥8 consecutive frames
    before confirming a fall, eliminating single-frame false alarms
  - Recovery requires ≥15 standing frames to reset, preventing oscillation

Usage:
    python fall_detector.py               # webcam
    python fall_detector.py video.mp4     # video file
"""

import sys
import math
import collections
from typing import Optional

import cv2
import numpy as np
from ultralytics import YOLO

# ---------------------------------------------------------------------------
# COCO-17 skeleton connections (index pairs)
# ---------------------------------------------------------------------------
SKELETON: list[tuple[int, int]] = [
    # Face
    (0, 1), (0, 2), (1, 3), (2, 4),
    # Arms
    (5, 7), (7, 9), (6, 8), (8, 10),
    # Shoulders
    (5, 6),
    # Torso
    (5, 11), (6, 12), (11, 12),
    # Legs
    (11, 13), (13, 15), (12, 14), (14, 16),
]

# Visual style
COLOR_NORMAL  = (0, 200, 0)    # Green  — STANDING / normal
COLOR_FALLING = (0, 165, 255)  # Orange — FALLING (not yet confirmed)
COLOR_FALL    = (0, 0, 220)    # Red    — FALLEN (confirmed)
FONT          = cv2.FONT_HERSHEY_SIMPLEX

# State constants
STATE_STANDING = "STANDING"
STATE_FALLING  = "FALLING"
STATE_FALLEN   = "FALLEN"


# ---------------------------------------------------------------------------
# FallDetector
# ---------------------------------------------------------------------------
class FallDetector:
    """
    Per-frame fall detection with 4 normalized features and a state machine.

    Args:
        model_path (str): Path to a YOLO pose model (.pt).
        conf_threshold (float): Minimum keypoint confidence to use.
        spine_angle_thresh (float): Degrees from vertical above which spine is
                                    considered tilted (default 60°).
        aspect_ratio_thresh (float): bbox width/height above which person is
                                     considered horizontal (default 1.3).
        hip_velocity_thresh (float): Downward hip velocity in body-heights/frame
                                     above which a fast drop is flagged (default 0.08).
        head_hip_ratio_thresh (float): (hip_y - head_y) / body_height below which
                                       head and hips are considered at similar height
                                       (person lying down, default 0.15).
        queue_size (int): Number of frames kept for velocity tracking.
        fall_confirm_frames (int): Consecutive frames with ≥2 features active
                                   required before transitioning FALLING → FALLEN.
        recovery_frames (int): Consecutive non-fall frames required to reset
                               from FALLEN → STANDING.
    """

    def __init__(
        self,
        model_path: str = "yolo11n-pose.pt",
        conf_threshold: float = 0.5,
        spine_angle_thresh: float = 60.0,
        aspect_ratio_thresh: float = 1.3,
        hip_velocity_thresh: float = 0.08,
        head_hip_ratio_thresh: float = 0.15,
        queue_size: int = 10,
        fall_confirm_frames: int = 8,
        recovery_frames: int = 15,
    ) -> None:
        self.model = YOLO(model_path)
        self.conf_threshold       = conf_threshold
        self.spine_angle_thresh   = spine_angle_thresh
        self.aspect_ratio_thresh  = aspect_ratio_thresh
        self.hip_velocity_thresh  = hip_velocity_thresh
        self.head_hip_ratio_thresh = head_hip_ratio_thresh
        self.queue_size           = queue_size
        self.fall_confirm_frames  = fall_confirm_frames
        self.recovery_frames      = recovery_frames

        # Per-track state: track_id → state dict
        self._track_states: dict[int, dict] = collections.defaultdict(
            self._new_track_state
        )

    def _new_track_state(self) -> dict:
        return {
            "hip_history":          collections.deque(maxlen=self.queue_size),
            "state":                STATE_STANDING,
            "fall_frame_count":     0,
            "standing_frame_count": 0,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_frame(self, frame: np.ndarray) -> np.ndarray:
        """Run pose estimation, compute features, update state machine, draw overlays."""
        results = self.model.track(frame, persist=True, verbose=False)

        if results and results[0].keypoints is not None:
            frame = self._process_results(frame, results[0])

        return frame

    # ------------------------------------------------------------------
    # Internal orchestration
    # ------------------------------------------------------------------

    def _process_results(self, frame: np.ndarray, result) -> np.ndarray:
        keypoints_data = result.keypoints
        boxes          = result.boxes

        if keypoints_data is None or boxes is None:
            return frame

        kp_array  = keypoints_data.data.cpu().numpy()   # (N, 17, 3)
        box_array = boxes.xyxy.cpu().numpy()            # (N, 4)
        track_ids = (
            boxes.id.cpu().numpy().astype(int)
            if boxes.id is not None
            else list(range(len(box_array)))
        )

        for i, (kps, box, tid) in enumerate(zip(kp_array, box_array, track_ids)):
            # --- normalize by body height ---
            body_height = self._compute_body_height(kps, box)

            # --- compute 4 features ---
            spine_angle   = self._spine_angle(kps)
            aspect_ratio  = self._bbox_aspect_ratio(box)
            hip_vel       = self._hip_velocity(tid, kps, body_height)
            head_hip_r    = self._head_hip_ratio(kps, body_height)

            # --- feature flags (missing → not triggered) ---
            flags = {
                "spine":    spine_angle  is not None and spine_angle  > self.spine_angle_thresh,
                "aspect":   aspect_ratio is not None and aspect_ratio > self.aspect_ratio_thresh,
                "hip_vel":  hip_vel      is not None and hip_vel      > self.hip_velocity_thresh,
                "head_hip": head_hip_r   is not None and head_hip_r   < self.head_hip_ratio_thresh,
            }

            # --- state machine ---
            prev_state = self._track_states[tid]["state"]
            sm_state   = self._update_state(tid, flags)

            # Log the moment a fall is confirmed
            if prev_state == STATE_FALLING and sm_state == STATE_FALLEN:
                print(f"[ALERT] Track #{tid} — FALL CONFIRMED")

            # --- visual style by state ---
            if sm_state == STATE_FALLEN:
                color = COLOR_FALL
            elif sm_state == STATE_FALLING:
                color = COLOR_FALLING
            else:
                color = COLOR_NORMAL

            # --- draw bbox ---
            x1, y1, x2, y2 = box.astype(int)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

            # --- status label ---
            fall_cnt = self._track_states[tid]["fall_frame_count"]
            if sm_state == STATE_FALLING:
                label = f"#{tid} FALLING ({fall_cnt}/{self.fall_confirm_frames})"
            else:
                label = f"#{tid} {sm_state}"
            label_y = max(y1 - 8, 16)
            cv2.putText(frame, label, (x1, label_y), FONT, 0.55, color, 2)

            # --- skeleton ---
            self._draw_skeleton(frame, kps, color)

            # --- HUD for first person ---
            if i == 0:
                self._draw_hud(
                    frame, spine_angle, aspect_ratio, hip_vel, head_hip_r,
                    flags, sm_state, fall_cnt, self.fall_confirm_frames,
                )

        return frame

    # ------------------------------------------------------------------
    # Body height normalization
    # ------------------------------------------------------------------

    def _compute_body_height(self, kps: np.ndarray, box: np.ndarray) -> float:
        """
        Estimate person height for feature normalization.

        Priority chain:
          1. Nose (kp[0])     → mid-ankle ((kp[15]+kp[16])/2 or single ankle)
          2. Shoulder-mid     → mid-ankle
          3. Bounding box height (fallback)
        """
        ct = self.conf_threshold

        # --- top of body ---
        top_y: Optional[float] = None
        if kps[0, 2] >= ct:
            top_y = float(kps[0, 1])
        elif kps[1, 2] >= ct and kps[2, 2] >= ct:
            top_y = float((kps[1, 1] + kps[2, 1]) / 2.0)
        elif kps[5, 2] >= ct and kps[6, 2] >= ct:
            top_y = float((kps[5, 1] + kps[6, 1]) / 2.0)

        # --- bottom of body (ankles) ---
        ankle_ys = [kps[j, 1] for j in [15, 16] if kps[j, 2] >= ct]
        bot_y = float(max(ankle_ys)) if ankle_ys else None

        if top_y is not None and bot_y is not None:
            h = abs(bot_y - top_y)
            if h > 10:
                return h

        # Fallback: bbox height
        _, y1, _, y2 = box
        return max(float(y2 - y1), 1.0)

    # ------------------------------------------------------------------
    # Feature 1 — Spine Angle (unchanged)
    # ------------------------------------------------------------------

    def _spine_angle(self, kps: np.ndarray) -> Optional[float]:
        """
        Tilt angle of the spine relative to vertical.
        0° = upright, 90° = horizontal.
        Uses kp[5,6] (shoulders) and kp[11,12] (hips).
        """
        required = [5, 6, 11, 12]
        if any(kps[j, 2] < self.conf_threshold for j in required):
            return None

        shoulder_mid = (kps[5, :2] + kps[6, :2]) / 2.0
        hip_mid      = (kps[11, :2] + kps[12, :2]) / 2.0

        dx = float(shoulder_mid[0] - hip_mid[0])
        dy = float(shoulder_mid[1] - hip_mid[1])
        return abs(math.degrees(math.atan2(dx, dy)))

    # ------------------------------------------------------------------
    # Feature 2 — Bounding Box Aspect Ratio (threshold raised to 1.3)
    # ------------------------------------------------------------------

    @staticmethod
    def _bbox_aspect_ratio(box: np.ndarray) -> Optional[float]:
        """width / height of bounding box. >1.3 → person likely lying down."""
        x1, y1, x2, y2 = box
        h = y2 - y1
        if h < 1:
            return None
        return float((x2 - x1) / h)

    # ------------------------------------------------------------------
    # Feature 3 — Hip Center Velocity (normalized, replaces head velocity)
    # ------------------------------------------------------------------

    def _hip_velocity(
        self, track_id: int, kps: np.ndarray, body_height: float
    ) -> Optional[float]:
        """
        Signed downward velocity of the hip center, normalized by body height.
        Positive = moving down in image coordinates = falling direction.

        Returns the mean per-frame delta over the sliding window.
        Returns None if hip keypoints are unavailable.
        """
        ct = self.conf_threshold
        l_conf, r_conf = kps[11, 2], kps[12, 2]

        if l_conf < ct and r_conf < ct:
            return None

        if l_conf >= ct and r_conf >= ct:
            hip_y = float((kps[11, 1] + kps[12, 1]) / 2.0)
        elif l_conf >= ct:
            hip_y = float(kps[11, 1])
        else:
            hip_y = float(kps[12, 1])

        hip_y_norm = hip_y / body_height
        history = self._track_states[track_id]["hip_history"]
        history.append(hip_y_norm)

        if len(history) < 3:
            return None

        # Mean signed velocity per frame (positive = downward movement)
        delta   = history[-1] - history[0]
        n_frames = len(history) - 1
        return delta / n_frames

    # ------------------------------------------------------------------
    # Feature 4 — Head-Hip Vertical Ratio (new)
    # ------------------------------------------------------------------

    def _head_hip_ratio(
        self, kps: np.ndarray, body_height: float
    ) -> Optional[float]:
        """
        (hip_center_y - head_y) / body_height

        Standing: head well above hips → ratio ~0.4–0.6
        Sitting:  ratio ~0.25–0.35
        Lying:    head and hips at similar height → ratio <0.15

        Returns None if required keypoints are unavailable.
        """
        ct = self.conf_threshold

        # Head: nose preferred, shoulder-mid as fallback
        head_y: Optional[float] = None
        if kps[0, 2] >= ct:
            head_y = float(kps[0, 1])
        elif kps[5, 2] >= ct and kps[6, 2] >= ct:
            head_y = float((kps[5, 1] + kps[6, 1]) / 2.0)
        if head_y is None:
            return None

        # Hip center
        l_conf, r_conf = kps[11, 2], kps[12, 2]
        if l_conf < ct and r_conf < ct:
            return None
        if l_conf >= ct and r_conf >= ct:
            hip_y = float((kps[11, 1] + kps[12, 1]) / 2.0)
        elif l_conf >= ct:
            hip_y = float(kps[11, 1])
        else:
            hip_y = float(kps[12, 1])

        return (hip_y - head_y) / body_height

    # ------------------------------------------------------------------
    # State Machine
    # ------------------------------------------------------------------

    def _update_state(self, track_id: int, flags: dict) -> str:
        """
        Transition diagram:
          STANDING ──(≥2 features)──────────────────► FALLING
          FALLING  ──(≥2 features × fall_confirm_frames)──► FALLEN
          FALLING  ──(<2 features × 3 frames)────────► STANDING
          FALLEN   ──(<2 features × recovery_frames)─► STANDING
        """
        state     = self._track_states[track_id]
        triggered = sum(flags.values()) >= 2
        current   = state["state"]

        if current == STATE_STANDING:
            if triggered:
                state["fall_frame_count"]     += 1
                state["standing_frame_count"]  = 0
                state["state"]                 = STATE_FALLING
            else:
                state["fall_frame_count"] = 0

        elif current == STATE_FALLING:
            if triggered:
                state["fall_frame_count"]     += 1
                state["standing_frame_count"]  = 0
                if state["fall_frame_count"] >= self.fall_confirm_frames:
                    state["state"] = STATE_FALLEN
            else:
                state["standing_frame_count"] += 1
                # 3-frame noise gate: tolerate brief keypoint dropout
                if state["standing_frame_count"] >= 3:
                    state["state"]                = STATE_STANDING
                    state["fall_frame_count"]     = 0
                    state["standing_frame_count"] = 0

        elif current == STATE_FALLEN:
            if not triggered:
                state["standing_frame_count"] += 1
                state["fall_frame_count"]      = 0
                if state["standing_frame_count"] >= self.recovery_frames:
                    state["state"]                = STATE_STANDING
                    state["standing_frame_count"] = 0
            else:
                state["standing_frame_count"] = 0

        return state["state"]

    # ------------------------------------------------------------------
    # Drawing helpers
    # ------------------------------------------------------------------

    def _draw_skeleton(
        self, frame: np.ndarray, kps: np.ndarray, color: tuple
    ) -> None:
        for j in range(17):
            if kps[j, 2] >= self.conf_threshold:
                cx, cy = int(kps[j, 0]), int(kps[j, 1])
                cv2.circle(frame, (cx, cy), 4, color, -1)

        for a, b in SKELETON:
            if kps[a, 2] >= self.conf_threshold and kps[b, 2] >= self.conf_threshold:
                pa = (int(kps[a, 0]), int(kps[a, 1]))
                pb = (int(kps[b, 0]), int(kps[b, 1]))
                cv2.line(frame, pa, pb, color, 2)

    @staticmethod
    def _draw_hud(
        frame: np.ndarray,
        spine_angle: Optional[float],
        aspect_ratio: Optional[float],
        hip_velocity: Optional[float],
        head_hip_ratio: Optional[float],
        flags: dict,
        sm_state: str,
        fall_frame_count: int,
        fall_confirm_frames: int,
    ) -> None:
        """
        Semi-transparent HUD in the top-left corner showing all 4 features,
        their trigger status, and current state machine state.
        """
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (360, 145), (20, 20, 20), -1)
        cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)

        DIM  = (200, 200, 200)  # dim white for labels
        TRIG = COLOR_FALL       # red  for triggered
        OK   = COLOR_NORMAL     # green for ok

        def tag(triggered: bool) -> tuple:
            return (TRIG, "[TRIGGER]") if triggered else (OK, "[  ok   ]")

        # Feature lines
        rows = [
            (f"Spine Angle  : {spine_angle:.1f} deg"  if spine_angle   is not None else "Spine Angle  : N/A",     flags["spine"]),
            (f"Aspect Ratio : {aspect_ratio:.2f}"      if aspect_ratio  is not None else "Aspect Ratio : N/A",     flags["aspect"]),
            (f"Hip Velocity : {hip_velocity:.3f} bh/f" if hip_velocity  is not None else "Hip Velocity : N/A",     flags["hip_vel"]),
            (f"Head-Hip Rat.: {head_hip_ratio:.2f}"    if head_hip_ratio is not None else "Head-Hip Rat.: N/A",    flags["head_hip"]),
        ]

        for idx, (text, triggered) in enumerate(rows):
            y = 22 + idx * 22
            cv2.putText(frame, text, (8, y),   FONT, 0.48, DIM,  1)
            c, t = tag(triggered)
            cv2.putText(frame, t,    (248, y), FONT, 0.48, c,    1)

        # State line
        if sm_state == STATE_FALLING:
            state_text = f"State: FALLING ({fall_frame_count}/{fall_confirm_frames})"
            state_color = COLOR_FALLING
        elif sm_state == STATE_FALLEN:
            state_text  = "State: FALLEN"
            state_color = COLOR_FALL
        else:
            state_text  = "State: STANDING"
            state_color = COLOR_NORMAL

        cv2.putText(frame, state_text, (8, 22 + 4 * 22), FONT, 0.55, state_color, 2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    source = sys.argv[1] if len(sys.argv) > 1 else 0
    detector = FallDetector(
        model_path="yolo11l-pose.pt",
        conf_threshold=0.5,
        spine_angle_thresh=60.0,
        aspect_ratio_thresh=1.3,
        hip_velocity_thresh=0.08,
        head_hip_ratio_thresh=0.15,
        queue_size=10,
        fall_confirm_frames=8,
        recovery_frames=15,
    )

    cap = cv2.VideoCapture(1)
    if not cap.isOpened():
        print(f"[ERROR] Cannot open source: {source}")
        sys.exit(1)

    print(f"[INFO] Fall Detector started — source: {source!r}")
    print("[INFO] Press 'q' to quit.")

    while True:
        ret, frame = cap.read()
        if not ret:
            print("[INFO] Stream ended.")
            break

        frame = detector.process_frame(frame)
        cv2.imshow("Fall Detection", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
