#!/usr/bin/env python3
###############################################################################
#  start-stream.py - GStreamer Tee Pipeline
#
#  Shares 1 CSI camera (IMX219) between:
#    - Branch 1: H.264+AAC → MPEG-TS → stdout (go2rtc exec)
#    - Branch 2: raw BGR → POSIX shared memory (ai_core, no JPEG/ZMQ)
#
#  Modes:
#    exec    (default): fdsink fd=1 → go2rtc reads stdout
#    service          : tcpserversink :8553 → nc reads TCP
#
#  go2rtc.yaml: exec:python3 /opt/stream/start-stream.py
#
#  SHM protocol (must match ai_core/src/shm_video_source.py):
#    Header 64 bytes + double buffer (stride * height each slot)
###############################################################################

import argparse
import mmap
import os
import signal
import struct
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

AI_WIDTH = 1920
AI_HEIGHT = 1080
AI_MAX_FPS = 5

# Shared memory backing file path.
# Accept legacy STREAM_SHM_NAME (/mini_pc_ai_frames) and normalize to /dev/shm/<name>.bin
_raw_shm_path = os.environ.get("STREAM_SHM_PATH", os.environ.get("STREAM_SHM_NAME", "/mini_pc_ai_frames"))
if _raw_shm_path.startswith("/dev/shm/"):
    SHM_PATH = _raw_shm_path
else:
    SHM_PATH = f"/dev/shm/{_raw_shm_path.lstrip('/')}.bin"

# Health watchdog: if no frame produced for this many seconds, pipeline is stalled → exit
HEALTH_TIMEOUT_SEC = 30
WATCHDOG_NOTIFY = True  # notify systemd WatchdogSec

HEADER_SIZE = 64
MAGIC = b"MPAI"
FORMAT_BGR = 0

# ---------------------------------------------------------------------------
# Shared memory writer (double buffer; protocol matches ai_core shm_video_source)
# ---------------------------------------------------------------------------
_shm = None
_shm_fd = None
_shm_lock = threading.Lock()
_shm_active_slot = 0
_shm_seq = 0


