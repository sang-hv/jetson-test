#!/usr/bin/env python3
###############################################################################
#  start-stream.py - GStreamer Tee Pipeline
#
#  Shares 1 CSI camera (IMX219) between:
#    - Branch 1: H.264+AAC → MPEG-TS → stdout (go2rtc exec)
#    - Branch 2: AI JPEG frames → ZMQ ipc:///tmp/ai_frames.sock
#
#  Modes:
#    exec    (default): fdsink fd=1 → go2rtc reads stdout
#    service          : tcpserversink :8553 → nc reads TCP
#
#  go2rtc.yaml: exec:python3 /opt/stream/start-stream.py
###############################################################################

import argparse
import os
import signal
import sys
import threading
import time

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
STREAM_WIDTH = 1920
STREAM_HEIGHT = 1080
STREAM_FPS = 30
STREAM_BITRATE = 2000  # kbps — reduced to save RAM
TCP_PORT = 8553

AI_WIDTH = 1920   # smaller = less memory, sufficient for detection
AI_HEIGHT = 1080
AI_MAX_FPS = 5   # 3fps is enough for AI detection

ZMQ_ENDPOINT = "ipc:///tmp/ai_frames.sock"
JPEG_QUALITY = 85  # higher quality for photo capture

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def log(msg: str) -> None:
    print(f"[stream] {msg}", file=sys.stderr, flush=True)


