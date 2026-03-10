#!/usr/bin/env python3
"""
Audio Backchannel Server — receives audio via WebSocket, plays through PulseAudio.
Supports WebM/Opus (browser/Android) and PCMU/G.711 (iOS). Format via ?type=ios param.
Only 1 connection at a time.

Usage: python3 server.py [--port 8080] [--sink echocancel_sink]
Requires: ffmpeg, pacat (pulseaudio-utils), pip3 install websockets
"""

import asyncio
import argparse
import struct
import subprocess
import signal
import sys
import logging
import shutil
from typing import Optional
from urllib.parse import urlparse, parse_qs

try:
    import websockets
    from websockets.server import WebSocketServerProtocol
except ImportError:
    print("ERROR: pip3 install websockets")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# ── EBML / WebM header builder ──────────────────────────────────────────────

def _vint(value):
    """Encode value as EBML variable-length size integer"""
    if value <= 126:
        return bytes([0x80 | value])
    elif value <= 16382:
        return bytes([0x40 | (value >> 8), value & 0xFF])
    elif value <= 2097150:
        return bytes([0x20 | (value >> 16), (value >> 8) & 0xFF, value & 0xFF])
    else:
        return bytes([0x10 | (value >> 24), (value >> 16) & 0xFF,
                      (value >> 8) & 0xFF, value & 0xFF])


def _elem(eid: bytes, data: bytes) -> bytes:
    """Build EBML element: raw ID bytes + VINT size + data"""
    return eid + _vint(len(data)) + data


def _uint(val: int) -> bytes:
    """Encode unsigned integer for EBML element content"""
    if val == 0:
        return b'\x00'
    n = (val.bit_length() + 7) // 8
    return val.to_bytes(n, 'big')


def build_webm_opus_header() -> bytes:
    """Build minimal WebM header for Opus 48 kHz mono streaming.
    Returns EBML Header + Segment(Info + Tracks) + Cluster start (timecode=0).
    """
    # OpusHead for CodecPrivate
    opus_head = (
        b'OpusHead\x01\x01'           # magic + version + 1 channel
        + struct.pack('<H', 312)       # pre-skip
        + struct.pack('<I', 48000)     # input sample rate
        + b'\x00\x00'                 # output gain
        + b'\x00'                     # channel mapping family 0
    )

    audio = (
        _elem(b'\xb5', struct.pack('>d', 48000.0)) +  # SamplingFrequency
        _elem(b'\x9f', _uint(1))                       # Channels = 1
    )

    track_entry = (
        _elem(b'\xd7', _uint(1)) +          # TrackNumber = 1
        _elem(b'\x73\xc5', _uint(1)) +      # TrackUID = 1
        _elem(b'\x83', _uint(2)) +           # TrackType = 2 (audio)
        _elem(b'\x86', b'A_OPUS') +          # CodecID
        _elem(b'\x63\xa2', opus_head) +      # CodecPrivate
        _elem(b'\xe1', audio)                # Audio
    )

    tracks = _elem(b'\x16\x54\xae\x6b',     # Tracks element
                   _elem(b'\xae', track_entry))  # single TrackEntry

    info = _elem(b'\x15\x49\xa9\x66',       # Info element
                 _elem(b'\x2a\xd7\xb1', _uint(1000000)))  # TimestampScale=1ms

    ebml_header = _elem(b'\x1a\x45\xdf\xa3',  # EBML Header
        _elem(b'\x42\x86', _uint(1)) +        # EBMLVersion
        _elem(b'\x42\xf7', _uint(1)) +        # EBMLReadVersion
        _elem(b'\x42\xf2', _uint(4)) +        # EBMLMaxIDLength
        _elem(b'\x42\xf3', _uint(8)) +        # EBMLMaxSizeLength
        _elem(b'\x42\x82', b'webm') +         # DocType
        _elem(b'\x42\x87', _uint(4)) +        # DocTypeVersion
        _elem(b'\x42\x85', _uint(2))          # DocTypeReadVersion
    )

    # Segment with unknown/infinite size (streaming)
    segment_start = b'\x18\x53\x80\x67' + b'\x01\xff\xff\xff\xff\xff\xff\xff'

    # Initial Cluster at timecode 0 with unknown size
    cluster_start = (
        b'\x1f\x43\xb6\x75'                   # Cluster element ID
        + b'\x01\xff\xff\xff\xff\xff\xff\xff'  # unknown size
        + b'\xe7\x81\x00'                      # Timestamp = 0
    )

    return ebml_header + segment_start + info + tracks + cluster_start


# Pre-build header once at import time
WEBM_OPUS_HEADER = build_webm_opus_header()

EBML_MAGIC = b'\x1a\x45\xdf\xa3'  # First 4 bytes of any WebM/Matroska file


