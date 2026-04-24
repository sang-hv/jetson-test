#!/usr/bin/env python3
"""
Stream Auth — Nginx auth_request backend.

Token format (base64url-encoded JSON):
  base64url({ "payload": { camera_id, start_time, time_exp, exp }, "signature": hexdigest })

Validation:
  1. base64url-decode token → extract payload + signature
  2. Recompute HMAC-SHA256(json.dumps(payload, sort_keys=True), secret_key).hexdigest()
  3. Compare signatures (constant-time)
  4. Check payload.camera_id == DEVICE_ID
  5. Check payload.time_exp has not passed

Responds:
  200 OK           — token valid
  401 Unauthorized — missing / expired / invalid signature
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
import threading
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("stream-auth")

DB_PATH = os.getenv("AUTH_DB_PATH", "/data/mini-pc/db/logic_service.db")
DEVICE_ENV_PATH = os.getenv("DEVICE_ENV_PATH", "/etc/device/device.env")

# TTL (seconds) for caching denied results.
# Valid tokens are cached until their own time_exp.
DENY_CACHE_TTL = int(os.getenv("DENY_CACHE_TTL", "60"))


# ---------------------------------------------------------------------------
# Token cache  (thread-safe, TTL-based)
# ---------------------------------------------------------------------------

class _TokenCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: dict[str, tuple[int, str, float]] = {}
        self._set_count = 0

    def get(self, token: str) -> tuple[int, str] | None:
        now = datetime.now(tz=timezone.utc).timestamp()
        with self._lock:
            entry = self._store.get(token)
            if entry is None:
                return None
            status, reason, expires_at = entry
            if now > expires_at:
                del self._store[token]
                return None
            return status, reason

    def set(self, token: str, status: int, reason: str, expires_at: float) -> None:
        with self._lock:
            self._store[token] = (status, reason, expires_at)
            self._set_count += 1
            if self._set_count % 200 == 0:
                now = datetime.now(tz=timezone.utc).timestamp()
                self._store = {k: v for k, v in self._store.items() if v[2] > now}


_cache = _TokenCache()


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_device_id() -> str:
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
    try:
        db = sqlite3.connect(DB_PATH, check_same_thread=False)
        row = db.execute(
            "SELECT value FROM camera_settings WHERE key = 'stream_secret_key' LIMIT 1"
        ).fetchone()
        db.close()
        return row[0] if row else ""
    except Exception as exc:
        log.error("Cannot read secret key from DB: %s", exc)
    return ""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_token(token: str, device_id: str, secret_key: str) -> tuple[int, str]:
    """Returns (http_status_code, reason). Results are cached."""
    cached = _cache.get(token)
    if cached is not None:
        return cached

    now = datetime.now(tz=timezone.utc)

    # --- 1. Decode base64url ---
    try:
        padding = 4 - len(token) % 4
        raw = base64.urlsafe_b64decode(token + "=" * (padding % 4))
        token_data = json.loads(raw)
        payload: dict = token_data["payload"]
        received_hex: str = token_data["signature"]
    except Exception:
        result = (401, "token decode error")
        _cache.set(token, *result, now.timestamp() + DENY_CACHE_TTL)
        return result

    # --- 2. Recompute HMAC (signed on camera_id only) ---
    try:
        message = payload["camera_id"].encode("utf-8")
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
            return 401, f"token expired at {time_exp_str}"
        # Valid — cache until time_exp
        _cache.set(token, 200, "ok", time_exp.timestamp())
        return 200, "ok"
    except (KeyError, ValueError) as exc:
        result = (401, f"time_exp parse error: {exc}")
        _cache.set(token, *result, now.timestamp() + DENY_CACHE_TTL)
        return result


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class _ConfigHolder:
    """Holds device_id and secret_key, auto-reloads from DB every RELOAD_INTERVAL."""

    RELOAD_INTERVAL = 60  # seconds

    def __init__(self) -> None:
        self.device_id: str = ""
        self.secret_key: str = ""
        self._last_reload: float = 0
        self._lock = threading.Lock()

    def load(self) -> None:
        """Force load from disk/DB."""
        self.device_id = _load_device_id()
        self.secret_key = _load_secret_key()
        self._last_reload = datetime.now(tz=timezone.utc).timestamp()
        log.info("Config loaded: device_id=%s", self.device_id or "(empty)")

    def get(self) -> tuple[str, str]:
        """Return (device_id, secret_key), reload if stale."""
        now = datetime.now(tz=timezone.utc).timestamp()
        if now - self._last_reload > self.RELOAD_INTERVAL:
            with self._lock:
                # Double-check after acquiring lock
                if now - self._last_reload > self.RELOAD_INTERVAL:
                    old_key = self.secret_key
                    self.device_id = _load_device_id()
                    self.secret_key = _load_secret_key()
                    self._last_reload = now
                    if self.secret_key != old_key:
                        log.info("secret_key changed — clearing token cache")
                        _cache.__init__()
        return self.device_id, self.secret_key


_config = _ConfigHolder()


class AuthHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        original_uri = self.headers.get("X-Original-URI", self.path)
        try:
            qs = parse_qs(urlparse(original_uri).query)
            token_list = qs.get("token", [])
        except Exception:
            token_list = []

        token = token_list[0] if token_list else ""

        # Fallback: allow token from cookie for clients that don't preserve query
        # params across HLS playlist/segment requests (common on iOS).
        if not token:
            try:
                cookie_header = self.headers.get("Cookie", "")
                if cookie_header:
                    jar = SimpleCookie()
                    jar.load(cookie_header)
                    morsel = jar.get("stream_token")
                    if morsel is not None:
                        token = morsel.value
            except Exception:
                token = ""

        if not token:
            self._respond(401, "missing token")
            return

        device_id, secret_key = _config.get()
        status, reason = _validate_token(token, device_id, secret_key)
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

    _config.load()

    if not _config.device_id:
        log.error("DEVICE_ID not found — requests will be rejected with 403")
    if not _config.secret_key:
        log.error("stream_secret_key not found in DB — requests will be rejected with 401")

    log.info("device_id=%s  db=%s  reload_interval=%ds",
             _config.device_id or "(empty)", DB_PATH, _config.RELOAD_INTERVAL)

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
