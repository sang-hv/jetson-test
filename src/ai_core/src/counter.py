"""
Zone-based people counter (IN/OUT).

Uses a configurable line to divide the frame into two zones (IN and OUT).
Tracks which zone each person first appeared in and last was seen in.
When a track is lost (stale), compares first_zone vs last_zone:
- first_zone="out" + last_zone="in" → IN event
- first_zone="in" + last_zone="out" → OUT event
- same zone → no count (person appeared and disappeared in the same zone)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .detector import TrackedPerson


@dataclass
class CrossingEvent:
    """Fired when a lost track is determined to have moved between zones."""
    track_id: int
    direction: str  # "in" or "out"
    person_id: str = "Unknown"
    age: Optional[int] = None
    gender: Optional[str] = None


@dataclass
class PasserbyEvent:
    """Fired when a stranger appeared and disappeared in the OUT zone."""
    track_id: int
    person_id: str = "Unknown"
    age: Optional[int] = None
    gender: Optional[str] = None


class ZoneCounter:
    """
    Counts people moving between zones defined by a dividing line.

    The line splits the frame into two zones. `origin_direction` determines
    which side of the line (the side containing the origin (0,0)) is "in" or "out".
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

        # Per-track zone state
        self._track_first_zone: dict[int, str] = {}  # track_id -> "in"/"out"
        self._track_last_zone: dict[int, str] = {}   # track_id -> "in"/"out"
        self._track_last_seen: dict[int, int] = {}   # track_id -> frame_number
        self._track_person_info: dict[int, dict] = {}  # track_id -> {person_id, age, gender}

        self._in_count = 0
        self._out_count = 0
        self._passerby_count = 0
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
        """Return +1 if point is on the left side, -1 if on the right, 0 if on the line."""
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

    def _get_zone(self, cx_ratio: float, cy_ratio: float) -> str:
        """Determine which zone a point (in ratio coords) belongs to."""
        sign = self._cross_sign(
            self._line_start, self._line_end, (cx_ratio, cy_ratio)
        )
        if sign == 0:
            # On the line — treat as same side as origin
            sign = self._origin_sign
        if sign == self._origin_sign:
            return self._origin_direction  # origin side
        else:
            return "out" if self._origin_direction == "in" else "in"

    def update(
        self,
        tracked_persons: List[TrackedPerson],
        frame_shape: Tuple[int, ...],
        frame_number: int,
        track_infos: Optional[Dict[int, dict]] = None,
    ) -> None:
        """
        Update zone tracking for current frame's tracked persons.

        Records first_zone on first appearance and continuously updates last_zone.
        Also caches person info (label, age, gender) so it's available when the
        track is lost (TrackManager may have already cleaned it up by then).
        """
        h, w = frame_shape[:2]

        with self._lock:
            for person in tracked_persons:
                track_id = person.track_id
                cx, cy = person.center
                cx_ratio = cx / w
                cy_ratio = cy / h

                zone = self._get_zone(cx_ratio, cy_ratio)

                if track_id not in self._track_first_zone:
                    self._track_first_zone[track_id] = zone

                self._track_last_zone[track_id] = zone
                self._track_last_seen[track_id] = frame_number

                if track_infos and track_id in track_infos:
                    self._track_person_info[track_id] = track_infos[track_id]

    def process_lost_tracks(
        self,
        active_ids: List[int],
        frame_number: int,
        max_age: int = 150,
    ) -> Tuple[List[CrossingEvent], List[PasserbyEvent]]:
        """
        Check for lost tracks and generate crossing / passerby events.

        A track is considered lost when it's not in active_ids AND hasn't been
        seen for more than max_age frames.

        Returns:
            Tuple of (crossing_events, passerby_events).
        """
        active_set: Set[int] = set(active_ids)
        crossings: List[CrossingEvent] = []
        passerby_events: List[PasserbyEvent] = []

        with self._lock:
            lost_ids = [
                tid
                for tid, last_frame in self._track_last_seen.items()
                if tid not in active_set
                and frame_number - last_frame > max_age
            ]

            for tid in lost_ids:
                first_zone = self._track_first_zone.get(tid)
                last_zone = self._track_last_zone.get(tid)
                info = self._track_person_info.get(tid, {})

                if first_zone and last_zone and first_zone != last_zone:
                    if first_zone == "out" and last_zone == "in":
                        direction = "in"
                        self._in_count += 1
                    else:
                        direction = "out"
                        self._out_count += 1
                    crossings.append(CrossingEvent(
                        track_id=tid,
                        direction=direction,
                        person_id=info.get("person_id", "Unknown"),
                        age=info.get("age"),
                        gender=info.get("gender"),
                    ))
                elif first_zone == "out" and last_zone == "out":
                    pid = info.get("person_id", "Unknown")
                    if pid == "Unknown" or pid.endswith("?"):
                        self._passerby_count += 1
                        passerby_events.append(PasserbyEvent(
                            track_id=tid,
                            person_id=pid,
                            age=info.get("age"),
                            gender=info.get("gender"),
                        ))

                # Cleanup state for this track
                self._track_first_zone.pop(tid, None)
                self._track_last_zone.pop(tid, None)
                self._track_last_seen.pop(tid, None)
                self._track_person_info.pop(tid, None)

        return crossings, passerby_events

    def get_tracks_in_zone(self, zone: str) -> Set[int]:
        """Return track IDs currently in the given zone."""
        with self._lock:
            return {tid for tid, z in self._track_last_zone.items() if z == zone}

    def get_counts(self) -> Tuple[int, int]:
        """Return (in_count, out_count) thread-safely."""
        with self._lock:
            return self._in_count, self._out_count

    def get_passerby_count(self) -> int:
        """Return passerby count thread-safely."""
        with self._lock:
            return self._passerby_count

    def get_line_points_px(
        self, frame_shape: Tuple[int, ...]
    ) -> Tuple[Tuple[int, int], Tuple[int, int]]:
        """Convert line from ratio coordinates to pixel coordinates."""
        h, w = frame_shape[:2]
        pt1 = (int(self._line_start[0] * w), int(self._line_start[1] * h))
        pt2 = (int(self._line_end[0] * w), int(self._line_end[1] * h))
        return pt1, pt2


