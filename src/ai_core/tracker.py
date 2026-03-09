"""
Track state management with temporal smoothing and rate limiting.

Manages recognition state for each tracked person, implementing:
- Rate-limited recognition (don't recognize every frame)
- Temporal smoothing (require consistent labels before confirmation)
- Label persistence (keep last known label when face not detected)
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class TrackState:
    """
    State for a single tracked person.

    Maintains recognition history and handles temporal smoothing
    to prevent label flickering.
    """

    track_id: int
    last_recognition_time: float = 0.0
    recognition_history: List[str] = field(default_factory=list)
    recognition_scores: List[float] = field(default_factory=list)
    confirmed_label: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    # Mask detection history
    mask_history: List[float] = field(default_factory=list)  # Probability values
    confirmed_mask: Optional[bool] = None  # True=masked, False=no mask, None=unknown

    # Age/Gender history (temporal smoothing)
    age_history: List[int] = field(default_factory=list)
    gender_history: List[int] = field(default_factory=list)
    confirmed_age: Optional[int] = None
    confirmed_gender: Optional[int] = None  # 0=Female, 1=Male

    # Helmet detection history
    helmet_history: List[float] = field(default_factory=list)  # Probability values
    confirmed_helmet: Optional[bool] = None  # True=wearing, False=not wearing, None=unknown

    # Glove detection history
    glove_history: List[float] = field(default_factory=list)  # Probability values
    confirmed_glove: Optional[bool] = None  # True=wearing, False=not wearing, None=unknown

    def needs_recognition(self, interval_ms: float) -> bool:
        """
        Check if this track needs face recognition based on interval.

        Args:
            interval_ms: Minimum interval between recognitions in milliseconds

        Returns:
            True if enough time has passed since last recognition
        """
        if self.last_recognition_time == 0.0:
            return True  # Never recognized, do it now

        current_time = time.time()
        elapsed_ms = (current_time - self.last_recognition_time) * 1000
        return elapsed_ms >= interval_ms

    def update_recognition(
        self,
        label: str,
        score: float,
        min_confirm_frames: int,
    ) -> None:
        """
        Update recognition history and determine confirmed label.

        Uses weighted vote: each recognition result contributes its similarity
        score as a vote weight. "Unknown" results always carry score=0.0 so
        they never influence the vote — a confirmed identity is never overwritten
        by occlusion or bad angles.

        Confirmation requires the winning label to accumulate at least
        min_confirm_frames individual votes (regardless of their weights).

        Args:
            label: Recognition result ("Unknown" or person name)
            score: Similarity score (0.0-1.0); always 0.0 for "Unknown"
            min_confirm_frames: Minimum number of non-Unknown recognitions
                                needed to confirm a label
        """
        self.last_recognition_time = time.time()
        self.last_seen = time.time()
        self.recognition_history.append(label)
        self.recognition_scores.append(score)

        # Keep only recent history (3x min_confirm_frames)
        max_history = min_confirm_frames * 3
        if len(self.recognition_history) > max_history:
            self.recognition_history = self.recognition_history[-max_history:]
            self.recognition_scores = self.recognition_scores[-max_history:]

        # Weighted vote — accumulate similarity scores per label.
        # "Unknown" has score=0.0 so it contributes nothing and can never win.
        vote_weights: dict[str, float] = {}
        vote_counts: dict[str, int] = {}
        for lbl, sc in zip(self.recognition_history, self.recognition_scores):
            if lbl != "Unknown" and sc > 0:
                vote_weights[lbl] = vote_weights.get(lbl, 0.0) + sc
                vote_counts[lbl] = vote_counts.get(lbl, 0) + 1

        if not vote_weights:
            # All frames so far are Unknown — keep confirmed_label unchanged
            # (protects a previously confirmed identity from being wiped)
            return

        best_label = max(vote_weights, key=vote_weights.__getitem__)

        # Require min_confirm_frames individual hits before locking in
        if vote_counts[best_label] >= min_confirm_frames:
            self.confirmed_label = best_label

    def get_display_label(self) -> str:
        """
        Get the label to display for this track.

        Priority:
        1. Confirmed label (after min_confirm_frames consistency)
        2. Most common recent label with "?" suffix (uncertain)
        3. "Unknown" (no recognition history)

        Returns:
            Label string for display
        """
        if self.confirmed_label:
            return self.confirmed_label
        elif self.recognition_history:
            # Return most common recent label with "?" suffix to indicate uncertainty
            recent = self.recognition_history[-5:]  # Last 5 recognitions
            counter = Counter(recent)
            most_common = counter.most_common(1)[0][0]
            # Add "?" if not confirmed yet
            if most_common != "Unknown":
                return f"{most_common}?"
            return most_common
        return "Unknown"

    def get_average_score(self, window: int = 5) -> float:
        """Get average recognition score from recent history."""
        if not self.recognition_scores:
            return 0.0
        recent = self.recognition_scores[-window:]
        return sum(recent) / len(recent)

    def update_mask(self, mask_probability: float) -> None:
        """
        Update mask detection history.

        Args:
            mask_probability: Probability of wearing mask (0.0-1.0)
        """
        self.mask_history.append(mask_probability)

        # Keep only last 5 mask values
        if len(self.mask_history) > 5:
            self.mask_history = self.mask_history[-5:]

        # Update confirmed mask status (temporal smoothing)
        if len(self.mask_history) >= 2:
            avg_prob = sum(self.mask_history[-3:]) / min(len(self.mask_history), 3)
            self.confirmed_mask = avg_prob > 0.5

    def get_mask_status(self) -> Optional[bool]:
        """
        Get confirmed mask status (smoothed).

        Returns:
            True if wearing mask, False if not, None if uncertain
        """
        if len(self.mask_history) < 2:
            return None
        return self.confirmed_mask

    def update_age_gender(self, age: Optional[int], gender: Optional[int]) -> None:
        """
        Update age/gender history with temporal smoothing.

        Args:
            age: Detected age (0-100) or None
            gender: Detected gender (0=Female, 1=Male) or None
        """
        if age is not None:
            self.age_history.append(age)
            if len(self.age_history) > 5:
                self.age_history = self.age_history[-5:]
            # Smoothed age = median of last 3 values
            if len(self.age_history) >= 2:
                recent = self.age_history[-3:]
                self.confirmed_age = int(sorted(recent)[len(recent) // 2])

        if gender is not None:
            self.gender_history.append(gender)
            if len(self.gender_history) > 5:
                self.gender_history = self.gender_history[-5:]
            # Smoothed gender = mode of last 3 values
            if len(self.gender_history) >= 2:
                recent = self.gender_history[-3:]
                self.confirmed_gender = max(set(recent), key=recent.count)

    def get_age_gender(self) -> Tuple[Optional[int], Optional[str]]:
        """
        Get confirmed age and gender string.

        Returns:
            Tuple of (age, gender_str) where:
            - age: Confirmed age or None if uncertain
            - gender_str: "M" or "F" or None if uncertain
        """
        gender_str = None
        if self.confirmed_gender is not None:
            gender_str = "M" if self.confirmed_gender == 1 else "F"
        return self.confirmed_age, gender_str

    def update_helmet(self, helmet_probability: float) -> None:
        """
        Update helmet detection history.

        Args:
            helmet_probability: Probability of wearing helmet (0.0-1.0)
        """
        self.helmet_history.append(helmet_probability)

        # Keep only last 5 helmet values
        if len(self.helmet_history) > 5:
            self.helmet_history = self.helmet_history[-5:]

        # Update confirmed helmet status (temporal smoothing)
        if len(self.helmet_history) >= 2:
            avg_prob = sum(self.helmet_history[-3:]) / min(len(self.helmet_history), 3)
            self.confirmed_helmet = avg_prob > 0.5

    def get_helmet_status(self) -> Optional[bool]:
        """
        Get confirmed helmet status (smoothed).

        Returns:
            True if wearing helmet, False if not, None if uncertain
        """
        if len(self.helmet_history) < 2:
            return None
        return self.confirmed_helmet

    def update_glove(self, glove_probability: float) -> None:
        """
        Update glove detection history.

        Args:
            glove_probability: Probability of wearing glove (0.0-1.0)
        """
        self.glove_history.append(glove_probability)

        # Keep only last 5 glove values
        if len(self.glove_history) > 5:
            self.glove_history = self.glove_history[-5:]

        # Update confirmed glove status (temporal smoothing)
        if len(self.glove_history) >= 2:
            avg_prob = sum(self.glove_history[-3:]) / min(len(self.glove_history), 3)
            self.confirmed_glove = avg_prob > 0.5

    def get_glove_status(self) -> Optional[bool]:
        """
        Get confirmed glove status (smoothed).

        Returns:
            True if wearing glove, False if not, None if uncertain
        """
        if len(self.glove_history) < 2:
            return None
        return self.confirmed_glove

    def mark_seen(self) -> None:
        """Update last_seen timestamp (call when track is detected)."""
        self.last_seen = time.time()


class TrackManager:
    """
    Manages track states for all tracked persons.

    Handles:
    - Creating/retrieving track states
    - Rate-limiting recognition per track
    - Temporal smoothing of labels
    - Cleanup of stale tracks
    """

    def __init__(
        self,
        recognize_interval_ms: float = 500.0,
        min_confirm_frames: int = 3,
        track_timeout_seconds: float = 5.0,
    ):
        """
        Initialize track manager.

        Args:
            recognize_interval_ms: Minimum interval between recognitions per track.
                Higher values reduce CPU load but increase response latency.
                Recommended: 300-1000ms depending on hardware.
            min_confirm_frames: Number of consistent recognition results needed
                to confirm identity. Higher values are more stable but slower
                to confirm. Recommended: 3-5.
            track_timeout_seconds: Time after which to remove tracks that haven't
                been seen. Should be longer than typical occlusion duration.
        """
        self.recognize_interval_ms = recognize_interval_ms
        self.min_confirm_frames = min_confirm_frames
        self.track_timeout_seconds = track_timeout_seconds
        self.tracks: Dict[int, TrackState] = {}

        # Thread safety lock for multi-threaded access
        self._lock = threading.Lock()

    def get_or_create_track(self, track_id: int) -> TrackState:
        """
        Get existing track state or create new one.

        Thread-safe.

        Args:
            track_id: Track ID from detector

        Returns:
            TrackState for the given track ID
        """
        with self._lock:
            if track_id not in self.tracks:
                self.tracks[track_id] = TrackState(track_id=track_id)
            return self.tracks[track_id]

    def should_recognize(self, track_id: int) -> bool:
        """
        Check if track needs face recognition based on interval.

        Thread-safe.

        Args:
            track_id: Track ID to check

        Returns:
            True if recognition should be performed for this track
        """
        track = self.get_or_create_track(track_id)
        return track.needs_recognition(self.recognize_interval_ms)

    def mark_recognition_submitted(self, track_id: int) -> None:
        """
        Mark that a recognition task was submitted for this track.

        Updates last_recognition_time to prevent duplicate submissions.
        Thread-safe.

        Args:
            track_id: Track ID that was submitted for recognition
        """
        with self._lock:
            if track_id in self.tracks:
                self.tracks[track_id].last_recognition_time = time.time()

    def update_recognition(
        self,
        track_id: int,
        label: str,
        score: float,
        mask_probability: Optional[float] = None,
        age: Optional[int] = None,
        gender: Optional[int] = None,
        helmet_probability: Optional[float] = None,
        glove_probability: Optional[float] = None,
    ) -> None:
        """
        Update track with new recognition result.

        Thread-safe - can be called from recognition worker thread.

        Args:
            track_id: Track ID that was recognized
            label: Recognition result ("Unknown" or person name)
            score: Similarity score
            mask_probability: Optional mask probability (0.0-1.0)
            age: Optional detected age (0-100)
            gender: Optional detected gender (0=Female, 1=Male)
            helmet_probability: Optional helmet probability (0.0-1.0)
            glove_probability: Optional glove probability (0.0-1.0)
        """
        with self._lock:
            if track_id not in self.tracks:
                self.tracks[track_id] = TrackState(track_id=track_id)
            self.tracks[track_id].update_recognition(label, score, self.min_confirm_frames)

            # Update mask status if provided
            if mask_probability is not None:
                self.tracks[track_id].update_mask(mask_probability)

            # Update age/gender if provided
            if age is not None or gender is not None:
                self.tracks[track_id].update_age_gender(age, gender)

            # Update helmet status if provided
            if helmet_probability is not None:
                self.tracks[track_id].update_helmet(helmet_probability)

            # Update glove status if provided
            if glove_probability is not None:
                self.tracks[track_id].update_glove(glove_probability)

    def get_label(self, track_id: int) -> str:
        """
        Get display label for track.

        Returns the confirmed label if available, otherwise the most
        likely label with uncertainty marker.
        Thread-safe.

        Args:
            track_id: Track ID to get label for

        Returns:
            Label string for display
        """
        with self._lock:
            if track_id in self.tracks:
                return self.tracks[track_id].get_display_label()
            return "Unknown"

    def get_mask_status(self, track_id: int) -> Optional[bool]:
        """
        Get mask status for track.

        Thread-safe.

        Args:
            track_id: Track ID to get mask status for

        Returns:
            True if wearing mask, False if not, None if uncertain
        """
        with self._lock:
            if track_id in self.tracks:
                return self.tracks[track_id].get_mask_status()
            return None

    def get_age_gender(self, track_id: int) -> Tuple[Optional[int], Optional[str]]:
        """
        Get age and gender for track.

        Thread-safe.

        Args:
            track_id: Track ID to get age/gender for

        Returns:
            Tuple of (age, gender_str) where:
            - age: Confirmed age or None if uncertain
            - gender_str: "M" or "F" or None if uncertain
        """
        with self._lock:
            if track_id in self.tracks:
                return self.tracks[track_id].get_age_gender()
            return None, None

    def get_helmet_status(self, track_id: int) -> Optional[bool]:
        """
        Get helmet status for track.

        Thread-safe.

        Args:
            track_id: Track ID to get helmet status for

        Returns:
            True if wearing helmet, False if not, None if uncertain
        """
        with self._lock:
            if track_id in self.tracks:
                return self.tracks[track_id].get_helmet_status()
            return None

    def get_glove_status(self, track_id: int) -> Optional[bool]:
        """
        Get glove status for track.

        Thread-safe.

        Args:
            track_id: Track ID to get glove status for

        Returns:
            True if wearing glove, False if not, None if uncertain
        """
        with self._lock:
            if track_id in self.tracks:
                return self.tracks[track_id].get_glove_status()
            return None

    def mark_track_seen(self, track_id: int) -> None:
        """
        Mark a track as seen (updates last_seen timestamp).

        Call this for every detected track each frame to prevent
        premature cleanup.
        Thread-safe.

        Args:
            track_id: Track ID that was detected
        """
        with self._lock:
            if track_id in self.tracks:
                self.tracks[track_id].mark_seen()
            else:
                # Create new track
                self.tracks[track_id] = TrackState(track_id=track_id)

    def cleanup_stale_tracks(self, active_track_ids: List[int]) -> int:
        """
        Remove tracks that are no longer active.

        Thread-safe.

        Args:
            active_track_ids: List of track IDs currently detected

        Returns:
            Number of tracks removed
        """
        with self._lock:
            current_time = time.time()
            stale_ids = []

            for track_id, track in self.tracks.items():
                if track_id not in active_track_ids:
                    # Track not currently detected
                    if (current_time - track.last_seen) > self.track_timeout_seconds:
                        stale_ids.append(track_id)

            for track_id in stale_ids:
                del self.tracks[track_id]

            return len(stale_ids)

    def get_track_info(self, track_id: int) -> Optional[dict]:
        """
        Get detailed info about a track.

        Thread-safe.

        Args:
            track_id: Track ID to query

        Returns:
            Dict with track details or None if not found
        """
        with self._lock:
            if track_id not in self.tracks:
                return None

            track = self.tracks[track_id]
            return {
                "track_id": track.track_id,
                "confirmed_label": track.confirmed_label,
                "display_label": track.get_display_label(),
                "history_length": len(track.recognition_history),
                "avg_score": track.get_average_score(),
                "age_seconds": time.time() - track.created_at,
            }

    @property
    def active_track_count(self) -> int:
        """Number of active tracks being managed. Thread-safe."""
        with self._lock:
            return len(self.tracks)

    @property
    def confirmed_track_count(self) -> int:
        """Number of tracks with confirmed labels. Thread-safe."""
        with self._lock:
            return sum(1 for t in self.tracks.values() if t.confirmed_label is not None)

    def reset(self) -> None:
        """Reset all track states. Thread-safe."""
        with self._lock:
            self.tracks.clear()

    def is_priority_track(self, track_id: int) -> bool:
        """
        Check if track should be prioritized for recognition.

        Priority tracks are:
        - New tracks (not in self.tracks yet)
        - Tracks without confirmed label

        Thread-safe.

        Args:
            track_id: Track ID to check

        Returns:
            True if track should have high priority (new or unconfirmed)
        """
        with self._lock:
            if track_id not in self.tracks:
                return True  # New track = high priority
            track = self.tracks[track_id]
            return track.confirmed_label is None  # Unconfirmed = high priority
