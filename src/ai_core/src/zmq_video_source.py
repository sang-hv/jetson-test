"""
ZMQ-based video source that receives JPEG frames from start-stream.py.

Drop-in replacement for cv2.VideoCapture — exposes the same read()/isOpened()/
release()/get() interface so Pipeline.run() works without changes.

Protocol (set by start-stream.py):
    [8-byte little-endian uint64 timestamp_ns][jpeg bytes]
    Transport: ZMQ PUB/SUB over IPC (ipc:///tmp/ai_frames.sock)
"""

from __future__ import annotations

import logging
import struct
import time
from typing import Optional, Tuple, Union

import cv2
import numpy as np
import zmq

logger = logging.getLogger(__name__)

HEADER_SIZE = 8  # 8-byte uint64 timestamp


class ZMQVideoSource:
    """
    Receives JPEG frames from a ZMQ PUB socket and decodes them into
    numpy arrays, matching the cv2.VideoCapture interface.

    Handles:
    - Configurable receive timeout (avoids infinite blocking)
    - Automatic socket reconnect after sustained timeouts
    - Escalating log warnings (not every missed frame)
    - Corrupted JPEG graceful fallback
    """

    def __init__(
        self,
        endpoint: str = "ipc:///tmp/ai_frames.sock",
        recv_timeout_ms: int = 2000,
        max_timeouts_before_reconnect: int = 10,
    ) -> None:
        self._endpoint = endpoint
        self._recv_timeout_ms = recv_timeout_ms
        self._max_timeouts_before_reconnect = max_timeouts_before_reconnect

        self._closed = False
        self._context: Optional[zmq.Context] = None
        self._socket: Optional[zmq.Socket] = None
        self._poller: Optional[zmq.Poller] = None

        self._last_frame_time: float = 0.0
        self._consecutive_timeouts: int = 0
        self._total_frames: int = 0
        self._total_decode_errors: int = 0
        self._total_reconnects: int = 0

        self._frame_width: int = 0
        self._frame_height: int = 0
        self._frame_fps: float = 0.0

        self._connect()

    def _connect(self) -> None:
        """Create ZMQ SUB socket and connect to publisher."""
        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.SUB)
        self._socket.setsockopt(zmq.SUBSCRIBE, b"")
        self._socket.setsockopt(zmq.RCVHWM, 2)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.connect(self._endpoint)

        self._poller = zmq.Poller()
        self._poller.register(self._socket, zmq.POLLIN)

        logger.info("ZMQVideoSource connected to %s", self._endpoint)

    def _reconnect(self) -> None:
        """Tear down and re-create the socket (handles publisher restart)."""
        self._total_reconnects += 1
        logger.warning(
            "ZMQVideoSource reconnecting (%d total) to %s",
            self._total_reconnects,
            self._endpoint,
        )
        self._teardown_socket()
        self._connect()
        self._consecutive_timeouts = 0

    def _teardown_socket(self) -> None:
        """Close socket and context without marking the source as closed."""
        try:
            if self._poller and self._socket:
                self._poller.unregister(self._socket)
        except Exception:
            pass
        try:
            if self._socket:
                self._socket.close(linger=0)
        except Exception:
            pass
        try:
            if self._context:
                self._context.term()
        except Exception:
            pass
        self._socket = None
        self._context = None
        self._poller = None

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        """
        Receive and decode one JPEG frame.

        Returns:
            (True, bgr_frame) on success.
            (False, None) on timeout, decode error, or closed source.
        """
        if self._closed or self._socket is None:
            return False, None

        try:
            events = self._poller.poll(timeout=self._recv_timeout_ms)
        except zmq.ZMQError as exc:
            logger.error("ZMQVideoSource poll error: %s", exc)
            return False, None

        if not events:
            self._consecutive_timeouts += 1
            self._log_timeout_warning()

            if self._consecutive_timeouts >= self._max_timeouts_before_reconnect:
                self._reconnect()

            return False, None

        try:
            msg = self._socket.recv(flags=zmq.NOBLOCK)
        except zmq.Again:
            return False, None
        except zmq.ZMQError as exc:
            logger.error("ZMQVideoSource recv error: %s", exc)
            return False, None

        if len(msg) <= HEADER_SIZE:
            logger.warning("ZMQVideoSource received runt message (%d bytes)", len(msg))
            return False, None

        jpeg_data = msg[HEADER_SIZE:]
        frame = cv2.imdecode(
            np.frombuffer(jpeg_data, dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )

        if frame is None:
            self._total_decode_errors += 1
            if self._total_decode_errors % 50 == 1:
                logger.warning(
                    "ZMQVideoSource JPEG decode failed (%d total errors)",
                    self._total_decode_errors,
                )
            return False, None

        self._consecutive_timeouts = 0
        self._total_frames += 1
        self._last_frame_time = time.monotonic()

        h, w = frame.shape[:2]
        if w != self._frame_width or h != self._frame_height:
            self._frame_width = w
            self._frame_height = h
            logger.info("ZMQVideoSource frame size: %dx%d", w, h)

        return True, frame

    def _log_timeout_warning(self) -> None:
        """Log warnings at escalating intervals: 1, 5, 10, then every 10."""
        n = self._consecutive_timeouts
        if n in (1, 5, 10) or n % 10 == 0:
            elapsed = ""
            if self._last_frame_time > 0:
                secs = time.monotonic() - self._last_frame_time
                elapsed = f" (last frame {secs:.1f}s ago)"
            logger.warning(
                "ZMQVideoSource: no frame for %d polls%s",
                n,
                elapsed,
            )

    def isOpened(self) -> bool:
        """Compatible with cv2.VideoCapture.isOpened()."""
        return not self._closed and self._socket is not None

    def release(self) -> None:
        """Compatible with cv2.VideoCapture.release()."""
        if self._closed:
            return
        self._closed = True
        self._teardown_socket()
        logger.info(
            "ZMQVideoSource released (frames=%d, decode_errors=%d, reconnects=%d)",
            self._total_frames,
            self._total_decode_errors,
            self._total_reconnects,
        )

    def get(self, prop_id: int) -> Union[int, float]:
        """
        Compatible with cv2.VideoCapture.get() for common properties.

        Returns cached values from the last decoded frame.
        """
        if prop_id == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._frame_width)
        if prop_id == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._frame_height)
        if prop_id == cv2.CAP_PROP_FPS:
            return self._frame_fps
        return 0.0
