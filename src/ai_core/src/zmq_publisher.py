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
    STRANGER_ALERT_TOPIC = b"stranger_alert"
    PASSERBY_TOPIC = b"passerby_event"
    ANIMAL_ALERT_TOPIC = b"animal_alert"
    PERSON_COUNT_TOPIC = b"person_count"

    # shop topics
    ZONE_ENTRY_TOPIC = b"zone_entry"
    ZONE_EXIT_TOPIC = b"zone_exit"

    # enterprise topics
    EMPLOYEE_CROSSING_TOPIC = b"employee_crossing"

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

    def send_stranger_alert(self, data: dict) -> None:
        """Serialize data as JSON and publish with the stranger_alert topic prefix."""
        try:
            payload = json.dumps(data, ensure_ascii=False).encode()
            self._socket.send_multipart([self.STRANGER_ALERT_TOPIC, payload], flags=zmq.NOBLOCK)
        except zmq.Again:
            pass
        except Exception as exc:
            logger.warning(f"ZMQPublisher stranger alert send failed: {exc}")

    def send_passerby_event(self, data: dict) -> None:
        """Serialize data as JSON and publish with the passerby_event topic prefix."""
        try:
            payload = json.dumps(data, ensure_ascii=False).encode()
            self._socket.send_multipart([self.PASSERBY_TOPIC, payload], flags=zmq.NOBLOCK)
        except zmq.Again:
            pass
        except Exception as exc:
            logger.warning(f"ZMQPublisher passerby event send failed: {exc}")

    def send_animal_alert(self, data: dict) -> None:
        """Serialize data as JSON and publish with the animal_alert topic prefix."""
        try:
            payload = json.dumps(data, ensure_ascii=False).encode()
            self._socket.send_multipart([self.ANIMAL_ALERT_TOPIC, payload], flags=zmq.NOBLOCK)
        except zmq.Again:
            pass
        except Exception as exc:
            logger.warning(f"ZMQPublisher animal alert send failed: {exc}")

    def send_person_count(self, data: dict) -> None:
        """Serialize data as JSON and publish with the person_count topic prefix."""
        try:
            payload = json.dumps(data, ensure_ascii=False).encode()
            self._socket.send_multipart([self.PERSON_COUNT_TOPIC, payload], flags=zmq.NOBLOCK)
        except zmq.Again:
            pass
        except Exception as exc:
            logger.warning(f"ZMQPublisher person count send failed: {exc}")

    def send_zone_entry(self, data: dict) -> None:
        """Serialize data as JSON and publish with the zone_entry topic prefix."""
        try:
            payload = json.dumps(data, ensure_ascii=False).encode()
            self._socket.send_multipart([self.ZONE_ENTRY_TOPIC, payload], flags=zmq.NOBLOCK)
        except zmq.Again:
            pass
        except Exception as exc:
            logger.warning(f"ZMQPublisher zone entry send failed: {exc}")

    def send_zone_exit(self, data: dict) -> None:
        """Serialize data as JSON and publish with the zone_exit topic prefix."""
        try:
            payload = json.dumps(data, ensure_ascii=False).encode()
            self._socket.send_multipart([self.ZONE_EXIT_TOPIC, payload], flags=zmq.NOBLOCK)
        except zmq.Again:
            pass
        except Exception as exc:
            logger.warning(f"ZMQPublisher zone exit send failed: {exc}")

    def send_employee_crossing(self, data: dict) -> None:
        """Serialize data as JSON and publish with the employee_crossing topic prefix."""
        try:
            payload = json.dumps(data, ensure_ascii=False).encode()
            self._socket.send_multipart([self.EMPLOYEE_CROSSING_TOPIC, payload], flags=zmq.NOBLOCK)
        except zmq.Again:
            pass
        except Exception as exc:
            logger.warning(f"ZMQPublisher employee crossing send failed: {exc}")

    def close(self) -> None:
        """Release ZMQ resources."""
        try:
            self._socket.close(linger=0)
            self._context.term()
        except Exception:
            pass