@dataclass
class StrangerAlertEvent:
    """Fired when a stranger is detected in the IN zone."""
    track_id: int
    person_id: str = "Unknown"
    age: Optional[int] = None
    gender: Optional[str] = None
    alert_count: int = 1


@dataclass
class _StrangerAlertState:
    """Internal per-track state for stranger alert timing."""
    track_id: int
    last_alert_time: float
    alert_count: int = 0


class StrangerAlertManager:
    """
    Tracks strangers in the IN zone and fires periodic alerts.

    First alert fires immediately when a stranger enters the IN zone.
    Subsequent alerts fire every `alert_interval` seconds while the
    stranger remains in the zone and unrecognized.
    """

    def __init__(self, alert_interval: float = 10.0):
        self._alert_interval = alert_interval
        self._tracked_strangers: dict[int, _StrangerAlertState] = {}
        self._lock = threading.Lock()

    def update(self, stranger_in_zone: Dict[int, dict]) -> List[StrangerAlertEvent]:
        """
        Process current strangers in the IN zone and return alerts to fire.

        Args:
            stranger_in_zone: {track_id: {"person_id", "age", "gender"}} for
                              Unknown/uncertain persons currently in the IN zone.

        Returns:
            List of StrangerAlertEvent for new or repeated alerts.
        """
        now = time.time()
        alerts: List[StrangerAlertEvent] = []

        with self._lock:
            # Remove tracks no longer in the stranger set (left zone or recognized)
            stale = [tid for tid in self._tracked_strangers if tid not in stranger_in_zone]
            for tid in stale:
                del self._tracked_strangers[tid]

            for tid, info in stranger_in_zone.items():
                if tid not in self._tracked_strangers:
                    # New stranger — alert immediately
                    self._tracked_strangers[tid] = _StrangerAlertState(
                        track_id=tid, last_alert_time=now, alert_count=1,
                    )
                    alerts.append(StrangerAlertEvent(
                        track_id=tid,
                        person_id=info.get("person_id", "Unknown"),
                        age=info.get("age"),
                        gender=info.get("gender"),
                        alert_count=1,
                    ))
                else:
                    state = self._tracked_strangers[tid]
                    if now - state.last_alert_time >= self._alert_interval:
                        state.last_alert_time = now
                        state.alert_count += 1
                        alerts.append(StrangerAlertEvent(
                            track_id=tid,
                            person_id=info.get("person_id", "Unknown"),
                            age=info.get("age"),
                            gender=info.get("gender"),
                            alert_count=state.alert_count,
                        ))

        return alerts
