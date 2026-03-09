"""
Line crossing counter for people counting (IN/OUT).

Uses cross product to determine which side of a configurable line
each tracked person's centroid is on. When a track crosses the line,
it is counted as IN or OUT based on the configured origin direction.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import List, Tuple

from .detector import TrackedPerson


@dataclass
class CrossingEvent:
    """Fired once per track when it crosses the counting line."""
    track_id: int
    direction: str  # "in" or "out"


class LineCrossingCounter:
    """
    Counts people crossing a line defined by two points (in ratio coordinates 0.0-1.0).

    Direction logic:
    - Compute the cross product sign of the origin (0,0) relative to the line.
      This determines the "origin side".
    - `origin_direction` config says whether the origin side is "in" or "out".
    - When a track moves from origin side to the other side:
      - If origin_direction="in" → that's an OUT event
      - If origin_direction="out" → that's an IN event
    - And vice versa for the reverse direction.
    """

    def __init__(
        self,
        line_start: Tuple[float, float],
        line_end: Tuple[float, float],
        origin_direction: str = "in",
    ):
        self._line_start = line_start  # (x, y) as ratio 0.0-1.0
        self._line_end = line_end
        self._origin_direction = origin_direction  # "in" or "out"

        # Per-track state
        self._track_sides: dict[int, int] = {}  # track_id -> sign (+1 or -1)
        self._track_last_seen: dict[int, int] = {}  # track_id -> frame_number
        self._counted_tracks: set[int] = set()  # debouncing: already counted

        self._in_count = 0
        self._out_count = 0
        self._lock = threading.Lock()

        # Pre-compute origin sign (which side of the line (0,0) is on)
        self._origin_sign = self._cross_sign(
            self._line_start, self._line_end, (0.0, 0.0)
        )

    @staticmethod
    def _cross_sign(
        line_start: Tuple[float, float],
        line_end: Tuple[float, float],
        point: Tuple[float, float],
    ) -> int:
        """
        Compute the sign of the cross product of (line_end - line_start) x (point - line_start).

        Returns +1 if point is on the left side, -1 if on the right side, 0 if on the line.
        """
        dx = line_end[0] - line_start[0]
        dy = line_end[1] - line_start[1]
        px = point[0] - line_start[0]
        py = point[1] - line_start[1]
        cross = dx * py - dy * px
        if cross > 0:
            return 1
        elif cross < 0:
            return -1
        return 0

    def update(
        self,
        tracked_persons: List[TrackedPerson],
        frame_shape: Tuple[int, ...],
        frame_number: int,
    ) -> List[CrossingEvent]:
        """
        Update counter with current frame's tracked persons.

        Args:
            tracked_persons: List of tracked person detections.
            frame_shape: (height, width, channels) of the frame.
            frame_number: Current frame index (for cleanup timing).

        Returns:
            List of CrossingEvent for any new line crossings detected this frame.
        """
        h, w = frame_shape[:2]
        crossings: List[CrossingEvent] = []

        # Convert line from ratio to pixel, then back to ratio for centroid comparison
        # Actually, convert centroid to ratio space to compare with line in ratio space
        with self._lock:
            for person in tracked_persons:
                track_id = person.track_id
                cx, cy = person.center

                # Convert centroid to ratio coordinates
                cx_ratio = cx / w
                cy_ratio = cy / h

                current_sign = self._cross_sign(
                    self._line_start, self._line_end, (cx_ratio, cy_ratio)
                )

                # Skip if exactly on the line
                if current_sign == 0:
                    self._track_last_seen[track_id] = frame_number
                    continue

                prev_sign = self._track_sides.get(track_id)

                if prev_sign is not None and prev_sign != current_sign:
                    # Crossed the line — only count if not already counted
                    if track_id not in self._counted_tracks:
                        # Determine direction: moving from origin side to other side?
                        leaving_origin_side = prev_sign == self._origin_sign

                        if self._origin_direction == "in":
                            if leaving_origin_side:
                                self._out_count += 1
                                crossings.append(CrossingEvent(track_id=track_id, direction="out"))
                            else:
                                self._in_count += 1
                                crossings.append(CrossingEvent(track_id=track_id, direction="in"))
                        else:  # origin_direction == "out"
                            if leaving_origin_side:
                                self._in_count += 1
                                crossings.append(CrossingEvent(track_id=track_id, direction="in"))
                            else:
                                self._out_count += 1
                                crossings.append(CrossingEvent(track_id=track_id, direction="out"))

                        self._counted_tracks.add(track_id)

                # Update state
                self._track_sides[track_id] = current_sign
                self._track_last_seen[track_id] = frame_number

        return crossings

    def cleanup(self, frame_number: int, max_age: int = 150) -> None:
        """
        Remove stale tracks from counting memory to prevent memory leaks.

        Args:
            frame_number: Current frame index.
            max_age: Max frames since last seen before removing a track.
        """
        with self._lock:
            stale_ids = [
                tid
                for tid, last_frame in self._track_last_seen.items()
                if frame_number - last_frame > max_age
            ]
            for tid in stale_ids:
                self._track_sides.pop(tid, None)
                self._track_last_seen.pop(tid, None)
                self._counted_tracks.discard(tid)

    def get_counts(self) -> Tuple[int, int]:
        """Return (in_count, out_count) thread-safely."""
        with self._lock:
            return self._in_count, self._out_count

    def get_line_points_px(
        self, frame_shape: Tuple[int, ...]
    ) -> Tuple[Tuple[int, int], Tuple[int, int]]:
        """Convert line from ratio coordinates to pixel coordinates."""
        h, w = frame_shape[:2]
        pt1 = (int(self._line_start[0] * w), int(self._line_start[1] * h))
        pt2 = (int(self._line_end[0] * w), int(self._line_end[1] * h))
        return pt1, pt2
