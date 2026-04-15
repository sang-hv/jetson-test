"""
Fall detector — YOLO pose + state-machine confirmation.

Ported from src/fall_detection/fall_detector.py for use inside the ai_core
package. Only loads a single YOLO pose model (no face/mask/PPE deps).

process_frame() returns (annotated_frame, fall_events) where fall_events is a
list of {"track_id", "bbox", "timestamp"} for each FALLING -> FALLEN transition
this frame, gated by a per-track wall-clock cooldown.
"""

from __future__ import annotations

import collections
import math
import time
from typing import List, Optional, Tuple

import cv2
import numpy as np
from ultralytics import YOLO


SKELETON: List[Tuple[int, int]] = [
    (0, 1), (0, 2), (1, 3), (2, 4),
    (5, 7), (7, 9), (6, 8), (8, 10),
    (5, 6),
    (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16),
]

COLOR_NORMAL = (0, 200, 0)
COLOR_FALLING = (0, 165, 255)
COLOR_FALL = (0, 0, 220)
FONT = cv2.FONT_HERSHEY_SIMPLEX

STATE_STANDING = "STANDING"
STATE_FALLING = "FALLING"
STATE_FALLEN = "FALLEN"


class FallDetector:
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
        alert_cooldown_sec: float = 30.0,
    ) -> None:
        print(f"[FallDetector] Loading pose model: {model_path}")
        self.model = YOLO(model_path)
        self.conf_threshold = conf_threshold
        self.spine_angle_thresh = spine_angle_thresh
        self.aspect_ratio_thresh = aspect_ratio_thresh
        self.hip_velocity_thresh = hip_velocity_thresh
        self.head_hip_ratio_thresh = head_hip_ratio_thresh
        self.queue_size = queue_size
        self.fall_confirm_frames = fall_confirm_frames
        self.recovery_frames = recovery_frames
        self.alert_cooldown_sec = alert_cooldown_sec

        self._track_states: dict[int, dict] = collections.defaultdict(self._new_track_state)
        self._last_alert_time: dict[int, float] = {}

    def _new_track_state(self) -> dict:
        return {
            "hip_history": collections.deque(maxlen=self.queue_size),
            "state": STATE_STANDING,
            "fall_frame_count": 0,
            "standing_frame_count": 0,
        }

    def process_frame(
        self, frame: np.ndarray
    ) -> Tuple[np.ndarray, List[dict]]:
        """Run pose estimation, update state machine, draw overlays.

        Returns (annotated_frame, fall_events). fall_events contains one entry
        per FALLING -> FALLEN transition this frame (subject to cooldown).
        """
        events: List[dict] = []
        results = self.model.track(frame, persist=True, verbose=False)
        if results and results[0].keypoints is not None:
            frame = self._process_results(frame, results[0], events)
        return frame, events

    def _process_results(
        self, frame: np.ndarray, result, events: List[dict]
    ) -> np.ndarray:
        keypoints_data = result.keypoints
        boxes = result.boxes
        if keypoints_data is None or boxes is None:
            return frame

        kp_array = keypoints_data.data.cpu().numpy()
        box_array = boxes.xyxy.cpu().numpy()
        track_ids = (
            boxes.id.cpu().numpy().astype(int)
            if boxes.id is not None
            else list(range(len(box_array)))
        )

        for i, (kps, box, tid) in enumerate(zip(kp_array, box_array, track_ids)):
            body_height = self._compute_body_height(kps, box)

            spine_angle = self._spine_angle(kps)
            aspect_ratio = self._bbox_aspect_ratio(box)
            hip_vel = self._hip_velocity(int(tid), kps, body_height)
            head_hip_r = self._head_hip_ratio(kps, body_height)

            flags = {
                "spine":    spine_angle  is not None and spine_angle  > self.spine_angle_thresh,
                "aspect":   aspect_ratio is not None and aspect_ratio > self.aspect_ratio_thresh,
                "hip_vel":  hip_vel      is not None and hip_vel      > self.hip_velocity_thresh,
                "head_hip": head_hip_r   is not None and head_hip_r   < self.head_hip_ratio_thresh,
            }

            prev_state = self._track_states[int(tid)]["state"]
            sm_state = self._update_state(int(tid), flags)

            if prev_state == STATE_FALLING and sm_state == STATE_FALLEN:
                now = time.time()
                last = self._last_alert_time.get(int(tid), 0.0)
                if now - last >= self.alert_cooldown_sec:
                    self._last_alert_time[int(tid)] = now
                    x1, y1, x2, y2 = [float(v) for v in box]
                    events.append({
                        "track_id": int(tid),
                        "bbox": [x1, y1, x2, y2],
                        "timestamp": now,
                    })

            if sm_state == STATE_FALLEN:
                color = COLOR_FALL
            elif sm_state == STATE_FALLING:
                color = COLOR_FALLING
            else:
                color = COLOR_NORMAL

            x1, y1, x2, y2 = box.astype(int)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

            fall_cnt = self._track_states[int(tid)]["fall_frame_count"]
            if sm_state == STATE_FALLING:
                label = f"#{int(tid)} FALLING ({fall_cnt}/{self.fall_confirm_frames})"
            else:
                label = f"#{int(tid)} {sm_state}"
            label_y = max(y1 - 8, 16)
            cv2.putText(frame, label, (x1, label_y), FONT, 0.55, color, 2)

            self._draw_skeleton(frame, kps, color)

            if i == 0:
                self._draw_hud(
                    frame, spine_angle, aspect_ratio, hip_vel, head_hip_r,
                    flags, sm_state, fall_cnt, self.fall_confirm_frames,
                )

        return frame

    def _compute_body_height(self, kps: np.ndarray, box: np.ndarray) -> float:
        ct = self.conf_threshold

        top_y: Optional[float] = None
        if kps[0, 2] >= ct:
            top_y = float(kps[0, 1])
        elif kps[1, 2] >= ct and kps[2, 2] >= ct:
            top_y = float((kps[1, 1] + kps[2, 1]) / 2.0)
        elif kps[5, 2] >= ct and kps[6, 2] >= ct:
            top_y = float((kps[5, 1] + kps[6, 1]) / 2.0)

        ankle_ys = [kps[j, 1] for j in [15, 16] if kps[j, 2] >= ct]
        bot_y = float(max(ankle_ys)) if ankle_ys else None

        if top_y is not None and bot_y is not None:
            h = abs(bot_y - top_y)
            if h > 10:
                return h

        _, y1, _, y2 = box
        return max(float(y2 - y1), 1.0)

    def _spine_angle(self, kps: np.ndarray) -> Optional[float]:
        required = [5, 6, 11, 12]
        if any(kps[j, 2] < self.conf_threshold for j in required):
            return None
        shoulder_mid = (kps[5, :2] + kps[6, :2]) / 2.0
        hip_mid = (kps[11, :2] + kps[12, :2]) / 2.0
        dx = float(shoulder_mid[0] - hip_mid[0])
        dy = float(shoulder_mid[1] - hip_mid[1])
        return abs(math.degrees(math.atan2(dx, dy)))

    @staticmethod
    def _bbox_aspect_ratio(box: np.ndarray) -> Optional[float]:
        x1, y1, x2, y2 = box
        h = y2 - y1
        if h < 1:
            return None
        return float((x2 - x1) / h)

    def _hip_velocity(
        self, track_id: int, kps: np.ndarray, body_height: float
    ) -> Optional[float]:
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
        delta = history[-1] - history[0]
        n_frames = len(history) - 1
        return delta / n_frames

    def _head_hip_ratio(
        self, kps: np.ndarray, body_height: float
    ) -> Optional[float]:
        ct = self.conf_threshold
        head_y: Optional[float] = None
        if kps[0, 2] >= ct:
            head_y = float(kps[0, 1])
        elif kps[5, 2] >= ct and kps[6, 2] >= ct:
            head_y = float((kps[5, 1] + kps[6, 1]) / 2.0)
        if head_y is None:
            return None

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

    def _update_state(self, track_id: int, flags: dict) -> str:
        state = self._track_states[track_id]
        triggered = sum(flags.values()) >= 2
        current = state["state"]

        if current == STATE_STANDING:
            if triggered:
                state["fall_frame_count"] += 1
                state["standing_frame_count"] = 0
                state["state"] = STATE_FALLING
            else:
                state["fall_frame_count"] = 0

        elif current == STATE_FALLING:
            if triggered:
                state["fall_frame_count"] += 1
                state["standing_frame_count"] = 0
                if state["fall_frame_count"] >= self.fall_confirm_frames:
                    state["state"] = STATE_FALLEN
            else:
                state["standing_frame_count"] += 1
                if state["standing_frame_count"] >= 3:
                    state["state"] = STATE_STANDING
                    state["fall_frame_count"] = 0
                    state["standing_frame_count"] = 0

        elif current == STATE_FALLEN:
            if not triggered:
                state["standing_frame_count"] += 1
                state["fall_frame_count"] = 0
                if state["standing_frame_count"] >= self.recovery_frames:
                    state["state"] = STATE_STANDING
                    state["standing_frame_count"] = 0
            else:
                state["standing_frame_count"] = 0

        return state["state"]

    def _draw_skeleton(self, frame: np.ndarray, kps: np.ndarray, color: tuple) -> None:
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
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (360, 145), (20, 20, 20), -1)
        cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)

        DIM = (200, 200, 200)
        TRIG = COLOR_FALL
        OK = COLOR_NORMAL

        def tag(triggered: bool) -> tuple:
            return (TRIG, "[TRIGGER]") if triggered else (OK, "[  ok   ]")

        rows = [
            (f"Spine Angle  : {spine_angle:.1f} deg"  if spine_angle   is not None else "Spine Angle  : N/A",     flags["spine"]),
            (f"Aspect Ratio : {aspect_ratio:.2f}"      if aspect_ratio  is not None else "Aspect Ratio : N/A",     flags["aspect"]),
            (f"Hip Velocity : {hip_velocity:.3f} bh/f" if hip_velocity  is not None else "Hip Velocity : N/A",     flags["hip_vel"]),
            (f"Head-Hip Rat.: {head_hip_ratio:.2f}"    if head_hip_ratio is not None else "Head-Hip Rat.: N/A",    flags["head_hip"]),
        ]
        for idx, (text, triggered) in enumerate(rows):
            y = 22 + idx * 22
            cv2.putText(frame, text, (8, y), FONT, 0.48, DIM, 1)
            c, t = tag(triggered)
            cv2.putText(frame, t, (248, y), FONT, 0.48, c, 1)

        if sm_state == STATE_FALLING:
            state_text = f"State: FALLING ({fall_frame_count}/{fall_confirm_frames})"
            state_color = COLOR_FALLING
        elif sm_state == STATE_FALLEN:
            state_text = "State: FALLEN"
            state_color = COLOR_FALL
        else:
            state_text = "State: STANDING"
            state_color = COLOR_NORMAL

        cv2.putText(frame, state_text, (8, 22 + 4 * 22), FONT, 0.55, state_color, 2)
