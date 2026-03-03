#!/bin/bash
# GStreamer stream script for go2rtc (exec source)
# Default: video + audio (PulseAudio echocancel)
# Fallback: video-only if no mic

log() { echo "[start-stream] $*" >&2; }

# PulseAudio session
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export PULSE_SERVER=unix:${XDG_RUNTIME_DIR}/pulse/native

# --- Detect video encoder ---
if gst-inspect-1.0 nvv4l2h264enc >/dev/null 2>&1; then
    VIDEO_ENC="nvv4l2h264enc preset-level=1 bitrate=2000000 control-rate=1 ! h264parse"
else
    VIDEO_ENC="x264enc tune=zerolatency speed-preset=ultrafast bitrate=2000 key-int-max=30 ! h264parse"
fi
log "Encoder: $VIDEO_ENC"

# --- Detect camera ---
VIDEO_DEV=""
for dev in /dev/video*; do
    [ -e "$dev" ] || continue
    if v4l2-ctl -d "$dev" --all 2>/dev/null | grep -q "Video Capture"; then
        VIDEO_DEV="$dev"
        break
    fi
done

if [ -z "$VIDEO_DEV" ]; then
    log "FATAL: No camera found"
    exit 1
fi
log "Camera: $VIDEO_DEV"

# --- Detect audio (echocancel → USB pulse → skip) ---
AUDIO_SRC=""
if pactl list short sources 2>/dev/null | grep -q "echocancel_source"; then
    AUDIO_SRC="pulsesrc device=echocancel_source"
    log "Audio: echocancel_source"
elif pactl info >/dev/null 2>&1; then
    USB_SRC=$(pactl list short sources 2>/dev/null | grep -iv "monitor\|echo" | grep -i "usb\|jabra" | head -1 | awk '{print $2}')
    if [ -n "$USB_SRC" ]; then
        AUDIO_SRC="pulsesrc device=$USB_SRC"
        log "Audio: $USB_SRC"
    fi
fi

if [ -z "$AUDIO_SRC" ]; then
    log "No mic found — video only"
fi

# --- Launch GStreamer ---
if [ -n "$AUDIO_SRC" ]; then
    log "Starting video + audio"
    exec gst-launch-1.0 -q \
        v4l2src device=$VIDEO_DEV ! image/jpeg,width=1280,height=720,framerate=30/1 ! jpegdec ! videoconvert ! \
        $VIDEO_ENC ! queue ! mux. \
        $AUDIO_SRC ! queue ! audioconvert ! audioresample ! \
        voaacenc bitrate=128000 ! aacparse ! mux. \
        mpegtsmux name=mux ! fdsink fd=1
else
    log "Starting video only"
    exec gst-launch-1.0 -q \
        v4l2src device=$VIDEO_DEV ! image/jpeg,width=1280,height=720,framerate=30/1 ! jpegdec ! videoconvert ! \
        $VIDEO_ENC ! queue ! \
        mpegtsmux ! fdsink fd=1
fi