def err(msg: str) -> None:
    print(f"[stream] ERROR: {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# ZMQ Publisher (lazy import — works without zmq for stream-only mode)
# ---------------------------------------------------------------------------
_zmq_socket = None
_zmq_lock = threading.Lock()


def init_zmq() -> bool:
    """Initialize ZMQ publisher. Returns True if successful."""
    global _zmq_socket
    try:
        import zmq

        ctx = zmq.Context()
        _zmq_socket = ctx.socket(zmq.PUB)
        _zmq_socket.setsockopt(zmq.SNDHWM, 2)  # drop old frames
        _zmq_socket.bind(ZMQ_ENDPOINT)
        log(f"ZMQ publisher bound: {ZMQ_ENDPOINT}")
        return True
    except ImportError:
        log("WARNING: zmq not installed — AI frame publishing disabled")
        return False
    except Exception as e:
        log(f"WARNING: ZMQ init failed: {e}")
        return False


def publish_frame(jpeg_data: bytes, timestamp_ns: int) -> None:
    """Publish a JPEG frame via ZMQ (non-blocking, drops if no subscriber)."""
    if _zmq_socket is None:
        return
    try:
        import struct

        # Message format: [8-byte timestamp][jpeg bytes]
        header = struct.pack("<Q", timestamp_ns)
        _zmq_socket.send(header + jpeg_data, flags=1)  # NOBLOCK
    except Exception:
        pass  # silently drop if no subscriber or queue full


# ---------------------------------------------------------------------------
# GStreamer appsink callback
# ---------------------------------------------------------------------------
_last_ai_frame_time = 0.0


def on_new_sample(sink) -> Gst.FlowReturn:
    """Called when appsink has a new JPEG frame for AI (already rate-limited by videorate)."""
    global _last_ai_frame_time

    sample = sink.emit("pull-sample")
    if sample is None:
        return Gst.FlowReturn.OK

    buf = sample.get_buffer()
    ok, map_info = buf.map(Gst.MapFlags.READ)
    if not ok:
        return Gst.FlowReturn.OK

    jpeg_data = bytes(map_info.data)
    buf.unmap(map_info)

    publish_frame(jpeg_data, buf.pts)

    return Gst.FlowReturn.OK


# ---------------------------------------------------------------------------
# PulseAudio check
# ---------------------------------------------------------------------------
def has_echocancel() -> bool:
    """Check if PulseAudio echocancel_source is available, with retries."""
    import subprocess

    # Debug: show PulseAudio env
    log(f"  PulseAudio debug:")
    log(f"    UID: {os.getuid()}")
    log(f"    XDG_RUNTIME_DIR: {os.environ.get('XDG_RUNTIME_DIR', '<not set>')}")
    log(f"    PULSE_SERVER: {os.environ.get('PULSE_SERVER', '<not set>')}")
    log(f"    HOME: {os.environ.get('HOME', '<not set>')}")

    for attempt in range(10):
        try:
            result = subprocess.run(
                ["pactl", "list", "short", "sources"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            log(f"    pactl exit={result.returncode} stdout='{result.stdout.strip()[:200]}' stderr='{result.stderr.strip()[:200]}'")
            if "echocancel_source" in result.stdout:
                return True
            if attempt < 9:
                log(f"  PulseAudio: echocancel not found, retry {attempt + 1}/10...")
                time.sleep(2)
        except Exception as e:
            if attempt < 9:
                log(f"  PulseAudio: not ready ({e}), retry {attempt + 1}/10...")
                time.sleep(2)
    return False


# ---------------------------------------------------------------------------
# Build pipeline
# ---------------------------------------------------------------------------
def build_pipeline(with_audio: bool, mode: str = "exec") -> Gst.Pipeline:
    """Build GStreamer pipeline with tee for stream + AI."""

    # Video source (shared)
    video_src = (
        f"nvarguscamerasrc wbmode=1 ispdigitalgainrange=\"1 1\" ! "
        f"video/x-raw(memory:NVMM),width={STREAM_WIDTH},height={STREAM_HEIGHT},"
        f"framerate={STREAM_FPS}/1 ! "
        f"nvvidconv ! video/x-raw,format=I420 ! "
        f"tee name=t"
    )

    # Branch 1: Stream → fdsink (for go2rtc)
    stream_branch = (
        f"t. ! queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 ! "
        f"x264enc tune=zerolatency speed-preset=ultrafast "
        f"bitrate={STREAM_BITRATE} key-int-max={STREAM_FPS} ! "
        f"h264parse config-interval=-1 ! "
        f"queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 ! mux."
    )

    # Audio (if available)
    if with_audio:
        audio_branch = (
            # buffer-time/latency-time: read 100ms chunks to handle echocancel latency
            # do-timestamp=true: use pipeline clock instead of PulseAudio timestamps
            "pulsesrc device=echocancel_source buffer-time=100000 latency-time=50000 do-timestamp=true ! "
            # Use a real-time queue with 1-second buffer; leaky=downstream drops old audio, not new
            "queue leaky=downstream max-size-time=1000000000 max-size-buffers=0 max-size-bytes=0 ! "
            "audioconvert ! audioresample ! volume volume=8.0 ! "
            "voaacenc bitrate=128000 ! aacparse ! mux."
        )
    else:
        audio_branch = ""

    # Muxer → output (exec mode: stdout, service mode: TCP)
    if mode == "service":
        mux_out = (
            f"mpegtsmux name=mux alignment=7 ! "
            f"tcpserversink host=0.0.0.0 port={TCP_PORT} "
            f"recover-policy=keyframe sync-method=latest-keyframe"
        )
    else:
        mux_out = "mpegtsmux name=mux alignment=7 ! fdsink fd=1"

    # Branch 2: AI → appsink (JPEG for ZMQ)
    # videorate limits to AI_MAX_FPS BEFORE heavy processing (saves GPU memory)
    ai_branch = (
        f"t. ! queue leaky=downstream max-size-buffers=2 "
        f"max-size-bytes=0 max-size-time=0 ! "
        f"videorate ! video/x-raw,framerate={AI_MAX_FPS}/1 ! "
        f"videoscale ! video/x-raw,width={AI_WIDTH},height={AI_HEIGHT} ! "
        f"videoconvert ! video/x-raw,format=RGB ! "
        f"jpegenc quality={JPEG_QUALITY} ! "
        f"appsink name=ai_sink emit-signals=true max-buffers=1 drop=true sync=false"
    )

    pipeline_str = f"{video_src} {stream_branch} {audio_branch} {mux_out} {ai_branch}"

    log(f"Pipeline: {pipeline_str}")

    pipeline = Gst.parse_launch(pipeline_str)

    # Connect appsink callback
    ai_sink = pipeline.get_by_name("ai_sink")
    if ai_sink:
        ai_sink.connect("new-sample", on_new_sample)

    return pipeline


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["exec", "service"], default="exec",
                        help="exec: fdsink stdout (go2rtc), service: tcpserversink TCP")
    args = parser.parse_args()

    Gst.init(None)
    log(f"Mode: {args.mode}")

    # PulseAudio env — force-set using actual UID (%U in systemd resolves wrong)
    uid = os.getuid()
    os.environ["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"
    os.environ["PULSE_SERVER"] = f"unix:/run/user/{uid}/pulse/native"

    # Check audio
    with_audio = has_echocancel()
    if with_audio:
        log("Audio: echocancel_source ✓")
    else:
        log("No echocancel — video only")

    # Init ZMQ for AI frames
    zmq_ok = init_zmq()
    if zmq_ok:
        log(f"AI frames: ZMQ → {ZMQ_ENDPOINT} (max {AI_MAX_FPS}fps, {AI_WIDTH}x{AI_HEIGHT})")
    else:
        log("AI frames: disabled (stream only mode)")

    # Build and start pipeline
    pipeline = build_pipeline(with_audio, mode=args.mode)
    pipeline.set_state(Gst.State.PLAYING)
    log("Pipeline PLAYING")

    # Main loop
    loop = GLib.MainLoop()

    # Handle signals
    def on_signal(sig, frame):
        log(f"Signal {sig} received, stopping...")
        pipeline.set_state(Gst.State.NULL)
        loop.quit()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    # Watch for errors
    bus = pipeline.get_bus()
    bus.add_signal_watch()

    def on_bus_message(bus, msg):
        t = msg.type
        if t == Gst.MessageType.ERROR:
            error, debug = msg.parse_error()
            err(f"GStreamer error: {error.message}")
            if debug:
                err(f"  Debug: {debug}")
            pipeline.set_state(Gst.State.NULL)
            loop.quit()
        elif t == Gst.MessageType.EOS:
            log("End of stream")
            pipeline.set_state(Gst.State.NULL)
            loop.quit()
        elif t == Gst.MessageType.WARNING:
            warning, debug = msg.parse_warning()
            log(f"WARNING: {warning.message}")

    bus.connect("message", on_bus_message)

    try:
        loop.run()
    except Exception as e:
        err(f"Main loop error: {e}")
    finally:
        pipeline.set_state(Gst.State.NULL)
        log("Pipeline stopped")

    return 0


if __name__ == "__main__":
    sys.exit(main())
