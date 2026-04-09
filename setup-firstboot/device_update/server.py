#!/usr/bin/env python3
"""
Device Update Server — receives OTA update requests from backend.

Backend calls this endpoint (via Cloudflare tunnel) to trigger a software update.
Auth is handled by Nginx auth_request → stream-auth (port 8091).
The server spawns the update script in background and returns 200 immediately.

Request:
  POST /update
  { "version": "feature/branch-name" }

Response:
  200 — update accepted, running in background
  400 — bad request
  409 — update already in progress

Usage: python3 server.py [--port 8092]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("device-update-server")

UPDATE_SCRIPT = os.getenv("UPDATE_SCRIPT", "/opt/device/run-update.sh")
LOCK_FILE = Path("/tmp/device-update.lock")


def _is_update_running() -> bool:
    """Check if an update is already in progress."""
    if not LOCK_FILE.exists():
        return False
    try:
        pid = int(LOCK_FILE.read_text().strip())
        os.kill(pid, 0)  # Check if process is alive
        return True
    except (ValueError, OSError):
        LOCK_FILE.unlink(missing_ok=True)
        return False


class UpdateHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt: str, *args: object) -> None:
        log.info(fmt, *args)

    def do_POST(self) -> None:
        if self.path != "/update":
            self._respond(404, {"error": "not found"})
            return

        # --- Parse body ---
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError):
            self._respond(400, {"error": "invalid JSON body"})
            return

        version = body.get("version", "")

        if not version:
            self._respond(400, {"error": "missing 'version' field"})
            return

        # --- Check lock ---
        if _is_update_running():
            self._respond(409, {"error": "update already in progress"})
            return

        # --- Spawn update in background ---
        log.info("Update requested: version=%s", version)

        def _run_update():
            try:
                subprocess.run(
                    ["/bin/bash", UPDATE_SCRIPT, version],
                    timeout=3600,  # 1 hour max
                )
            except subprocess.TimeoutExpired:
                log.error("Update script timed out after 1 hour")
            except Exception as exc:
                log.error("Update script failed: %s", exc)

        thread = threading.Thread(target=_run_update, daemon=True)
        thread.start()

        self._respond(200, {
            "status": "accepted",
            "version": version,
            "message": "update started in background",
        })

    def do_GET(self) -> None:
        if self.path == "/health":
            updating = _is_update_running()
            self._respond(200, {
                "status": "updating" if updating else "idle",
            })
            return
        self._respond(405, {"error": "method not allowed"})

    def _respond(self, status: int, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Device update server")
    parser.add_argument("--port", type=int,
                        default=int(os.getenv("UPDATE_SERVER_PORT", "8092")))
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), UpdateHandler)
    log.info("Device update server listening on http://127.0.0.1:%d", args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
