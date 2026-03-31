"""
Enterprise camera pipeline for employee check-in / check-out.

Extends BasePipeline with:
- Line crossing detection via ZoneCounter (same as HomePipeline)
- ZMQ notification ONLY for recognized employees (person_id != "Unknown")
- direction "in"  → checkin event
- direction "out" → checkout event
- Unknown persons are silently ignored — no message sent
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Dict, List

import numpy as np

from .base_pipeline import BasePipeline
from .detector import TrackedAnimal, TrackedPerson
from .utils import (
    compute_crop_score,
    draw_counting_info,
    draw_counting_line,
    draw_in_zone_overlay,
    draw_restricted_zone,
)

if TYPE_CHECKING:
    from .pipeline import Config


class EnterprisePipeline(BasePipeline):
    """Pipeline for enterprise cameras with employee check-in/check-out detection."""

    def __init__(self, config: "Config") -> None:
        # Age/gender recognition is not needed in enterprise mode
        config.age_gender_enabled = False
        super().__init__(config)

    def _init_extra_components(self) -> None:
        config = self.config

        self.counter = None
        # Per-track cooldown to prevent duplicate events when a track oscillates
        # at the line boundary or is briefly lost and regained.
        self._employee_crossing_last_notified: Dict[int, float] = {}
        self._employee_crossing_cooldown: float = 10.0  # seconds

        # Restricted zone alert: per-track cooldown
        self._restricted_alert_last_notified: Dict[int, float] = {}
        self._restricted_alert_cooldown: float = 10.0  # seconds

        # PPE violation alert: tracks that have already been alerted (reset when track leaves frame)
        self._ppe_alerted_tracks: set = set()

        if config.ppe_violation_alert_enabled:
            items = []
            if config.ppe_violation_alert_mask:
                items.append("mask")
            if config.ppe_violation_alert_helmet:
                items.append("helmet")
            if config.ppe_violation_alert_glove:
                items.append("glove")
            if not config.mask_detection_enabled and config.ppe_violation_alert_mask:
                print("[EnterprisePipeline] WARNING: PPE_VIOLATION_ALERT_MASK=true but MASK_DETECTION_ENABLED=false")
            if not config.ppe_detection_enabled and (config.ppe_violation_alert_helmet or config.ppe_violation_alert_glove):
                print("[EnterprisePipeline] WARNING: PPE_VIOLATION_ALERT_HELMET/GLOVE=true but PPE_DETECTION_ENABLED=false")
            print(f"[EnterprisePipeline] PPE violation alerts: Enabled (items={items})")
        else:
            print("[EnterprisePipeline] PPE violation alerts: Disabled (set PPE_VIOLATION_ALERT_ENABLED=true)")

        if config.counting_enabled:
            from .counter import ZoneCounter

            self.counter = ZoneCounter(
                line_start=config.counting_line_start,
                line_end=config.counting_line_end,
                in_direction_point=config.counting_in_direction_point,
            )
            print(f"[EnterprisePipeline] Employee crossing detection: Enabled (line {config.counting_line_start} -> {config.counting_line_end})")
        else:
            print("[EnterprisePipeline] Employee crossing detection: Disabled (set COUNTING_ENABLED=true)")

    def _on_detections(
        self,
        tracked_persons: List[TrackedPerson],
        tracked_animals: List[TrackedAnimal],
        frame: np.ndarray,
    ) -> None:
        active_ids = [p.track_id for p in tracked_persons]
        active_set = set(active_ids)

        track_infos = {}
        track_scores = {}
        for person in tracked_persons:
            tid = person.track_id
            track_infos[tid] = {
                "person_id": self.track_manager.get_label(tid),
            }
            track_scores[tid] = compute_crop_score(person.bbox, frame.shape)

        if self.counter is not None:
            self.counter.update(tracked_persons, frame, self._frame_count, track_infos, track_scores)

            crossings, _ = self.counter.process_lost_tracks(
                active_ids, self._frame_count, self.config.counting_cleanup_max_age
            )

            if crossings and self.zmq_publisher is not None:
                self._publish_employee_crossings(crossings)

        # Purge cooldown entries for tracks no longer active
        self._employee_crossing_last_notified = {
            tid: t for tid, t in self._employee_crossing_last_notified.items()
            if tid in active_set
        }
        self._ppe_alerted_tracks = {tid for tid in self._ppe_alerted_tracks if tid in active_set}

        # Restricted zone: alert when any person enters the restricted area
        if self.config.restricted_zone is not None and self.zmq_publisher is not None:
            self._check_restricted_zone(tracked_persons, frame)

        # PPE violation: alert when a person is confirmed not wearing required PPE
        if self.config.ppe_violation_alert_enabled and self.zmq_publisher is not None:
            self._check_ppe_violations(tracked_persons, frame)

    def _draw_extra_overlays(self, frame: np.ndarray) -> np.ndarray:
        if self.counter is not None:
            pt1, pt2 = self.counter.get_line_points_px(frame.shape)
            in_pt = self.counter.get_in_direction_point_px(frame.shape)
            frame = draw_in_zone_overlay(frame, pt1, pt2, in_pt)
            frame = draw_counting_line(frame, pt1, pt2)
            in_count, out_count = self.counter.get_counts()
            frame = draw_counting_info(frame, in_count, out_count)
        if self.config.restricted_zone is not None:
            frame = draw_restricted_zone(frame, self.config.restricted_zone)
        return frame

    # ------------------------------------------------------------------
    # Publish methods
    # ------------------------------------------------------------------

    def _publish_employee_crossings(self, crossings) -> None:
        """Publish crossing events for recognized employees only."""
        now = time.time()
        detections = []

        for c in crossings:
            # Skip unknown persons — do not send any message
            if c.person_id == "Unknown" or c.person_id.endswith("?"):
                continue

            # Skip if within cooldown window
            elapsed = now - self._employee_crossing_last_notified.get(c.track_id, 0.0)
            if elapsed < self._employee_crossing_cooldown:
                continue

            self._employee_crossing_last_notified[c.track_id] = now

            detection_result = None
            if c.frame is not None and c.bbox is not None:
                detection_result = self.detection_saver.save_frame_with_box(
                    c.frame, c.bbox, "employee_crossing", c.track_id, c.person_id,
                )

            track_info = self.track_manager.get_track_info(c.track_id)
            confidence = track_info.get("avg_score") if track_info else None

            detections.append({
                "track_id": c.track_id,
                "person_id": c.person_id,
                "direction": c.direction,  # "in" = checkin, "out" = checkout
                "confidence": confidence,
                "detection_result": detection_result,
            })

        if detections:
            payload = {"timestamp": time.time(), "detections": detections}
            self.zmq_publisher.send_employee_crossing(payload)

    def _check_restricted_zone(
        self, tracked_persons: List[TrackedPerson], frame: np.ndarray
    ) -> None:
        """Alert when a person's bbox center enters the restricted zone."""
        min_x, min_y, max_x, max_y = self.config.restricted_zone
        fh, fw = frame.shape[:2]
        now = time.time()
        detections = []

        for person in tracked_persons:
            tid = person.track_id
            x1, y1, x2, y2 = person.bbox.astype(int)
            cx_ratio = ((x1 + x2) / 2) / fw
            cy_ratio = ((y1 + y2) / 2) / fh

            if not (min_x <= cx_ratio <= max_x and min_y <= cy_ratio <= max_y):
                continue

            elapsed = now - self._restricted_alert_last_notified.get(tid, 0.0)
            if elapsed < self._restricted_alert_cooldown:
                continue

            self._restricted_alert_last_notified[tid] = now

            label = self.track_manager.get_label(tid)
            track_info = self.track_manager.get_track_info(tid)
            confidence = track_info.get("avg_score") if track_info else None
            detection_result = self.detection_saver.save_frame_with_box(
                frame, person.bbox, "restricted_zone", tid, label
            )

            detections.append({
                "track_id": tid,
                "person_id": label,
                "confidence": confidence,
                "detection_result": detection_result,
            })

        if detections:
            self.zmq_publisher.send_restricted_zone_alert({
                "timestamp": now,
                "detections": detections,
            })

        # Purge stale cooldown entries for tracks no longer in frame
        active_ids = {p.track_id for p in tracked_persons}
        self._restricted_alert_last_notified = {
            tid: t for tid, t in self._restricted_alert_last_notified.items()
            if tid in active_ids
        }

    def _check_ppe_violations(
        self, tracked_persons: List[TrackedPerson], frame: np.ndarray
    ) -> None:
        """Alert when a person is confirmed not wearing required PPE.

        Only fires when status is confirmed False (not None/uncertain).
        Per-track cooldown prevents repeated alerts while person remains in frame.
        """
        cfg = self.config
        now = time.time()
        detections = []

        for person in tracked_persons:
            tid = person.track_id

            mask_status = self.track_manager.get_mask_status(tid)
            print(f"[PPE DEBUG] tid={tid} mask_status={mask_status}")

            # Collect confirmed violations (False only — None means uncertain, skip)
            violations: List[str] = []
            if cfg.ppe_violation_alert_mask and self.track_manager.get_mask_status(tid) is False:
                violations.append("mask")
            if cfg.ppe_violation_alert_helmet and self.track_manager.get_helmet_status(tid) is False:
                violations.append("helmet")
            if cfg.ppe_violation_alert_glove and self.track_manager.get_glove_status(tid) is False:
                violations.append("glove")

            if not violations:
                continue

            # Only alert once per track entry — reset when track leaves frame
            if tid in self._ppe_alerted_tracks:
                print(f"[PPE DEBUG] tid={tid} skip — already alerted this entry")
                continue
            self._ppe_alerted_tracks.add(tid)
            print(f"[PPE DEBUG] tid={tid} violations={violations} — sending ZMQ alert")

            label = self.track_manager.get_label(tid)
            track_info = self.track_manager.get_track_info(tid)
            confidence = track_info.get("avg_score") if track_info else None
            detection_result = self.detection_saver.save_frame_with_box(
                frame, person.bbox, "ppe_violation", tid, label
            )

            detections.append({
                "track_id": tid,
                "person_id": label,
                "violations": violations,
                "confidence": confidence,
                "detection_result": detection_result,
            })

        if detections:
            self.zmq_publisher.send_ppe_violation_alert({
                "timestamp": now,
                "detections": detections,
            })
