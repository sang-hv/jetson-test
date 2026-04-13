"""
Shop camera pipeline with real-time zone entry notifications.

Extends BasePipeline with shop-specific features:
- Real-time ZMQ notification when a person enters the IN zone
- Person info: known/unknown identity, age, gender
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Dict, List, Set

import numpy as np

from .base_pipeline import BasePipeline
from .detector import TrackedAnimal, TrackedPerson
from .utils import (
    compute_crop_score,
    draw_counting_info,
    draw_counting_line,
    draw_in_zone_overlay,
)

if TYPE_CHECKING:
    from .pipeline import Config


class ShopPipeline(BasePipeline):
    """Pipeline for shop cameras with zone entry notifications."""

    def _init_extra_components(self) -> None:
        config = self.config

        self.counter = None
        # Maps track_id -> timestamp of last zone_entry notification.
        # Used as cooldown to prevent duplicate messages when a track oscillates
        # at the zone boundary or temporarily loses/regains tracking.
        self._zone_entry_last_notified: Dict[int, float] = {}
        self._zone_entry_cooldown: float = 10.0  # seconds

        if config.counting_enabled:
            from .counter import ZoneCounter

            self.counter = ZoneCounter(
                line_start=config.counting_line_start,
                line_end=config.counting_line_end,
                in_direction_point=config.counting_in_direction_point,
            )
            print(f"[ShopPipeline] Zone entry detection: Enabled (line {config.counting_line_start} -> {config.counting_line_end})")
        else:
            print("[ShopPipeline] Zone entry detection: Disabled (set COUNTING_ENABLED=true)")

    def _on_detections(
        self,
        tracked_persons: List[TrackedPerson],
        tracked_animals: List[TrackedAnimal],
        frame: np.ndarray,
    ) -> None:
        if self.counter is None:
            return

        active_ids = set(p.track_id for p in tracked_persons)

        # Gather track info for zone counter
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

        # Cleanup lost tracks and detect zone exits
        crossings, _ = self.counter.process_lost_tracks(
            list(active_ids), self._frame_count, self.config.counting_cleanup_max_age
        )

        # Notify when a known person exits (IN → OUT)
        if crossings and self.zmq_publisher is not None:
            exit_detections = []
            for c in crossings:
                if c.direction != "out":
                    continue
                if c.person_id == "Unknown" or c.person_id.endswith("?"):
                    continue
                detection_result = None
                if c.frame is not None and c.bbox is not None:
                    detection_result = self.detection_saver.save_frame_with_box(
                        c.frame, c.bbox, "zone_exit", c.track_id, c.person_id,
                    )
                track_info = self.track_manager.get_track_info(c.track_id)
                confidence = track_info.get("avg_score") if track_info else None
                exit_detections.append({
                    "track_id": c.track_id,
                    "person_id": c.person_id,
                    "age": c.age,
                    "gender": c.gender,
                    "confidence": confidence,
                    "detection_result": detection_result,
                })
            if exit_detections:
                self.zmq_publisher.send_zone_exit({
                    "timestamp": time.time(),
                    "detections": exit_detections,
                })

        # Detect new entries into IN zone
        in_zone_tracks = self.counter.get_tracks_in_zone("in")

        # Find tracks currently in IN zone that haven't been notified within the cooldown window.
        # Using a time-based cooldown (instead of a plain set) prevents duplicate messages when:
        # - a track oscillates at the zone boundary (bbox jitter)
        # - tracking is temporarily lost and regained (same or new track_id)
        now = time.time()
        new_entries = {
            tid for tid in in_zone_tracks & active_ids
            if now - self._zone_entry_last_notified.get(tid, 0.0) >= self._zone_entry_cooldown
        }

        # Purge cooldown entries for tracks that are no longer relevant
        relevant_ids = active_ids | in_zone_tracks
        self._zone_entry_last_notified = {
            tid: t for tid, t in self._zone_entry_last_notified.items()
            if tid in relevant_ids
        }

        if not new_entries or self.zmq_publisher is None:
            return

        track_bboxes = {p.track_id: p.bbox for p in tracked_persons}
        detections = []
        for tid in new_entries:
            self._zone_entry_last_notified[tid] = now

            info = track_infos.get(tid, {})
            track_info = self.track_manager.get_track_info(tid)
            confidence = track_info.get("avg_score") if track_info else None

            # Save detection image
            detection_result = None
            bbox = track_bboxes.get(tid)
            if bbox is not None:
                detection_result = self.detection_saver.save_frame_with_box(
                    frame, bbox, "zone_entry", tid, info.get("person_id", "Unknown"),
                )

            detections.append({
                "track_id": tid,
                "person_id": info.get("person_id", "Unknown"),
                "age": info.get("age"),
                "gender": info.get("gender"),
                "confidence": confidence,
                "detection_result": detection_result,
            })

        payload = {"timestamp": time.time(), "detections": detections}
        self.zmq_publisher.send_zone_entry(payload)

    def _draw_extra_overlays(self, frame: np.ndarray) -> np.ndarray:
        if self.counter is not None:
            pt1, pt2 = self.counter.get_line_points_px(frame.shape)
            in_pt = self.counter.get_in_direction_point_px(frame.shape)
            frame = draw_in_zone_overlay(frame, pt1, pt2, in_pt)
            frame = draw_counting_line(frame, pt1, pt2)
            in_count, out_count = self.counter.get_counts()
            frame = draw_counting_info(frame, in_count, out_count)
        return frame
