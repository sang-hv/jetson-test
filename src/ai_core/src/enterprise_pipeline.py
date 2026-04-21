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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Set

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


@dataclass
class _PendingRestrictedAlert:
    """State for a track inside the restricted zone awaiting identity resolution."""

    track_id: int
    entered_at: float       # time.time() when first detected in zone as unknown
    last_bbox: np.ndarray   # updated each frame for image saving
    last_frame: np.ndarray  # updated each frame for image saving


@dataclass
class _PendingPPEViolation:
    """State for a track with a PPE violation awaiting identity resolution."""

    track_id: int
    violations: Set[str]    # accumulated violation types: {"mask", "helmet", "glove"}
    entered_at: float       # time.time() when the first violation was detected
    last_bbox: np.ndarray   # updated each frame for image saving
    last_frame: np.ndarray  # updated each frame for image saving


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
        # Deferred alert: wait for identity resolution before sending
        self._pending_restricted_alerts: Dict[int, _PendingRestrictedAlert] = {}
        self._restricted_alert_id_wait_timeout: float = 5.0  # seconds

        # PPE violation alert: maps track_id → set of violation types already alerted (reset when track leaves frame)
        self._ppe_alerted_tracks: Dict[int, set] = {}
        # Deferred PPE alerts: wait for identity resolution before sending (same pattern as restricted zone)
        self._pending_ppe_violations: Dict[int, _PendingPPEViolation] = {}

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
        self._ppe_alerted_tracks = {tid: v for tid, v in self._ppe_alerted_tracks.items() if tid in active_set}

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

    @staticmethod
    def _is_identified(label: str) -> bool:
        """Return True if the label represents a confirmed identity."""
        return label != "Unknown" and not label.endswith("?")

    def _build_restricted_detection(
        self, tid: int, label: str, bbox: np.ndarray, frame: np.ndarray,
    ) -> dict:
        """Build a single detection dict for the restricted zone alert payload."""
        track_info = self.track_manager.get_track_info(tid)
        confidence = track_info.get("avg_score") if track_info else None
        annotated_frame = draw_restricted_zone(frame, self.config.restricted_zone)
        detection_result = self.detection_saver.save_frame_with_box(
            annotated_frame, bbox, "restricted_zone", tid, label,
        )
        return {
            "track_id": tid,
            "person_id": label,
            "confidence": confidence,
            "detection_result": detection_result,
        }

    def _check_restricted_zone(
        self, tracked_persons: List[TrackedPerson], frame: np.ndarray
    ) -> None:
        """Alert when a person's bbox center enters the restricted zone.

        If the person is unknown/uncertain, defer the alert up to
        ``_restricted_alert_id_wait_timeout`` seconds to allow face
        recognition to resolve their identity.  The deferred alert fires
        when the person is identified, the timeout elapses, or the person
        leaves the zone / track is lost.
        """
        min_x, min_y, max_x, max_y = self.config.restricted_zone
        fh, fw = frame.shape[:2]
        now = time.time()
        detections: List[dict] = []

        # --- Phase A: build per-track lookup & zone membership ---
        person_lookup: Dict[int, tuple] = {}  # tid -> (person, in_zone)
        in_zone_tids: set = set()
        for person in tracked_persons:
            tid = person.track_id
            x1, y1, x2, y2 = person.bbox.astype(int)
            cx_ratio = ((x1 + x2) / 2) / fw
            cy_ratio = ((y1 + y2) / 2) / fh
            is_in_zone = min_x <= cx_ratio <= max_x and min_y <= cy_ratio <= max_y
            person_lookup[tid] = (person, is_in_zone)
            if is_in_zone:
                in_zone_tids.add(tid)

        active_ids = set(person_lookup.keys())

        # --- Phase B: new entries into the zone ---
        for tid in in_zone_tids:
            if tid in self._pending_restricted_alerts:
                continue  # already pending, handled in Phase C
            elapsed = now - self._restricted_alert_last_notified.get(tid, 0.0)
            if elapsed < self._restricted_alert_cooldown:
                continue

            label = self.track_manager.get_label(tid)
            person, _ = person_lookup[tid]

            if self._is_identified(label):
                # Known person — send immediately
                self._restricted_alert_last_notified[tid] = now
                detections.append(
                    self._build_restricted_detection(tid, label, person.bbox, frame)
                )
            else:
                # Unknown / uncertain — defer to allow identification
                self._pending_restricted_alerts[tid] = _PendingRestrictedAlert(
                    track_id=tid,
                    entered_at=now,
                    last_bbox=person.bbox.copy(),
                    last_frame=frame.copy(),
                )

        # --- Phase C: resolve existing pending alerts ---
        resolved_tids: List[int] = []
        for tid, pending in self._pending_restricted_alerts.items():
            label = self.track_manager.get_label(tid)
            elapsed = now - pending.entered_at

            send_now = False
            use_stored = False  # use stored frame/bbox when track is lost

            if self._is_identified(label):
                send_now = True
            elif elapsed >= self._restricted_alert_id_wait_timeout:
                send_now = True
            elif tid not in active_ids:
                # track lost entirely
                send_now = True
                use_stored = True
            elif tid not in in_zone_tids:
                # person left the zone
                send_now = True

            if send_now:
                resolved_tids.append(tid)
                self._restricted_alert_last_notified[tid] = now
                if use_stored:
                    detections.append(self._build_restricted_detection(
                        tid, label, pending.last_bbox, pending.last_frame,
                    ))
                else:
                    person, _ = person_lookup[tid]
                    detections.append(self._build_restricted_detection(
                        tid, label, person.bbox, frame,
                    ))
            else:
                # still pending — keep stored frame/bbox up to date
                if tid in active_ids:
                    person, _ = person_lookup[tid]
                    pending.last_bbox = person.bbox.copy()
                    pending.last_frame = frame.copy()

        for tid in resolved_tids:
            del self._pending_restricted_alerts[tid]

        # --- Phase D: send batched detections ---
        if detections:
            self.zmq_publisher.send_restricted_zone_alert({
                "timestamp": now,
                "detections": detections,
            })

        # --- Phase E: purge stale cooldown entries ---
        self._restricted_alert_last_notified = {
            tid: t for tid, t in self._restricted_alert_last_notified.items()
            if tid in active_ids
        }

    def _build_ppe_detection(
        self,
        tid: int,
        label: str,
        violations: List[str],
        bbox: np.ndarray,
        frame: np.ndarray,
    ) -> dict:
        """Build one detection dict for the PPE violation alert payload."""
        track_info = self.track_manager.get_track_info(tid)
        confidence = track_info.get("avg_score") if track_info else None
        detection_result = self.detection_saver.save_frame_with_box(
            frame, bbox, "ppe_violation", tid, label,
        )
        return {
            "track_id": tid,
            "person_id": label,
            "violations": violations,
            "confidence": confidence,
            "detection_result": detection_result,
        }

    def _check_ppe_violations(
        self, tracked_persons: List[TrackedPerson], frame: np.ndarray
    ) -> None:
        """Alert when a person is confirmed not wearing required PPE.

        If the person's identity has not yet been confirmed by face recognition,
        defer the alert up to ``config.ppe_violation_identity_grace_period``
        seconds.  The deferred alert fires when the person is identified, when
        the grace period expires (sent as "Unknown"), or when the track is lost
        (sent as "Unknown" using the last known frame/bbox).
        """
        cfg = self.config
        now = time.time()
        detections: List[dict] = []

        # --- Phase A: collect per-track new violations ---
        # person_lookup maps tid -> (person, list_of_new_violations_this_frame)
        person_lookup: Dict[int, tuple] = {}
        for person in tracked_persons:
            tid = person.track_id

            violations: List[str] = []
            if cfg.ppe_violation_alert_mask and self.track_manager.get_mask_status(tid) is False:
                violations.append("mask")
            if cfg.ppe_violation_alert_helmet and self.track_manager.get_helmet_status(tid) is False:
                violations.append("helmet")
            if cfg.ppe_violation_alert_glove and self.track_manager.get_glove_status(tid) is False:
                violations.append("glove")

            already_alerted = self._ppe_alerted_tracks.get(tid, set())
            new_violations = [v for v in violations if v not in already_alerted]
            person_lookup[tid] = (person, new_violations)

        active_ids = set(person_lookup.keys())

        # --- Phase B: handle newly detected violations ---
        for tid, (person, new_violations) in person_lookup.items():
            if not new_violations:
                # Still keep stored frame/bbox fresh if a pending entry exists
                if tid in self._pending_ppe_violations:
                    pending = self._pending_ppe_violations[tid]
                    pending.last_bbox = person.bbox.copy()
                    pending.last_frame = frame.copy()
                continue

            if tid in self._pending_ppe_violations:
                # Merge additional violation types into the existing pending entry
                pending = self._pending_ppe_violations[tid]
                pending.violations.update(new_violations)
                pending.last_bbox = person.bbox.copy()
                pending.last_frame = frame.copy()
                continue

            label = self.track_manager.get_label(tid)
            if self._is_identified(label):
                # Identity is already confirmed — send immediately
                self._ppe_alerted_tracks.setdefault(tid, set()).update(new_violations)
                detections.append(
                    self._build_ppe_detection(tid, label, list(new_violations), person.bbox, frame)
                )
                print(
                    f"[PPE] tid={tid} label={label!r} violations={new_violations} "
                    f"— sending ZMQ alert (reason=identified)"
                )
            else:
                # Unknown / uncertain — defer until identity is confirmed or grace expires
                self._pending_ppe_violations[tid] = _PendingPPEViolation(
                    track_id=tid,
                    violations=set(new_violations),
                    entered_at=now,
                    last_bbox=person.bbox.copy(),
                    last_frame=frame.copy(),
                )
                print(
                    f"[PPE] tid={tid} label={label!r} violations={new_violations} "
                    f"— deferred (grace {cfg.ppe_violation_identity_grace_period}s)"
                )

        # --- Phase C: resolve pending alerts ---
        resolved_tids: List[int] = []
        for tid, pending in self._pending_ppe_violations.items():
            label = self.track_manager.get_label(tid)
            elapsed = now - pending.entered_at

            send_now = False
            use_stored = False
            reason = ""

            if tid not in active_ids:
                send_now = True
                use_stored = True
                label = "Unknown"
                reason = "track_lost"
            elif self._is_identified(label):
                send_now = True
                reason = "identified"
            elif elapsed >= cfg.ppe_violation_identity_grace_period:
                send_now = True
                label = "Unknown"
                reason = "grace_expired"

            if send_now:
                resolved_tids.append(tid)
                violations_list = sorted(pending.violations)
                self._ppe_alerted_tracks.setdefault(tid, set()).update(pending.violations)
                if use_stored:
                    detections.append(self._build_ppe_detection(
                        tid, label, violations_list, pending.last_bbox, pending.last_frame,
                    ))
                else:
                    person, _ = person_lookup[tid]
                    detections.append(self._build_ppe_detection(
                        tid, label, violations_list, person.bbox, frame,
                    ))
                print(
                    f"[PPE] tid={tid} label={label!r} violations={violations_list} "
                    f"— sending ZMQ alert (reason={reason})"
                )

        for tid in resolved_tids:
            del self._pending_ppe_violations[tid]

        # --- Phase D: send batched detections ---
        if detections:
            self.zmq_publisher.send_ppe_violation_alert({
                "timestamp": now,
                "detections": detections,
            })
