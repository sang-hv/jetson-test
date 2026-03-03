#!/bin/bash
# Wrapper script for backchannel service
# Resolves PulseAudio session access dynamically

export XDG_RUNTIME_DIR=/run/user/$(id -u)
export PULSE_SERVER=unix:${XDG_RUNTIME_DIR}/pulse/native

exec python3 /opt/backchannel/server.py --port 8080
