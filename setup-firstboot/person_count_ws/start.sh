#!/bin/bash
# Wrapper for person-count WebSocket service (ZMQ → WS on port 8090)

exec python3 /opt/person_count_ws/server.py --port 8090
