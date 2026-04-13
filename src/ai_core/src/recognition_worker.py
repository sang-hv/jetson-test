"""
Background worker thread for face recognition.

Separates face recognition from the main detection loop to prevent
blocking when multiple faces need to be recognized simultaneously.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

import numpy as np

if TYPE_CHECKING:
    from .mask_detector import MaskDetector
    from .ppe_detector import ProtectiveEquipmentDetector
    from .recognizer import FaceRecognizer
    from .tracker import TrackManager


@dataclass(order=True)
class RecognitionTask:
    """
    Task to be processed by recognition worker.

    Uses priority ordering for PriorityQueue:
    - priority=0: High priority (new/unconfirmed tracks)
    - priority=1: Low priority (confirmed tracks)
    """

    priority: int  # 0 = high (new track), 1 = low (confirmed track)
    track_id: int = field(compare=False)
    crop: np.ndarray = field(compare=False)  # Person crop image (BGR)
    timestamp: float = field(compare=False)  # Time when task was created


class RecognitionWorker:
    """
    Background thread for face recognition.

    Processes recognition tasks from a queue, allowing the main thread
    to continue detection without waiting for face recognition to complete.

    Features:
    - Non-blocking task submission
    - Queue size limit to prevent memory issues
    - Graceful shutdown
    - Thread-safe result updates via TrackManager

    Example:
        worker = RecognitionWorker(recognizer, track_manager, threshold=0.45)
        worker.start()

        # In main loop:
        task = RecognitionTask(track_id=1, crop=person_crop, timestamp=time.time())
        worker.submit(task)  # Non-blocking

        # When done:
        worker.stop()
    """

    def __init__(
        self,
        recognizer: FaceRecognizer,
        track_manager: TrackManager,
        threshold: float = 0.45,
        max_queue_size: int = 10,
        mask_detector: Optional[MaskDetector] = None,
        ppe_detector: Optional[ProtectiveEquipmentDetector] = None,
    ):
        """
        Initialize recognition worker.

        Args:
            recognizer: FaceRecognizer instance for face detection and matching
            track_manager: TrackManager for updating recognition results
            threshold: Similarity threshold for face matching
            max_queue_size: Maximum number of pending tasks. Tasks submitted
                           when queue is full will be dropped.
            mask_detector: Optional MaskDetector for mask detection
            ppe_detector: Optional ProtectiveEquipmentDetector for helmet/glove detection
        """
        self.recognizer = recognizer
        self.track_manager = track_manager
        self.threshold = threshold
        self.max_queue_size = max_queue_size
        self.mask_detector = mask_detector
        self.ppe_detector = ppe_detector

        # Priority queue with size limit (lower priority number = higher priority)
        self.task_queue: queue.PriorityQueue = queue.PriorityQueue(maxsize=max_queue_size)

        # Control flags
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Statistics
        self._tasks_processed = 0
        self._tasks_dropped = 0

    def start(self) -> None:
        """Start the worker thread."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="RecognitionWorker",
            daemon=True,
        )
        self._thread.start()
        print("[RecognitionWorker] Started")

    def stop(self) -> None:
        """Stop the worker thread gracefully."""
        if not self._running:
            return

        self._running = False

        # Put sentinel to unblock queue.get()
        try:
            self.task_queue.put_nowait(None)
        except queue.Full:
            pass

        # Wait for thread to finish
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                print("[RecognitionWorker] Warning: Thread did not stop cleanly")

        print(
            f"[RecognitionWorker] Stopped. "
            f"Processed: {self._tasks_processed}, Dropped: {self._tasks_dropped}"
        )

    def submit(self, task: RecognitionTask) -> bool:
        """
        Submit a recognition task to the queue.

        Non-blocking operation. If the queue is full, the task is dropped
        to prevent memory buildup and maintain real-time performance.

        Args:
            task: RecognitionTask containing track_id, crop, and timestamp

        Returns:
            True if task was queued, False if dropped (queue full)
        """
        if not self._running:
            return False

        try:
            self.task_queue.put_nowait(task)
            return True
        except queue.Full:
            self._tasks_dropped += 1
            return False

    def _worker_loop(self) -> None:
        """Main worker loop - runs in background thread."""
        while self._running:
            try:
                # Wait for task with timeout to allow checking _running flag
                task = self.task_queue.get(timeout=0.1)

                if task is None:  # Sentinel for shutdown
                    break

                self._process_task(task)
                self._tasks_processed += 1

            except queue.Empty:
                continue
            except Exception as e:
                print(f"[RecognitionWorker] Error processing task: {e}")

    def _process_task(self, task: RecognitionTask) -> None:
        """
        Process a single recognition task.

        Runs face recognition on the crop and updates the track manager
        with the result. Also runs mask detection and PPE detection if enabled.
        """
        # Skip if task is too old (stale crop)
        age_ms = (time.time() - task.timestamp) * 1000
        if age_ms > 2000:  # Skip tasks older than 2 seconds
            return

        # Run face recognition
        match = self.recognizer.recognize_in_crop(
            task.crop,
            threshold=self.threshold,
        )

        # Run mask detection on full person crop — mask YOLO detects faces itself
        mask_probability = None
        if self.mask_detector is not None and self.mask_detector.is_enabled:
            mask_probability = self.mask_detector.get_mask_probability(task.crop)

        # Run PPE detection on full person crop (helmet/glove)
        helmet_probability = None
        glove_probability = None
        if self.ppe_detector is not None and self.ppe_detector.is_enabled:
            helmet_probability, glove_probability = self.ppe_detector.get_probabilities(task.crop)

        if match is not None:
            # Update track manager (thread-safe via lock)
            self.track_manager.update_recognition(
                task.track_id,
                match.label,
                match.similarity,
                mask_probability=mask_probability,
                age=match.age,
                gender=match.gender,
                helmet_probability=helmet_probability,
                glove_probability=glove_probability,
            )
        elif mask_probability is not None or helmet_probability is not None:
            # No face match but still update detection status
            self.track_manager.update_recognition(
                task.track_id,
                "Unknown",
                0.0,
                mask_probability=mask_probability,
                helmet_probability=helmet_probability,
                glove_probability=glove_probability,
            )

    @property
    def queue_size(self) -> int:
        """Current number of pending tasks in queue."""
        return self.task_queue.qsize()

    @property
    def is_running(self) -> bool:
        """Check if worker thread is running."""
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def stats(self) -> dict:
        """Get worker statistics."""
        return {
            "running": self.is_running,
            "queue_size": self.queue_size,
            "max_queue_size": self.max_queue_size,
            "tasks_processed": self._tasks_processed,
            "tasks_dropped": self._tasks_dropped,
        }
