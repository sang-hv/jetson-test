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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

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
        path = urlsplit(self.path).path
        if path != "/update":
            self._respond(404, {"error": "not found"})
            return

        # --- Parse body ---
        content_type = (self.headers.get("Content-Type") or "").lower()
        version = ""

        if "application/json" in content_type:
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, ValueError):
                self._respond(400, {"error": "invalid JSON body"})
                return
            version = str(body.get("version", "") or "").strip()

        elif "multipart/form-data" in content_type:
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                marker = b'name="version"'
                i = raw.find(marker)
                if i != -1:
                    # Very small multipart parser: extract the first value for "version".
                    # Supports curl --form 'version="..."'
                    after = raw[i + len(marker):]
                    # headers end with \r\n\r\n; content ends at next \r\n--boundary
                    h_end = after.find(b"\r\n\r\n")
                    if h_end != -1:
                        content = after[h_end + 4 :]
                        v_end = content.find(b"\r\n")
                        if v_end != -1:
                            version = content[:v_end].decode("utf-8", errors="replace").strip()
            except Exception:
                self._respond(400, {"error": "invalid multipart form body"})
                return

        elif "application/x-www-form-urlencoded" in content_type:
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length).decode("utf-8", errors="replace")
                version = (parse_qs(raw).get("version", [""])[0] or "").strip()
            except ValueError:
                self._respond(400, {"error": "invalid form body"})
                return

        else:
            self._respond(
                400,
                {"error": "unsupported Content-Type (use JSON or form field 'version')"},
            )
            return

        if not version:
            self._respond(400, {"error": "missing 'version' field"})
            return

        # --- Check lock ---
        if _is_update_running():
            self._respond(409, {"error": "update already in progress"})
            return

        # --- Spawn update in background ---
        log.info("Update requested: version=%s", version)

        try:
            # Spawn a separate process (not a thread). This ensures the update keeps
            # running even if device-update-server is restarted during deploy.
            subprocess.Popen(
                ["/bin/bash", UPDATE_SCRIPT, version],
                start_new_session=True,
            )
        except Exception as exc:
            log.error("Failed to spawn update script: %s", exc)
            self._respond(500, {"error": "failed to spawn update"})
            return

        self._respond(200, {
            "status": "accepted",
            "version": version,
            "message": "update started in background",
        })

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/health":
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
