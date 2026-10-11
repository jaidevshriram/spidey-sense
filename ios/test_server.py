"""Tiny fake Spidey Sense engine for testing the iOS app.

Usage: ../.venv/bin/python test_server.py [port]   (default 8765)
Sends {"type":"hello","history":[2 events]} on connect, then a new event every 3s.
"""
import asyncio
import itertools
import json
import random
import sys
import time

import websockets

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
SAMPLES = [
    ("urgent", "car", "left", False, "car approaching fast from the left, not looked at"),
    ("pulse", "bike", "behind", False, "bike closing in from behind"),
    ("ambient", "person", "right", True, "person nearby on the right, already seen"),
    ("urgent", "pole", "ahead", False, "obstacle directly ahead"),
]
ids = itertools.count(1)
clients = set()


def make_event():
    pattern, hazard, where, seen, reason = random.choice(SAMPLES)
    return {
        "id": next(ids),
        "ts": time.time(),
        "pattern": pattern,
        "dot": random.randint(0, 7),
        "rule": f"{pattern}_rule",
        "reason": reason,
        "detection": {"hazard": hazard, "where": where, "seen_by_user": seen,
                      "urgency": round(random.uniform(0.2, 1.0), 2)},
        "latency_ms": round(random.uniform(20, 120), 1),
    }


history = [make_event(), make_event()]


async def handler(ws):
    clients.add(ws)
    try:
        await ws.send(json.dumps({"type": "hello", "history": history[-50:]}))
        await ws.wait_closed()
    finally:
        clients.discard(ws)


async def ticker():
    while True:
        await asyncio.sleep(3)
        ev = make_event()
        history.append(ev)
        websockets.broadcast(clients, json.dumps({"type": "event", **ev}))
        print("sent", ev["id"], ev["pattern"], ev["reason"], flush=True)


async def main():
    async with websockets.serve(handler, "0.0.0.0", PORT):
        print(f"serving ws://0.0.0.0:{PORT}", flush=True)
        await ticker()


if __name__ == "__main__":
    asyncio.run(main())
