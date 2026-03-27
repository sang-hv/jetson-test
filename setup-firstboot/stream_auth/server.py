#!/usr/bin/env python3
"""
Stream Auth — Nginx auth_request backend.

Validates the HMAC token passed as ?token=<base64url> on every protected
request (livestream WebSocket, backchannel, detection WS).

Validation steps:
  1. Decode base64url token → {payload, signature}
  2. Recompute HMAC-SHA256(json.dumps(payload, sort_keys=True), secret_key)
  3. Compare with stored signature (constant-time)
  4. Check payload.camera_id == DEVICE_ID from /etc/device/device.env
  5. Check payload.time_exp has not passed (timezone-aware)

Responds:
  200 OK          — token valid, Nginx forwards request
  401 Unauthorized — token missing / expired / bad signature
  403 Forbidden    — camera_id mismatch

Usage: python3 server.py [--port 8091]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("stream-auth")

DB_PATH = os.getenv("AUTH_DB_PATH", "/data/mini-pc/db/logic_service.db")
DEVICE_ENV_PATH = os.getenv("DEVICE_ENV_PATH", "/etc/device/device.env")

# TTL (seconds) for caching denied results (bad sig / camera mismatch).
# Valid tokens are cached until their own time_exp.
DENY_CACHE_TTL = int(os.getenv("DENY_CACHE_TTL", "60"))


# ---------------------------------------------------------------------------
# Token cache  (thread-safe, TTL-based)
# ---------------------------------------------------------------------------

class _TokenCache:
    """
    Maps SHA256(token) → (status, reason, expires_at).

    - Valid tokens   : cached until payload time_exp
    - Denied tokens  : cached for DENY_CACHE_TTL seconds
    - Expired entries are pruned lazily on every get/set
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # key: str (hex digest)  →  value: (status: int, reason: str, expires_at: float)
        self._store: dict[str, tuple[int, str, float]] = {}
        self._set_count = 0

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def get(self, token: str) -> tuple[int, str] | None:
        key = self._key(token)
        now = datetime.now(tz=timezone.utc).timestamp()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            status, reason, expires_at = entry
            if now > expires_at:
                del self._store[key]
                return None
            return status, reason

    def set(self, token: str, status: int, reason: str, expires_at: float) -> None:
        key = self._key(token)
        with self._lock:
            self._store[key] = (status, reason, expires_at)
            self._set_count += 1
            # Prune expired entries every 200 insertions
            if self._set_count % 200 == 0:
                now = datetime.now(tz=timezone.utc).timestamp()
                self._store = {k: v for k, v in self._store.items() if v[2] > now}


