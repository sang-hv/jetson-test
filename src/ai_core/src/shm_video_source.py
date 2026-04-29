"""
Shared-memory video source: reads raw BGR frames written by start-stream.py.

Protocol (must match setup-firstboot/scripts/start-stream.py):
  Header 64 bytes (little-endian):
    0-3:   magic b'MPAI'
    4-7:   version u32 = 1
    8-11:  width u32
    12-15: height u32
    16-19: stride u32 (bytes per row, typically width * 3 for BGR)
    20-23: format u32 (0 = BGR)
    24-31: seq u64 (incremented after each complete frame)
    32-35: active_slot u32 (0 or 1 — which buffer holds the latest frame)
  64+:   double buffer, each slot size = stride * height
"""

from __future__ import annotations

import logging
import mmap
import os
import struct
import time
from typing import Optional, Tuple, Union

import numpy as np

logger = logging.getLogger(__name__)

HEADER_SIZE = 64
MAGIC = b"MPAI"


class SharedMemoryVideoSource:
    """
    Drop-in replacement for cv2.VideoCapture — read()/isOpened()/release()/get().
    Attaches to file-backed mmap in /dev/shm created by start-stream.py (writer).
    """

    def __init__(
        self,
        shm_name: str = "/dev/shm/mini_pc_ai_frames.bin",
        recv_timeout_ms: int = 2000,
    ) -> None:
        self._shm_name = shm_name
        if self._shm_name.startswith("/dev/shm/"):
            self._shm_path = self._shm_name
        else:
            self._shm_path = f"/dev/shm/{self._shm_name.lstrip('/')}.bin"
        self._recv_timeout_ms = recv_timeout_ms
        self._closed = False
        self._shm = None
        self._shm_fd: Optional[int] = None
        self._last_seq: int = -1
        self._frame_width: int = 0
        self._frame_height: int = 0
        self._frame_fps: float = 0.0
        self._total_frames: int = 0
        self._last_connect_attempt: float = 0.0
        self._reconnect_interval_sec: float = 0.5
        if not self._connect():
            logger.warning(
                "SharedMemoryVideoSource initial attach failed (%s). "
                "Will keep retrying in read().",
                self._shm_name,
            )

    def _close_shm(self) -> None:
        if self._shm is not None:
            try:
                self._shm.close()
            except Exception:
                pass
            self._shm = None
        if self._shm_fd is not None:
            try:
                os.close(self._shm_fd)
            except Exception:
                pass
            self._shm_fd = None

    def _connect(self) -> bool:
        self._last_connect_attempt = time.monotonic()
        self._close_shm()
        try:
            self._shm_fd = os.open(self._shm_path, os.O_RDONLY)
            size = os.fstat(self._shm_fd).st_size
            self._shm = mmap.mmap(self._shm_fd, size, access=mmap.ACCESS_READ)
        except FileNotFoundError:
            self._shm = None
            self._shm_fd = None
            return False

        if len(self._shm) < HEADER_SIZE:
            logger.warning("Shared memory too small: %d", len(self._shm))
            self._close_shm()
            return False

        if bytes(self._shm[0:4]) != MAGIC:
            logger.warning(
                "SHM magic mismatch (got %r); writer may not have started yet",
                bytes(self._shm[0:4]),
            )

        logger.info(
            "SharedMemoryVideoSource attached: %s size=%d",
            self._shm_path,
            len(self._shm),
        )
        # Writer may restart and reset seq; allow new frames to be consumed.
        self._last_seq = -1
        return True

    def _maybe_reconnect(self) -> None:
        if self._closed:
            return
        now = time.monotonic()
        if now - self._last_connect_attempt < self._reconnect_interval_sec:
            return
        if self._connect():
            logger.info("SharedMemoryVideoSource reconnected to %s", self._shm_path)

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        """
        Wait up to recv_timeout_ms for a new frame (seq advances).

        Returns:
            (True, bgr_frame) on success.
            (False, None) on timeout or closed.
        """
        if self._closed:
            return False, None

        deadline = time.monotonic() + (self._recv_timeout_ms / 1000.0)
        while time.monotonic() < deadline:
            if self._shm is None:
                self._maybe_reconnect()
                time.sleep(0.001)
                continue

            try:
                seq = struct.unpack_from("<Q", self._shm, 24)[0]
            except Exception:
                # Writer may have restarted/unlinked while reading.
                self._close_shm()
                self._maybe_reconnect()
                time.sleep(0.001)
                continue

            if seq != self._last_seq and seq > 0:
                try:
                    w = struct.unpack_from("<I", self._shm, 8)[0]
                    h = struct.unpack_from("<I", self._shm, 12)[0]
                    stride = struct.unpack_from("<I", self._shm, 16)[0]
                    slot = struct.unpack_from("<I", self._shm, 32)[0]
                except Exception:
                    self._close_shm()
                    self._maybe_reconnect()
                    time.sleep(0.001)
                    continue
                if w <= 0 or h <= 0 or stride < w * 3:
                    time.sleep(0.001)
                    continue
                frame_bytes = stride * h
                slot_offset = HEADER_SIZE + (slot & 1) * frame_bytes
                if slot_offset + frame_bytes > len(self._shm):
                    logger.error("SHM frame out of bounds")
                    return False, None

                mv = memoryview(self._shm)[slot_offset : slot_offset + frame_bytes]
                raw = np.frombuffer(mv, dtype=np.uint8, count=frame_bytes)
                row_b = w * 3
                if stride == row_b:
                    frame = raw.reshape(h, w, 3).copy()
                else:
                    frame = np.empty((h, w, 3), dtype=np.uint8)
                    for i in range(h):
                        frame[i] = raw[i * stride : i * stride + row_b].reshape(1, w, 3)

                self._last_seq = seq
                self._total_frames += 1
                if w != self._frame_width or h != self._frame_height:
                    self._frame_width = w
                    self._frame_height = h
                    logger.info("SharedMemoryVideoSource frame size: %dx%d", w, h)
                return True, frame

            time.sleep(0.005)

        # No new frame within timeout; try reconnect to catch writer restart.
        self._maybe_reconnect()
        return False, None

    def isOpened(self) -> bool:
        # For SHM source we allow startup before writer exists; read() will auto-reconnect.
        return not self._closed

    def release(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._close_shm()
        logger.info(
            "SharedMemoryVideoSource released (frames=%d)",
            self._total_frames,
        )

    def get(self, prop_id: int) -> Union[int, float]:
        import cv2

        if prop_id == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._frame_width)
        if prop_id == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._frame_height)
        if prop_id == cv2.CAP_PROP_FPS:
            return self._frame_fps
        return 0.0
