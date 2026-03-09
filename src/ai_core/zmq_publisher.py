"""
ZeroMQ PUB socket publisher for broadcasting crossing events to Logic Service.

Fire-and-forget: if no subscriber is connected, messages are dropped silently.
"""

from __future__ import annotations

import json
import logging

import zmq

logger = logging.getLogger(__name__)


class ZMQPublisher:
    """
    Publishes detection events over a ZMQ PUB socket.

    Usage:
        publisher = ZMQPublisher(port=5555)
        publisher.send_detection({"timestamp": ..., "detections": [...]})
        publisher.close()
    """

    TOPIC = b"crossing_event"

    def __init__(self, port: int = 5555) -> None:
        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.PUB)
        self._socket.bind(f"tcp://*:{port}")
        # Small HWM so slow/missing subscribers don't accumulate memory
        self._socket.setsockopt(zmq.SNDHWM, 100)
        logger.info(f"ZMQPublisher bound to tcp://*:{port}")

    def send_detection(self, data: dict) -> None:
        """
        Serialize data as JSON and publish with the crossing_event topic prefix.

        Non-blocking: if the HWM is reached the message is dropped silently.
        """
        try:
            payload = json.dumps(data, ensure_ascii=False).encode()
            self._socket.send_multipart([self.TOPIC, payload], flags=zmq.NOBLOCK)
        except zmq.Again:
            pass  # No subscriber or HWM reached — safe to drop
        except Exception as exc:
            logger.warning(f"ZMQPublisher send failed: {exc}")

    def close(self) -> None:
        """Release ZMQ resources."""
        try:
            self._socket.close(linger=0)
            self._context.term()
        except Exception:
            pass