_cache = _TokenCache()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_device_id() -> str:
    """Read DEVICE_ID from /etc/device/device.env."""
    try:
        with open(DEVICE_ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line.startswith("DEVICE_ID="):
                    value = line.split("=", 1)[1].strip()
                    if value:
                        return value
    except OSError as exc:
        log.error("Cannot read %s: %s", DEVICE_ENV_PATH, exc)
    return ""


def _load_secret_key() -> str:
    """Read stream_secret_key from SQLite DB."""
    try:
        db = sqlite3.connect(DB_PATH, check_same_thread=False)
        row = db.execute(
            "SELECT value FROM camera_settings WHERE key = 'stream_secret_key' LIMIT 1"
        ).fetchone()
        db.close()
        if row:
            return row[0]
    except Exception as exc:
        log.error("Cannot read secret key from DB: %s", exc)
    return ""


def _validate_token(token: str, device_id: str, secret_key: str) -> tuple[int, str]:
    """
    Returns (http_status_code, reason).
    Results are cached:
      - 200 until token's own time_exp
      - 401/403 for DENY_CACHE_TTL seconds
    """
    # --- Cache lookup ---
    cached = _cache.get(token)
    if cached is not None:
        return cached

    now = datetime.now(tz=timezone.utc)

    # --- 1. Decode base64url (add padding as needed) ---
    try:
        padding = 4 - len(token) % 4
        raw = base64.urlsafe_b64decode(token + "=" * (padding % 4))
        token_data = json.loads(raw)
    except Exception:
        result = (401, "token decode error")
        _cache.set(token, *result, now.timestamp() + DENY_CACHE_TTL)
        return result

    try:
        payload: dict = token_data["payload"]
        received_hex: str = token_data["signature"]
    except (KeyError, Exception):
        result = (401, "token structure invalid")
        _cache.set(token, *result, now.timestamp() + DENY_CACHE_TTL)
        return result

    # --- 2. Verify HMAC-SHA256 ---
    try:
        message = json.dumps(payload, sort_keys=True).encode("utf-8")
        expected_hex = hmac.new(
            secret_key.encode("utf-8"),
            message,
            hashlib.sha256,
        ).hexdigest()
    except Exception:
        result = (401, "hmac computation error")
        _cache.set(token, *result, now.timestamp() + DENY_CACHE_TTL)
        return result

    if not hmac.compare_digest(received_hex, expected_hex):
        result = (401, "invalid signature")
        _cache.set(token, *result, now.timestamp() + DENY_CACHE_TTL)
        return result

    # --- 3. camera_id check ---
    if payload.get("camera_id") != device_id:
        result = (403, f"camera_id mismatch: {payload.get('camera_id')} != {device_id}")
        _cache.set(token, *result, now.timestamp() + DENY_CACHE_TTL)
        return result

    # --- 4. Expiry check ---
    try:
        time_exp_str: str = payload["time_exp"]
        time_exp = datetime.fromisoformat(time_exp_str)
        if time_exp.tzinfo is None:
            time_exp = time_exp.replace(tzinfo=timezone.utc)
        if now > time_exp:
            # Expired — do NOT cache, let client retry with fresh token
            return 401, f"token expired at {time_exp_str}"
    except (KeyError, ValueError) as exc:
        result = (401, f"time_exp parse error: {exc}")
        _cache.set(token, *result, now.timestamp() + DENY_CACHE_TTL)
        return result

    # --- Valid: cache until time_exp ---
    _cache.set(token, 200, "ok", time_exp.timestamp())
    return 200, "ok"


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class AuthHandler(BaseHTTPRequestHandler):
    # Populated by the server after startup
    device_id: str = ""
    secret_key: str = ""

    def log_message(self, fmt: str, *args: object) -> None:  # suppress default access log
        pass

    def do_GET(self) -> None:
        # Nginx passes the original URI (including query string) via header
        original_uri = self.headers.get("X-Original-URI", self.path)

        try:
            qs = parse_qs(urlparse(original_uri).query)
            token_list = qs.get("token", [])
        except Exception:
            token_list = []

        if not token_list:
            self._respond(401, "missing token")
            return

        token = token_list[0]
        status, reason = _validate_token(token, self.device_id, self.secret_key)

        if status != 200:
            log.warning("Auth denied [%d] %s — %s", status, original_uri, reason)
        self._respond(status, reason)

    def _respond(self, status: int, reason: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(reason.encode())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Stream auth server for Nginx auth_request")
    parser.add_argument("--port", type=int, default=int(os.getenv("STREAM_AUTH_PORT", "8091")))
    args = parser.parse_args()

    device_id = _load_device_id()
    secret_key = _load_secret_key()

    if not device_id:
        log.error("DEVICE_ID not found — requests will be rejected with 403")
    if not secret_key:
        log.error("stream_secret_key not found in DB — requests will be rejected with 401")

    log.info("device_id=%s  db=%s", device_id or "(empty)", DB_PATH)

    AuthHandler.device_id = device_id
    AuthHandler.secret_key = secret_key

    server = ThreadingHTTPServer(("127.0.0.1", args.port), AuthHandler)
    log.info("Stream auth listening on http://127.0.0.1:%d/verify", args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
