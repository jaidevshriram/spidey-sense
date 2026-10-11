"""WebSocket link to the phone app: broadcasts every haptic event and accepts app commands.

Full message list: docs/protocol.md. In short:
  server -> app: hello{history, rules, devices, settings}, event{...}, rules.list{rules},
                 device.status{devices}, settings{...}, ack{...}
  app -> server: rules.upsert{rule}, rules.delete{id}, feedback{...}, command{name, args}

The engine registers handlers with `on(type, fn)`. A handler gets the decoded message dict, runs
in a worker thread (so a slow sink can't stall the socket), and may return a dict to send back
to the sender only. `hello_extra` and `add_periodic` let the engine add data to hello and push
status on a timer.
"""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import traceback
from collections import deque
from typing import Callable


def lan_ip() -> str:
    """The Mac's address on the local network (no packets are sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def advertise_bonjour(port: int):
    """Announce the WebSocket as _spidey._tcp so the iPhone app finds the Mac with zero setup."""
    try:
        from zeroconf import ServiceInfo, Zeroconf
        host = socket.gethostname().split(".")[0]
        ip = lan_ip()
        info = ServiceInfo("_spidey._tcp.local.", f"Spidey Sense on {host}._spidey._tcp.local.",
                           addresses=[socket.inet_aton(ip)], port=port,
                           properties={"path": "/", "v": "1"}, server=f"{host}.local.")
        zc = Zeroconf()
        zc.register_service(info, allow_name_change=True)
        print(f"Bonjour: advertising _spidey._tcp on {ip}:{port}")
        return zc
    except Exception as e:  # discovery is a convenience; never block the engine
        print(f"Bonjour: not advertised ({type(e).__name__}: {e}); the app can still use a manual URL")
        return None


class EventServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8765, history: int = 50):
        self.host, self.port = host, port
        self.history: deque[dict] = deque(maxlen=history)
        self.clients: set = set()
        self.handlers: dict[str, Callable[[dict], dict | None]] = {}
        self.hello_extra: Callable[[], dict] | None = None   # extra keys merged into hello
        self.loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait(5)
        print(f"Event server: ws://{lan_ip()}:{port}  (the app finds it automatically via Bonjour)")
        self._zc = advertise_bonjour(port)

    # ---- engine-facing API -------------------------------------------------------------
    def on(self, msg_type: str, fn: Callable[[dict], dict | None]) -> None:
        """Route incoming app messages of `msg_type` to fn(msg). Return a dict to reply."""
        self.handlers[msg_type] = fn

    def add_periodic(self, every_s: float, make_msg: Callable[[], dict]) -> None:
        """Broadcast make_msg() every `every_s` seconds (e.g. device.status)."""
        async def tick():
            while True:
                await asyncio.sleep(every_s)
                try:
                    msg = await self.loop.run_in_executor(None, make_msg)
                    await self._send_all(json.dumps(msg))
                except Exception:
                    traceback.print_exc()

        self.loop.call_soon_threadsafe(lambda: self.loop.create_task(tick()))

    def broadcast(self, msg: dict) -> None:
        """Thread-safe: send any message to every connected app."""
        asyncio.run_coroutine_threadsafe(self._send_all(json.dumps(msg)), self.loop)

    def publish(self, event: dict) -> None:
        """Thread-safe: call from the engine loop."""
        self.history.append(event)
        self.broadcast({"type": "event", **event})

    # ---- internals ---------------------------------------------------------------------
    async def _send_all(self, msg: str) -> None:
        for ws in list(self.clients):
            try:
                await ws.send(msg)
            except Exception:
                self.clients.discard(ws)

    async def _dispatch(self, ws, raw) -> None:
        try:
            msg = json.loads(raw)
            kind = msg.get("type") if isinstance(msg, dict) else None
        except (ValueError, TypeError):
            await ws.send(json.dumps({"type": "error", "error": "invalid JSON"}))
            return
        fn = self.handlers.get(kind)
        if fn is None:
            await ws.send(json.dumps({"type": "error", "error": f"unknown message type {kind!r}"}))
            return
        try:
            reply = await self.loop.run_in_executor(None, fn, msg)
        except Exception as e:
            traceback.print_exc()
            reply = {"type": "error", "for": kind, "error": str(e)}
        if reply:
            await ws.send(json.dumps(reply))

    def _run(self):
        import websockets

        async def handler(ws):
            self.clients.add(ws)
            try:
                hello = {"type": "hello", "history": list(self.history)}
                if self.hello_extra:
                    try:
                        hello.update(await self.loop.run_in_executor(None, self.hello_extra))
                    except Exception:
                        traceback.print_exc()
                await ws.send(json.dumps(hello))
                async for raw in ws:
                    await self._dispatch(ws, raw)
            except websockets.ConnectionClosed:
                pass
            finally:
                self.clients.discard(ws)

        async def main():
            async with websockets.serve(handler, self.host, self.port):
                self._ready.set()
                await asyncio.Future()

        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(main())
