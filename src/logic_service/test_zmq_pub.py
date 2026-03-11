#!/usr/bin/env python3
"""
ZMQ test publisher — sends mock events to logic_service's ZMQ subscriber.

Usage:
    python3 test_zmq_pub.py [topic]

Topics:
    crossing   → CrossingEventPayload  (home_return_count)
    stranger   → StrangerAlertPayload  (unregistered_detection)
    passerby   → PasserbyEventPayload  (daily_passerby)
    animal     → AnimalAlertPayload    (creature_detection)
    all        → Send all 4 types

Examples:
    python3 test_zmq_pub.py crossing
    python3 test_zmq_pub.py all
"""

import json
import sys
import time

import zmq

ZMQ_PUB_ADDRESS = "tcp://*:5555"

# --- Mock payloads ---

def crossing_payload():
    return json.dumps({
        "timestamp": time.time(),
        "detections": [
            {
                "track_id": 1,
                "person_id": "田中太郎",
                "direction": "in",
                "age": 35,
                "gender": "M",
                "confidence": 0.92,
                "detection_result": "https://example.com/img/tanaka.jpg"
            }
        ]
    })

def stranger_payload():
    return json.dumps({
        "timestamp": time.time(),
        "detections": [
            {
                "track_id": 99,
                "person_id": "Unknown",
                "age": None,
                "gender": None,
                "alert_count": 1,
                "confidence": 0.88,
                "detection_result": "https://example.com/img/stranger.jpg"
            }
        ]
    })

def passerby_payload():
    return json.dumps({
        "timestamp": time.time(),
        "detections": [
            {
                "track_id": 50,
                "person_id": "Unknown",
                "age": 25,
                "gender": "F",
                "confidence": 0.75,
                "detection_result": "https://example.com/img/passerby.jpg"
            }
        ]
    })

def animal_payload():
    return json.dumps({
        "timestamp": time.time(),
        "detections": [
            {
                "track_id": 200,
                "class_id": 16,
                "class_name": "dog",
                "confidence": 0.97,
                "alert_count": 1,
                "detection_result": "https://example.com/img/dog.jpg"
            }
        ]
    })

TOPICS = {
    "crossing": (b"crossing_event", crossing_payload),
    "stranger": (b"stranger_alert", stranger_payload),
    "passerby": (b"passerby_event", passerby_payload),
    "animal":   (b"animal_alert",   animal_payload),
}

def main():
    topic_arg = sys.argv[1] if len(sys.argv) > 1 else "crossing"

    ctx = zmq.Context()
    socket = ctx.socket(zmq.PUB)
    socket.bind(ZMQ_PUB_ADDRESS)
    print(f"✅ ZMQ PUB bound to {ZMQ_PUB_ADDRESS}")

    # ZMQ PUB needs a moment for subscribers to connect
    print("⏳ Waiting 2s for subscriber to connect...")
    time.sleep(2)

    if topic_arg == "all":
        send_topics = list(TOPICS.keys())
    else:
        if topic_arg not in TOPICS:
            print(f"❌ Unknown topic: {topic_arg}")
            print(f"   Available: {', '.join(TOPICS.keys())}, all")
            sys.exit(1)
        send_topics = [topic_arg]

    for name in send_topics:
        topic_bytes, payload_fn = TOPICS[name]
        payload = payload_fn()
        socket.send_multipart([topic_bytes, payload.encode("utf-8")])
        print(f"📤 Sent [{name}] topic={topic_bytes.decode()}")
        print(f"   payload={payload[:200]}...")
        time.sleep(0.5)

    print(f"\n✅ Done! Check logic_service logs to verify it received the event(s).")
    socket.close()
    ctx.term()

if __name__ == "__main__":
    main()