def find_simple_block_offset(data: bytes) -> int:
    """Find first WebM SimpleBlock (ID=0xA3) in raw data.
    Returns byte offset or -1 if not found.
    """
    for i in range(len(data) - 6):
        if data[i] != 0xA3:
            continue
        # Validate: next bytes should be a reasonable VINT size, then track=0x81
        b1 = data[i + 1]
        if b1 & 0x80:  # 1-byte VINT
            size = b1 & 0x7F
            track_idx = i + 2
        elif b1 & 0x40:  # 2-byte VINT
            size = ((b1 & 0x3F) << 8) | data[i + 2]
            track_idx = i + 3
        else:
            continue
        if size < 20 or size > 4000:
            continue
        if track_idx < len(data) and data[track_idx] == 0x81:
            return i
    return -1


# ── Dependency check ────────────────────────────────────────────────────────

def check_deps():
    """Verify ffmpeg and pacat are installed"""
    missing = []
    if not shutil.which('ffmpeg'):
        missing.append('ffmpeg (apt install ffmpeg)')
    if not shutil.which('pacat'):
        missing.append('pacat (apt install pulseaudio-utils)')
    if missing:
        for m in missing:
            log.error(f"Missing: {m}")
        sys.exit(1)


def get_format_from_path(path: str) -> str:
    """Determine format from WS query param: ?type=ios → PCMU, else WebM/Opus"""
    qs = parse_qs(urlparse(path).query)
    client_type = qs.get('type', [''])[0].lower()
    if client_type == 'ios':
        return 'pcmu'
    return 'webm'


# ── Server ──────────────────────────────────────────────────────────────────