def _unlink_shm_if_exists(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[stream] WARNING: could not remove old SHM file {path!r}: {e}", file=sys.stderr, flush=True)


def init_shm_writer() -> bool:
    """Create file-backed mmap segment for raw BGR frames."""
    global _shm, _shm_fd

    stride = AI_WIDTH * 3
    slot_bytes = stride * AI_HEIGHT
    total = HEADER_SIZE + 2 * slot_bytes

    os.makedirs("/dev/shm", exist_ok=True)
    _unlink_shm_if_exists(SHM_PATH)
    try:
        _shm_fd = os.open(SHM_PATH, os.O_CREAT | os.O_RDWR, 0o666)
        os.ftruncate(_shm_fd, total)
        _shm = mmap.mmap(_shm_fd, total, access=mmap.ACCESS_WRITE)
    except Exception as e:
        print(f"[stream] ERROR: SHM create failed: {e}", file=sys.stderr, flush=True)
        return False

    buf = _shm
    buf[0:4] = MAGIC
    struct.pack_into("<I", buf, 4, 1)  # version
    struct.pack_into("<I", buf, 8, AI_WIDTH)
    struct.pack_into("<I", buf, 12, AI_HEIGHT)
    struct.pack_into("<I", buf, 16, stride)
    struct.pack_into("<I", buf, 20, FORMAT_BGR)
    struct.pack_into("<Q", buf, 24, 0)
    struct.pack_into("<I", buf, 32, 0)

    print(
        f"[stream] SHM writer: {SHM_PATH} size={total} ({AI_WIDTH}x{AI_HEIGHT} BGR x2)",
        file=sys.stderr,
        flush=True,
    )
    return True


def publish_frame_bgr(raw: bytes) -> None:
    """Write one BGR frame into SHM (caller holds GStreamer thread; lock protects)."""
    global _shm_active_slot, _shm_seq
    if _shm is None:
        return

    stride = AI_WIDTH * 3
    expected = stride * AI_HEIGHT
    if len(raw) < expected:
        return
    if len(raw) != expected:
        raw = raw[:expected]

    inactive = 1 - _shm_active_slot
    offset = HEADER_SIZE + inactive * expected

    with _shm_lock:
        _shm[offset : offset + expected] = raw
        struct.pack_into("<I", _shm, 8, AI_WIDTH)
        struct.pack_into("<I", _shm, 12, AI_HEIGHT)
        struct.pack_into("<I", _shm, 16, stride)
        struct.pack_into("<I", _shm, 32, inactive & 1)
        _shm_seq += 1
        struct.pack_into("<Q", _shm, 24, _shm_seq)
        _shm_active_slot = inactive


def close_shm_writer() -> None:
    global _shm, _shm_fd
    if _shm is not None:
        try:
            _shm.close()
        except Exception:
            pass
        _shm = None
    if _shm_fd is not None:
        try:
            os.close(_shm_fd)
        except Exception:
            pass
        _shm_fd = None
    _unlink_shm_if_exists(SHM_PATH)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def log(msg: str) -> None:
    print(f"[stream] {msg}", file=sys.stderr, flush=True)


def err(msg: str) -> None:
    print(f"[stream] ERROR: {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Health watchdog — detect stalled pipeline and auto-exit
# ---------------------------------------------------------------------------
_last_frame_time = time.monotonic()
_last_frame_lock = threading.Lock()


def touch_health():
    """Called every time a frame is produced (stream or AI branch)."""
    global _last_frame_time
    with _last_frame_lock:
        _last_frame_time = time.monotonic()


def _sd_notify(state: str):
    """Send sd_notify message (READY=1, WATCHDOG=1, etc.) via $NOTIFY_SOCKET."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    import socket
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        if addr.startswith("@"):
            addr = "\0" + addr[1:]
        sock.sendto(state.encode(), addr)
        sock.close()
    except Exception:
        pass


def health_watchdog(pipeline, loop):
    """Background thread: exits process if pipeline stalls (no frames for HEALTH_TIMEOUT_SEC)."""
    while True:
        time.sleep(10)
        with _last_frame_lock:
            elapsed = time.monotonic() - _last_frame_time

        if elapsed < HEALTH_TIMEOUT_SEC:
            _sd_notify("WATCHDOG=1")
        else:
            err(f"HEALTH WATCHDOG: no frame for {elapsed:.0f}s (limit {HEALTH_TIMEOUT_SEC}s) — forcing restart")
            try:
                pipeline.set_state(Gst.State.NULL)
            except Exception:
                pass
            try:
                loop.quit()
            except Exception:
                pass
            time.sleep(2)
            os._exit(1)


# ---------------------------------------------------------------------------
# GStreamer appsink callback (raw BGR)
# ---------------------------------------------------------------------------
def on_new_sample(sink) -> Gst.FlowReturn:
    sample = sink.emit("pull-sample")
    if sample is None:
        return Gst.FlowReturn.OK

    buf = sample.get_buffer()
    ok, map_info = buf.map(Gst.MapFlags.READ)
    if not ok:
        return Gst.FlowReturn.OK

    raw = bytes(map_info.data)
    buf.unmap(map_info)

    touch_health()
    publish_frame_bgr(raw)

    return Gst.FlowReturn.OK


# ---------------------------------------------------------------------------
# PulseAudio check
# ---------------------------------------------------------------------------
def has_echocancel() -> bool:
    """Check if PulseAudio echocancel_source is available, with retries."""
    import subprocess

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

    video_src = (
        f"nvarguscamerasrc wbmode=1 ispdigitalgainrange=\"1 1\" ! "
        f"video/x-raw(memory:NVMM),width={STREAM_WIDTH},height={STREAM_HEIGHT},"
        f"framerate={STREAM_FPS}/1 ! "
        f"nvvidconv ! video/x-raw,format=I420 ! "
        f"tee name=t"
    )

    stream_branch = (
        f"t. ! queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 ! "
        f"x264enc tune=zerolatency speed-preset=ultrafast "
        f"bitrate={STREAM_BITRATE} key-int-max={STREAM_FPS} ! "
        f"h264parse config-interval=-1 ! "
        f"queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 ! mux."
    )

    if with_audio:
        audio_branch = (
            "pulsesrc device=echocancel_source buffer-time=100000 latency-time=50000 do-timestamp=true ! "
            "queue leaky=downstream max-size-time=1000000000 max-size-buffers=0 max-size-bytes=0 ! "
            "audioconvert ! audioresample ! volume volume=8.0 ! "
            "voaacenc bitrate=128000 ! aacparse ! mux."
        )
    else:
        audio_branch = ""

    if mode == "service":
        mux_out = (
            f"mpegtsmux name=mux alignment=7 ! "
            f"tcpserversink host=0.0.0.0 port={TCP_PORT} "
            f"recover-policy=keyframe sync-method=latest-keyframe"
        )
    else:
        mux_out = "mpegtsmux name=mux alignment=7 ! fdsink fd=1"

    # AI branch: raw BGR → appsink (no jpegenc / ZMQ)
    ai_branch = (
        f"t. ! queue leaky=downstream max-size-buffers=2 "
        f"max-size-bytes=0 max-size-time=0 ! "
        f"videorate ! video/x-raw,framerate={AI_MAX_FPS}/1 ! "
        f"videoscale ! video/x-raw,width={AI_WIDTH},height={AI_HEIGHT} ! "
        f"videoconvert ! video/x-raw,format=BGR ! "
        f"appsink name=ai_sink emit-signals=true max-buffers=1 drop=true sync=false"
    )

    pipeline_str = f"{video_src} {stream_branch} {audio_branch} {mux_out} {ai_branch}"

    log(f"Pipeline: {pipeline_str}")

    pipeline = Gst.parse_launch(pipeline_str)

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

    uid = os.getuid()
    os.environ["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"
    os.environ["PULSE_SERVER"] = f"unix:/run/user/{uid}/pulse/native"

    with_audio = has_echocancel()
    if with_audio:
        log("Audio: echocancel_source ✓")
    else:
        log("No echocancel — video only")

    if not init_shm_writer():
        err("Failed to init SHM — exiting")
        return 1

    log(f"AI frames: SHM {SHM_PATH} (max {AI_MAX_FPS}fps, {AI_WIDTH}x{AI_HEIGHT} BGR)")

    pipeline = build_pipeline(with_audio, mode=args.mode)
    pipeline.set_state(Gst.State.PLAYING)
    log("Pipeline PLAYING")

    _sd_notify("READY=1")

    touch_health()

    loop = GLib.MainLoop()

    def on_signal(sig, frame):
        log(f"Signal {sig} received, stopping...")
        pipeline.set_state(Gst.State.NULL)
        loop.quit()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

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

    wd = threading.Thread(target=health_watchdog, args=(pipeline, loop), daemon=True)
    wd.start()
    log(f"Health watchdog started (timeout={HEALTH_TIMEOUT_SEC}s)")

    try:
        loop.run()
    except Exception as e:
        err(f"Main loop error: {e}")
    finally:
        pipeline.set_state(Gst.State.NULL)
        close_shm_writer()
        log("Pipeline stopped")

    return 0


if __name__ == "__main__":
    sys.exit(main())
