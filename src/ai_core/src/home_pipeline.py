"""
Home camera pipeline with zone counting, stranger alerts, and animal alerts.

Extends BasePipeline with home-security specific features:
- IN/OUT zone counting with entry/exit line
- Stranger alert for unknown persons in IN zone
- Passerby event for strangers in OUT zone only
- Animal alert for detected animals
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, List

import numpy as np

from .base_pipeline import BasePipeline
from .detector import TrackedAnimal, TrackedPerson
from .utils import (
    compute_crop_score,
    draw_counting_info,
    draw_counting_line,
    draw_in_zone_overlay,
    draw_tracked_animal,
)

if TYPE_CHECKING:
    from .pipeline import Config


class HomePipeline(BasePipeline):
    """Pipeline for home cameras with counting, stranger alerts, and animal alerts."""

    def _init_extra_components(self) -> None:
        config = self.config

        # Print home-specific feature status
        print(f"People counting: {'Enabled' if config.counting_enabled else 'Disabled'}")
        print(f"Animal detection: {'Enabled' if config.animal_detection_enabled else 'Disabled'}")

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

    def _on_detections(
        self,
        tracked_persons: List[TrackedPerson],
        tracked_animals: List[TrackedAnimal],
        frame: np.ndarray,
    ) -> None:
        active_ids = [p.track_id for p in tracked_persons]

        # Update zone counter and process lost tracks
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

            # Check for stranger alerts in IN zone
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
                    track_bboxes = {p.track_id: p.bbox for p in tracked_persons}
                    self._publish_stranger_alerts(alerts, frame, track_bboxes)

    def _on_draw_animals(
        self,
        tracked_animals: List[TrackedAnimal],
        frame: np.ndarray,
    ) -> np.ndarray:
        if self.animal_alert_manager is not None and tracked_animals:
            alerts = self.animal_alert_manager.update(tracked_animals)
            if alerts and self.zmq_publisher is not None:
                animal_bboxes = {a.track_id: a.bbox for a in tracked_animals}
                self._publish_animal_alerts(alerts, frame, animal_bboxes)
            for animal in tracked_animals:
                frame = draw_tracked_animal(frame, animal)
        return frame

    def _draw_extra_overlays(self, frame: np.ndarray) -> np.ndarray:
        if self.counter is not None:
            pt1, pt2 = self.counter.get_line_points_px(frame.shape)
            in_pt = self.counter.get_in_direction_point_px(frame.shape)
            frame = draw_in_zone_overlay(frame, pt1, pt2, in_pt)
            frame = draw_counting_line(frame, pt1, pt2)
            in_count, out_count = self.counter.get_counts()
            frame = draw_counting_info(frame, in_count, out_count)
        return frame

    # ------------------------------------------------------------------
    # Publish methods
    # ------------------------------------------------------------------

    def _publish_crossings(self, crossings) -> None:
        """Build ZMQ payload from crossing events and publish."""
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
        payload = {"timestamp": time.time(), "detections": detections}
        self.zmq_publisher.send_detection(payload)

    def _publish_passerby_events(self, events) -> None:
        """Build ZMQ payload from passerby events and publish."""
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
        payload = {"timestamp": time.time(), "detections": detections}
        self.zmq_publisher.send_passerby_event(payload)

    def _publish_animal_alerts(self, alerts, frame=None, track_bboxes=None) -> None:
        """Build ZMQ payload from animal alert events and publish."""
        detections = []
        for alert in alerts:
            detection_result = None
            bbox = track_bboxes.get(alert.track_id) if track_bboxes else None
            if frame is not None and bbox is not None:
                detection_result = self.detection_saver.save_frame_with_box(
                    frame, bbox, "animal_alert", alert.track_id, alert.class_name,
                )
            detections.append({
                "track_id": alert.track_id,
                "class_id": alert.class_id,
                "class_name": alert.class_name,
                "confidence": alert.confidence,
                "alert_count": alert.alert_count,
                "detection_result": detection_result,
            })
        payload = {"timestamp": time.time(), "detections": detections}
        self.zmq_publisher.send_animal_alert(payload)

    def _publish_stranger_alerts(self, alerts, frame=None, track_bboxes=None) -> None:
        """Build ZMQ payload from stranger alert events and publish."""
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
        payload = {"timestamp": time.time(), "detections": detections}
        self.zmq_publisher.send_stranger_alert(payload)
