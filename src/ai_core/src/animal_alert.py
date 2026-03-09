"""
Animal alert manager for periodic notifications when animals are detected.

Tracks animals in the frame and fires periodic alerts while they remain visible.
First alert fires immediately upon detection, subsequent alerts fire every
`alert_interval` seconds.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, List

from .detector import TrackedAnimal


@dataclass
class AnimalAlertEvent:
    """Fired when an animal alert should be sent."""
    track_id: int
    class_id: int
    class_name: str
    confidence: float
    alert_count: int = 1


@dataclass
class _AnimalAlertState:
    """Internal state for a tracked animal."""
    track_id: int
    class_id: int
    class_name: str
    last_alert_time: float
    alert_count: int = 0


class AnimalAlertManager:
    """
    Tracks animals in the frame and fires periodic alerts.

    First alert fires immediately when a new animal appears.
    Subsequent alerts fire every `alert_interval` seconds while the
    animal remains in the frame.
    """

    def __init__(self, alert_interval: float = 10.0):
        self._alert_interval = alert_interval
        self._tracked_animals: Dict[int, _AnimalAlertState] = {}
        self._lock = threading.Lock()

    def update(self, detected_animals: List[TrackedAnimal]) -> List[AnimalAlertEvent]:
        """
        Process currently detected animals and return alerts to fire.

        Args:
            detected_animals: List of TrackedAnimal from the current frame.

        Returns:
            List of AnimalAlertEvent for new or repeated alerts.
        """
        now = time.time()
        alerts: List[AnimalAlertEvent] = []
        current_ids = {a.track_id for a in detected_animals}

        with self._lock:
            # Remove tracks no longer detected
            stale = [tid for tid in self._tracked_animals if tid not in current_ids]
            for tid in stale:
                del self._tracked_animals[tid]

            for animal in detected_animals:
                tid = animal.track_id
                if tid not in self._tracked_animals:
                    # New animal — alert immediately
                    self._tracked_animals[tid] = _AnimalAlertState(
                        track_id=tid,
                        class_id=animal.class_id,
                        class_name=animal.class_name,
                        last_alert_time=now,
                        alert_count=1,
                    )
                    alerts.append(AnimalAlertEvent(
                        track_id=tid,
                        class_id=animal.class_id,
                        class_name=animal.class_name,
                        confidence=animal.confidence,
                        alert_count=1,
                    ))
                else:
                    state = self._tracked_animals[tid]
                    if now - state.last_alert_time >= self._alert_interval:
                        state.last_alert_time = now
                        state.alert_count += 1
                        alerts.append(AnimalAlertEvent(
                            track_id=tid,
                            class_id=animal.class_id,
                            class_name=animal.class_name,
                            confidence=animal.confidence,
                            alert_count=state.alert_count,
                        ))

        return alerts
