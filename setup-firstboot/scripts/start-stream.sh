#!/bin/bash
set -e
# GStreamer stream for go2rtc — CSI camera IMX219 + echo-cancel mic
log() { echo "[stream] $*" >&2; }

export XDG_RUNTIME_DIR=/run/user/$(id -u)
export PULSE_SERVER=unix:${XDG_RUNTIME_DIR}/pulse/native

# Check echocancel audio
HAS_AUDIO=""
if pactl list short sources 2>/dev/null | grep -q "echocancel_source"; then
    HAS_AUDIO=1
    log "Audio: echocancel_source"
else
    log "No echocancel — video only"
fi

# Launch
if [ -n "$HAS_AUDIO" ]; then
    log "Starting video + audio"
    exec gst-launch-1.0 -eq \
        nvarguscamerasrc wbmode=1 ispdigitalgainrange="1 1" ! 'video/x-raw(memory:NVMM),width=1280,height=720,framerate=30/1' ! \
        nvvidconv ! 'video/x-raw,format=I420' ! \
        x264enc tune=zerolatency speed-preset=ultrafast bitrate=2000 key-int-max=30 ! \
        h264parse config-interval=-1 ! \
        queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 ! mux. \
        pulsesrc device=echocancel_source ! \
        queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 ! \
        audioconvert ! audioresample ! volume volume=3.0 ! \
        voaacenc bitrate=128000 ! aacparse ! mux. \
        mpegtsmux name=mux alignment=7 ! fdsink fd=1
else
    log "Starting video only"
    exec gst-launch-1.0 -eq \
        nvarguscamerasrc wbmode=1 ispdigitalgainrange="1 1" ! 'video/x-raw(memory:NVMM),width=1280,height=720,framerate=30/1' ! \
        nvvidconv ! 'video/x-raw,format=I420' ! \
        x264enc tune=zerolatency speed-preset=ultrafast bitrate=2000 key-int-max=30 ! \
        h264parse config-interval=-1 ! \
        queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 ! \
        mpegtsmux alignment=7 ! fdsink fd=1
fi