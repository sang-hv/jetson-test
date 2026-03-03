#!/usr/bin/env python3
"""
Audio Backchannel Server for Jetson Nano
Receives audio from browser via WebSocket and plays through speaker using FFmpeg/ALSA

Usage:
    python3 server.py [--port 8080] [--audio-device default]

Requirements:
    pip3 install websockets
"""

import asyncio
import argparse
import subprocess
import signal
import sys
import logging
from typing import Optional

try:
    import websockets
    from websockets.server import WebSocketServerProtocol
except ImportError:
    print("Error: websockets not installed. Run: pip3 install websockets")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AudioBackchannelServer:
    """WebSocket server that receives audio and plays through speaker"""
    
    def __init__(self, port: int = 8080, audio_device: str = "default"):
        self.port = port
        self.audio_device = audio_device
        self.active_connections: dict[str, subprocess.Popen] = {}
        self.running = True
        self.current_client_id: Optional[str] = None  # Only 1 connection allowed
        self.ffmpeg_path = self._find_ffmpeg()
        self.msg_count: dict[str, int] = {}  # Per-client message counter
    
    @staticmethod
    def _find_ffmpeg() -> str:
        """Find FFmpeg binary — prefer system install over static (static lacks webm/opus)"""
        for path in ['/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg', '/usr/local/bin/ffmpeg-static', 'ffmpeg']:
            try:
                result = subprocess.run(
                    [path, '-version'], capture_output=True, timeout=3
                )
                if result.returncode == 0:
                    version = result.stdout.decode().split('\n')[0]
                    logger.info(f"Using FFmpeg: {path} ({version})")
                    return path
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        logger.warning("FFmpeg not found, using 'ffmpeg'")
        return 'ffmpeg'
        
    def create_ffmpeg_process(self, client_id: str) -> Optional[subprocess.Popen]:
        """Create FFmpeg→pacat pipeline to decode WebM/Opus and play through PulseAudio"""
        try:
            # Determine PulseAudio sink for pacat
            pulse_sink = 'echocancel_sink'
            if self.audio_device and self.audio_device != 'default' and self.audio_device != 'pulse':
                pulse_sink = self.audio_device
            
            # Check if echocancel_sink exists, fallback to default
            check = subprocess.run(
                ['pactl', 'list', 'short', 'sinks'],
                capture_output=True, text=True, timeout=3
            )
            if pulse_sink not in check.stdout:
                logger.warning(f"[{client_id}] Sink '{pulse_sink}' not found, using default")
                pulse_sink = None  # pacat will use default sink
            
            logger.info(f"[{client_id}] Starting FFmpeg→pacat pipeline (sink: {pulse_sink or 'default'})")

            # FFmpeg: decode WebM/Opus → raw PCM s16le to stdout
            ffmpeg_cmd = [
                self.ffmpeg_path,
                '-hide_banner',
                '-loglevel', 'warning',
                # Input: WebM/Opus - ultra low latency
                '-fflags', '+nobuffer+flush_packets',
                '-flags', 'low_delay',
                '-probesize', '32',
                '-analyzeduration', '0',
                '-f', 'webm',
                '-i', 'pipe:0',
                # Output: raw PCM to stdout
                '-ac', '2',
                '-ar', '48000',
                '-af', 'volume=4.0',
                '-f', 's16le',
                '-flush_packets', '1',
                'pipe:1'
            ]
            
            # pacat: play raw PCM through PulseAudio
            pacat_cmd = ['pacat', '--format=s16le', '--rate=48000', '--channels=2', '--latency-msec=50']
            if pulse_sink:
                pacat_cmd.extend(['--device', pulse_sink])
            
            stderr_log = open(f'/tmp/ffmpeg_{client_id.replace(":","_")}.log', 'a')
            
            # Pipe: FFmpeg stdout → pacat stdin
            ffmpeg_proc = subprocess.Popen(
                ffmpeg_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr_log,
            )
            
            pacat_proc = subprocess.Popen(
                pacat_cmd,
                stdin=ffmpeg_proc.stdout,
                stdout=subprocess.DEVNULL,
                stderr=stderr_log,
            )
            
            # Allow ffmpeg_proc to receive SIGPIPE if pacat exits
            ffmpeg_proc.stdout.close()
            
            # Store pacat reference for cleanup
            ffmpeg_proc._pacat = pacat_proc
            
            logger.info(f"[{client_id}] FFmpeg PID {ffmpeg_proc.pid}, pacat PID {pacat_proc.pid}")
            return ffmpeg_proc
            
        except FileNotFoundError:
            logger.error("FFmpeg not found. Please install: sudo apt install ffmpeg")
            return None
        except Exception as e:
            logger.error(f"Failed to start FFmpeg: {e}")
            return None
    
    def cleanup_ffmpeg(self, client_id: str):
        """Cleanup FFmpeg and pacat processes for a client"""
        if client_id in self.active_connections:
            process = self.active_connections[client_id]
            # Cleanup pacat first
            if hasattr(process, '_pacat') and process._pacat:
                pacat = process._pacat
                if pacat.poll() is None:
                    pacat.terminate()
                    try:
                        pacat.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pacat.kill()
            # Cleanup FFmpeg
            if process and process.poll() is None:
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                logger.info(f"[{client_id}] FFmpeg process terminated")
            del self.active_connections[client_id]
    
    async def handle_client(self, websocket: WebSocketServerProtocol):
        """Handle incoming WebSocket connection (only 1 at a time)"""
        client_id = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
        logger.info(f"[{client_id}] Client attempting to connect")
        
        # Reject if another client is already connected
        if self.current_client_id is not None:
            logger.warning(f"[{client_id}] Rejected — already occupied by {self.current_client_id}")
            await websocket.close(1013, "Backchannel busy — another user is connected")
            return
        
        # Lock this slot
        self.current_client_id = client_id
        logger.info(f"[{client_id}] Client connected (slot acquired)")
        
        # Create FFmpeg process for this client
        ffmpeg_process = self.create_ffmpeg_process(client_id)
        if not ffmpeg_process:
            self.current_client_id = None
            await websocket.close(1011, "Failed to start audio pipeline")
            return
            
        self.active_connections[client_id] = ffmpeg_process
        
        try:
            async for message in websocket:
                if isinstance(message, bytes) and ffmpeg_process.stdin:
                    try:
                        # Check if FFmpeg is still running before writing
                        if ffmpeg_process.poll() is not None:
                            logger.warning(f"[{client_id}] FFmpeg exited with code {ffmpeg_process.returncode}, restarting...")
                            self.cleanup_ffmpeg(client_id)
                            await asyncio.sleep(0.1)
                            ffmpeg_process = self.create_ffmpeg_process(client_id)
                            if ffmpeg_process:
                                self.active_connections[client_id] = ffmpeg_process
                            else:
                                break
                        
                        ffmpeg_process.stdin.write(message)
                        ffmpeg_process.stdin.flush()
                        
                        # Log every 50 messages to reduce spam
                        self.msg_count[client_id] = self.msg_count.get(client_id, 0) + 1
                        if self.msg_count[client_id] % 50 == 1:
                            logger.info(f"[{client_id}] Audio flowing ({self.msg_count[client_id]} chunks, last {len(message)}B)")
                    except BrokenPipeError:
                        logger.warning(f"[{client_id}] FFmpeg pipe broken, restarting...")
                        self.cleanup_ffmpeg(client_id)
                        await asyncio.sleep(0.1)  # Small delay to prevent restart storm
                        ffmpeg_process = self.create_ffmpeg_process(client_id)
                        if ffmpeg_process:
                            self.active_connections[client_id] = ffmpeg_process
                        else:
                            break
                else:
                    logger.warning(f"[{client_id}] Skipping non-bytes message or FFmpeg stdin closed")
                            
        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"[{client_id}] Connection closed: {e.code}")
        except Exception as e:
            logger.error(f"[{client_id}] Error: {e}")
        finally:
            self.cleanup_ffmpeg(client_id)
            self.msg_count.pop(client_id, None)
            self.current_client_id = None  # Release the slot
            logger.info(f"[{client_id}] Client disconnected (slot released)")
    
    async def start(self):
        """Start the WebSocket server"""
        logger.info(f"Starting Audio Backchannel Server on port {self.port}")
        logger.info(f"Audio output device: {self.audio_device}")
        
        async with websockets.serve(
            self.handle_client,
            "0.0.0.0",
            self.port,
            ping_interval=30,
            ping_timeout=10
        ):
            logger.info(f"Server ready at ws://0.0.0.0:{self.port}")
            await asyncio.Future()  # Run forever
    
    def stop(self):
        """Stop the server and cleanup"""
        self.running = False
        for client_id in list(self.active_connections.keys()):
            self.cleanup_ffmpeg(client_id)
        logger.info("Server stopped")


def main():
    parser = argparse.ArgumentParser(description='Audio Backchannel Server')
    parser.add_argument('--port', type=int, default=8080, help='WebSocket port (default: 8080)')
    parser.add_argument('--audio-device', type=str, default='default', 
                        help='ALSA audio device (default: default). Use "aplay -L" to list devices')
    args = parser.parse_args()
    
    server = AudioBackchannelServer(port=args.port, audio_device=args.audio_device)
    
    # Handle graceful shutdown
    def signal_handler(sig, frame):
        logger.info("Shutdown signal received")
        server.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run server
    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()