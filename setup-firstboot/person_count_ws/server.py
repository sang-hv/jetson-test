#!/usr/bin/env python3
"""
Person-count WebSocket server.

- Subscribes to ZMQ PUB/SUB topic "person_count" (JSON payload).
- Broadcasts updates to all connected WebSocket clients.

Usage: python3 server.py [--port 8090] [--zmq tcp://localhost:5555]
Requires: pip3 install websockets pyzmq
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from typing import Any

try:
    import websockets
    from websockets.server import WebSocketServerProtocol
except ImportError:
    print("ERROR: pip3 install websockets")
    sys.exit(1)

try:
    import zmq
    import zmq.asyncio
except ImportError:
    print("ERROR: pip3 install pyzmq")
    sys.exit(1)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("person-count-ws")


class PersonCountWSServer:
    def __init__(self, zmq_sub_address: str, zmq_topic: bytes, port: int) -> None:
        self.zmq_sub_address = zmq_sub_address
        self.zmq_topic = zmq_topic
        self.port = port

        self._clients: set[WebSocketServerProtocol] = set()
        self._clients_lock = asyncio.Lock()
        self._latest: dict[str, Any] | None = None

        self._ctx = zmq.asyncio.Context.instance()
        self._sub = self._ctx.socket(zmq.SUB)

        self._broadcast_task: asyncio.Task | None = None
        self._zmq_task: asyncio.Task | None = None
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)

    async def _zmq_loop(self) -> None:
        self._sub.connect(self.zmq_sub_address)
        self._sub.setsockopt(zmq.SUBSCRIBE, self.zmq_topic)
        log.info('ZMQ SUB connected to %s topic="%s"', self.zmq_sub_address, self.zmq_topic.decode("utf-8"))

        while True:
            parts = await self._sub.recv_multipart()
            if len(parts) < 2:
                continue
            topic, payload = parts[0], parts[1]
            if topic != self.zmq_topic:
                continue

            try:
                raw = payload.decode("utf-8")
                data = json.loads(raw)
            except Exception as exc:
                log.warning("Invalid ZMQ payload: %s", exc)
                continue

            msg = {
                "type": "person_count",
                "person_count": data.get("person_count"),
                "ts": time.time(),
            }
            self._latest = msg

            # Drop oldest if queue is full (prefer newest)
            if self._queue.full():
                try:
                    _ = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                self._queue.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    async def _broadcast_loop(self) -> None:
        while True:
            msg = await self._queue.get()
            payload = json.dumps(msg, separators=(",", ":"), ensure_ascii=False)
            async with self._clients_lock:
                clients = list(self._clients)

            if not clients:
                continue

            dead: list[WebSocketServerProtocol] = []
            for ws in clients:
                try:
                    await ws.send(payload)
                except Exception:
                    dead.append(ws)

            if dead:
                async with self._clients_lock:
                    for ws in dead:
                        self._clients.discard(ws)

    async def _handle_client(self, ws: WebSocketServerProtocol) -> None:
        async with self._clients_lock:
            self._clients.add(ws)
        client = f"{ws.remote_address[0]}:{ws.remote_address[1]}" if ws.remote_address else "unknown"
        log.info("[%s] connected", client)

        try:
            if self._latest is not None:
                await ws.send(json.dumps(self._latest, separators=(",", ":"), ensure_ascii=False))

            async for _ in ws:
                pass
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            async with self._clients_lock:
                self._clients.discard(ws)
            log.info("[%s] disconnected", client)

    async def run(self) -> None:
        self._zmq_task = asyncio.create_task(self._zmq_loop())
        self._broadcast_task = asyncio.create_task(self._broadcast_loop())

        log.info("WebSocket listening on ws://0.0.0.0:%d", self.port)
        async with websockets.serve(
            self._handle_client,
            "0.0.0.0",
            self.port,
            ping_interval=30,
            ping_timeout=10,
            max_size=2**20,
        ):
            await asyncio.Future()

    async def shutdown(self) -> None:
        for task in (self._broadcast_task, self._zmq_task):
            if task and not task.done():
                task.cancel()
        await asyncio.sleep(0)
        try:
            self._sub.close(linger=0)
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Person-count WebSocket server")
    parser.add_argument("--port", type=int, default=int(os.getenv("PERSON_COUNT_WS_PORT", "8090")))
    parser.add_argument("--zmq", dest="zmq_sub_address", default=os.getenv("ZMQ_SUB_ADDRESS", "tcp://localhost:5555"))
    args = parser.parse_args()

    server = PersonCountWSServer(
        zmq_sub_address=args.zmq_sub_address,
        zmq_topic=b"person_count",
        port=args.port,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _run() -> None:
        await server.run()

    def _stop(*_a: object) -> None:
        log.info("Shutdown requested")
        for t in asyncio.all_tasks(loop):
            t.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _stop)

    try:
        loop.run_until_complete(_run())
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        pass
    finally:
        try:
            loop.run_until_complete(server.shutdown())
        finally:
            loop.close()


if __name__ == "__main__":
    main()
