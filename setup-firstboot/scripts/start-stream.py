#!/usr/bin/env python3
###############################################################################
#  start-stream.py - GStreamer Tee Pipeline (24/7 service)
#
#  Runs as systemd service, shares 1 CSI camera (IMX219) between:
#    - Branch 1: H.264+AAC → MPEG-TS → TCP :8554 (go2rtc reads this)
#    - Branch 2: AI JPEG frames → ZMQ ipc:///tmp/ai_frames.sock
#
#  go2rtc.yaml: tcp://localhost:8554
#  AI consumer: zmq.SUB → ipc:///tmp/ai_frames.sock
###############################################################################

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
STREAM_BITRATE = 4000  # kbps (higher for 1080p)
TCP_PORT = 8554  # MPEG-TS TCP server port for go2rtc

AI_WIDTH = 1920  # same as stream — needed for photo capture
AI_HEIGHT = 1080
AI_MAX_FPS = 5  # limit AI frame rate

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
_ai_interval = 1.0 / AI_MAX_FPS


def on_new_sample(sink) -> Gst.FlowReturn:
    """Called when appsink has a new JPEG frame for AI."""
    global _last_ai_frame_time

    # Frame rate limiter
    now = time.monotonic()
    if now - _last_ai_frame_time < _ai_interval:
        return Gst.FlowReturn.OK
    _last_ai_frame_time = now

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
    """Check if PulseAudio echocancel_source is available."""
    import subprocess

    try:
        result = subprocess.run(
            ["pactl", "list", "short", "sources"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return "echocancel_source" in result.stdout
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Build pipeline
# ---------------------------------------------------------------------------
def build_pipeline(with_audio: bool) -> Gst.Pipeline:
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
            "pulsesrc device=echocancel_source ! "
            "queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 ! "
            "audioconvert ! audioresample ! volume volume=3.0 ! "
            "voaacenc bitrate=128000 ! aacparse ! mux."
        )
    else:
        audio_branch = ""

    # Muxer → TCP server (go2rtc reads from tcp://localhost:8554)
    mux_out = (
        f"mpegtsmux name=mux alignment=7 ! "
        f"tcpserversink host=0.0.0.0 port={TCP_PORT} "
        f"recover-policy=keyframe sync-method=latest-keyframe"
    )

    # Branch 2: AI → appsink (JPEG for ZMQ)
    ai_branch = (
        f"t. ! queue leaky=downstream max-size-buffers=2 "
        f"max-size-bytes=0 max-size-time=0 ! "
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
    Gst.init(None)

    # PulseAudio env
    uid = os.getuid()
    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    os.environ.setdefault(
        "PULSE_SERVER", f"unix:/run/user/{uid}/pulse/native"
    )

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
    pipeline = build_pipeline(with_audio)
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