class BackchannelServer:
    def __init__(self, port: int, sink: str):
        self.port = port
        self.sink = sink
        self.active_client: Optional[str] = None
        self.process: Optional[subprocess.Popen] = None
        self.pacat: Optional[subprocess.Popen] = None
        self.count = 0

    def start_pipeline(self, client: str, fmt: str) -> bool:
        """Start audio pipeline. PCMU→pacat direct, WebM→FFmpeg→pacat."""
        try:
            err = open(f'/tmp/backchannel_{client.replace(":", "_")}.log', 'a')

            if fmt == 'pcmu':
                # PCMU: FFmpeg with zero probing (format pre-specified)
                ffmpeg_cmd = [
                    'ffmpeg', '-hide_banner', '-loglevel', 'warning',
                    '-probesize', '32', '-analyzeduration', '0',
                    '-fflags', '+nobuffer+flush_packets',
                    '-flags', 'low_delay',
                    '-f', 'mulaw', '-ar', '8000', '-ac', '1',
                    '-i', 'pipe:0',
                    '-ac', '1', '-ar', '48000', '-af', 'volume=4.0',
                    '-f', 's16le', '-flush_packets', '1', 'pipe:1'
                ]
                pacat_cmd = ['pacat', '--format=s16le', '--rate=48000', '--channels=1',
                             '--latency-msec=30', '--device', self.sink]
                self.process = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE,
                                                stdout=subprocess.PIPE, stderr=err)
                self.pacat = subprocess.Popen(pacat_cmd, stdin=self.process.stdout,
                                              stdout=subprocess.DEVNULL, stderr=err)
                self.process.stdout.close()
                log.info(f"[{client}] PCMU FFmpeg→pacat (zero-probe) "
                         f"(ffmpeg={self.process.pid}, pacat={self.pacat.pid})")
            else:
                # WebM/Opus: FFmpeg decode → pacat playback
                ffmpeg_cmd = [
                    'ffmpeg', '-hide_banner', '-loglevel', 'warning',
                    '-fflags', '+nobuffer+flush_packets+discardcorrupt',
                    '-flags', 'low_delay',
                    '-probesize', '32768', '-analyzeduration', '200000',
                    '-f', 'matroska',
                    '-i', 'pipe:0',
                    '-ac', '1', '-ar', '48000', '-af', 'volume=4.0',
                    '-f', 's16le', '-flush_packets', '1', 'pipe:1'
                ]
                pacat_cmd = ['pacat', '--format=s16le', '--rate=48000', '--channels=1',
                             '--latency-msec=50', '--device', self.sink]
                self.process = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE,
                                                stdout=subprocess.PIPE, stderr=err)
                self.pacat = subprocess.Popen(pacat_cmd, stdin=self.process.stdout,
                                              stdout=subprocess.DEVNULL, stderr=err)
                self.process.stdout.close()
                log.info(f"[{client}] WebM/Opus FFmpeg→pacat "
                         f"(ffmpeg={self.process.pid}, pacat={self.pacat.pid})")

            return True
        except Exception as e:
            log.error(f"[{client}] Pipeline failed: {e}")
            return False

    def stop_pipeline(self):
        """Stop FFmpeg and pacat"""
        for proc in [self.pacat, self.process]:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if self.process and self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        self.process = None
        self.pacat = None
        self.count = 0

    async def handle(self, ws: WebSocketServerProtocol):
        client = f"{ws.remote_address[0]}:{ws.remote_address[1]}"

        # Single connection lock
        if self.active_client:
            log.warning(f"[{client}] Rejected — busy ({self.active_client})")
            await ws.close(1013, "Busy — another user connected")
            return

        self.active_client = client
        path = ws.request.path if hasattr(ws, 'request') and ws.request else getattr(ws, 'path', '/')
        fmt = get_format_from_path(path)
        log.info(f"[{client}] Connected (format={fmt}, path={path})")

        try:
            # PCMU: pre-start pipeline (direct pacat, no FFmpeg delay)
            if fmt == 'pcmu':
                if not self.start_pipeline(client, fmt):
                    await ws.close(1011, "Audio pipeline failed")
                    return
                log.info(f"[{client}] PCMU pipeline ready")

            # For WebM: buffer first 3 messages to detect if header is present
            init_buffer = []
            async for msg in ws:
                if isinstance(msg, str):
                    log.info(f"[{client}] TEXT: {msg[:200]}")
                    continue
                if not isinstance(msg, bytes):
                    continue

                # ── Pipeline not started yet (WebM only) ──
                if self.process is None:

                    # WebM: buffer first 3 messages for header detection
                    init_buffer.append(msg)
                    if len(init_buffer) < 3:
                        continue

                    combined = b''.join(init_buffer)
                    has_ebml = combined[:4] == EBML_MAGIC
                    log.info(f"[{client}] Buffered {len(combined)} bytes, "
                             f"EBML header={'YES' if has_ebml else 'NO'}, "
                             f"first_hex={combined[:16].hex(' ')}")

                    if not self.start_pipeline(client, fmt):
                        await ws.close(1011, "Audio pipeline failed")
                        return

                    if has_ebml:
                        # Browser: data already has WebM header, pass through
                        self.process.stdin.write(combined)
                        log.info(f"[{client}] Browser WebM with header — pass-through")
                    else:
                        # Android: headerless WebM, prepend synthetic header
                        self.process.stdin.write(WEBM_OPUS_HEADER)
                        # Try to find first valid SimpleBlock boundary
                        offset = find_simple_block_offset(combined)
                        if offset >= 0:
                            log.info(f"[{client}] Android headerless WebM — "
                                     f"synthetic header + data from SimpleBlock@{offset}")
                            self.process.stdin.write(combined[offset:])
                        else:
                            log.info(f"[{client}] Android headerless WebM — "
                                     f"synthetic header + raw data (no SimpleBlock found)")
                            self.process.stdin.write(combined)

                    self.process.stdin.flush()
                    init_buffer = None
                    continue

                # ── Pipeline running: stream data ──

                # Restart if ffmpeg crashed
                if self.process.poll() is not None:
                    log.warning(f"[{client}] FFmpeg exited ({self.process.returncode}), restarting")
                    self.stop_pipeline()
                    await asyncio.sleep(0.1)
                    if fmt == 'webm':
                        if not self.start_pipeline(client, fmt):
                            break
                        self.process.stdin.write(WEBM_OPUS_HEADER)
                        self.process.stdin.flush()
                    else:
                        if not self.start_pipeline(client, fmt):
                            break

                try:
                    self.process.stdin.write(msg)
                    self.process.stdin.flush()
                    self.count += 1
                    if self.count <= 20 or self.count % 100 == 1:
                        log.info(f"[{client}] [{fmt}] chunk#{self.count} len={len(msg)}")
                except BrokenPipeError:
                    log.warning(f"[{client}] Pipe broken, restarting")
                    self.stop_pipeline()
                    await asyncio.sleep(0.1)
                    if not self.start_pipeline(client, fmt):
                        break

        except websockets.exceptions.ConnectionClosed as e:
            log.info(f"[{client}] Disconnected ({e.code})")
        except Exception as e:
            log.error(f"[{client}] Error: {e}")
        finally:
            self.stop_pipeline()
            self.active_client = None
            log.info(f"[{client}] Slot released")

    async def run(self):
        log.info(f"Backchannel server on ws://0.0.0.0:{self.port} (sink: {self.sink})")
        async with websockets.serve(self.handle, "0.0.0.0", self.port,
                                    ping_interval=30, ping_timeout=10):
            await asyncio.Future()


def main():
    parser = argparse.ArgumentParser(description='Audio Backchannel Server')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--sink', default='echocancel_sink', help='PulseAudio sink')
    args = parser.parse_args()

    check_deps()

    server = BackchannelServer(port=args.port, sink=args.sink)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: (server.stop_pipeline(), sys.exit(0)))

    asyncio.run(server.run())


if __name__ == "__main__":
    main()