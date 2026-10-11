"""El Jefe over Bluetooth LE, so the dots work with no USB cable.

DataFeel's own C# SDK (Datafeel.NET.BLE -> CallAndResponse.Transport.Ble) talks to El Jefe through
the Nordic UART Service and sends the same Modbus RTU frames it sends over USB serial:
    service 6e400001-..., write to RX 6e400002-..., replies notify on TX 6e400003-...

BleSerial is a tiny pyserial look-alike over that link. Registering it with minimalmodbus under the
port name "ble" makes the stock datafeel.device.Dot("ble", address) work unchanged.

macOS note: the process that touches Bluetooth must belong to an app with Bluetooth permission
(Terminal / iTerm is fine; it asks once). Run from Terminal, e.g. `python3 backend/run.py --sink datafeel-ble`.
"""
from __future__ import annotations

import asyncio
import threading
import time

NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"   # we write here
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"   # El Jefe notifies here
PORT = "ble"


class BleSerial:
    """Just enough of serial.Serial for minimalmodbus: write, read(n) with timeout, buffers."""

    def __init__(self, address: str | None = None, name_hint: str | None = None,
                 scan_timeout: float = 10.0, timeout: float = 0.5):
        self.port = PORT
        self.timeout = timeout
        self.baudrate, self.bytesize, self.parity, self.stopbits = 115200, 8, "N", 1
        self._buf = bytearray()
        self._cv = threading.Condition()
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        self.device = self._run(self._connect(address, name_hint, scan_timeout), scan_timeout + 15)

    # -- asyncio side ------------------------------------------------------
    def _run(self, coro, timeout):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    async def _connect(self, address, name_hint, scan_timeout):
        from bleak import BleakClient, BleakScanner

        if address:
            dev = await BleakScanner.find_device_by_address(address, timeout=scan_timeout)
        else:
            def match(d, adv):
                name = (d.name or adv.local_name or "").lower()
                if name_hint:  # exact name: several El Jefes can be in range at an event
                    return name == name_hint.lower()
                return NUS_SERVICE in [u.lower() for u in adv.service_uuids] or \
                    any(k in name for k in ("datafeel", "jefe"))
            dev = await BleakScanner.find_device_by_filter(match, timeout=scan_timeout)
        if dev is None:
            raise RuntimeError("No El Jefe found over Bluetooth. Is it on (battery LED red) and "
                               "not connected to another computer/app?")
        self._client = BleakClient(dev, disconnected_callback=lambda c: print("[ble] disconnected"))
        await self._client.connect()
        await self._client.start_notify(NUS_TX, self._on_notify)
        ch = self._client.services.get_characteristic(NUS_RX)
        self._no_rsp = "write-without-response" in ch.properties
        self._chunk = max(20, (self._client.mtu_size or 23) - 3)
        print(f"[ble] connected to {dev.name} {dev.address} (mtu chunk {self._chunk})")
        return dev

    def _on_notify(self, _char, data: bytearray):
        with self._cv:
            self._buf.extend(data)
            self._cv.notify_all()

    async def _write(self, data: bytes):
        for i in range(0, len(data), self._chunk):
            await self._client.write_gatt_char(NUS_RX, data[i:i + self._chunk],
                                               response=not self._no_rsp)

    # -- pyserial surface used by minimalmodbus -----------------------------
    @property
    def is_open(self) -> bool:
        return self._client.is_connected

    def open(self):
        pass

    def close(self):
        pass  # minimalmodbus may call this between calls; keep the link up

    def shutdown(self):
        try:
            self._run(self._client.disconnect(), 5)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def write(self, data: bytes) -> int:
        self._run(self._write(bytes(data)), 5)
        return len(data)

    def read(self, n: int) -> bytes:
        deadline = time.time() + (self.timeout or 0)
        with self._cv:
            while len(self._buf) < n:
                left = deadline - time.time()
                if left <= 0:
                    break
                self._cv.wait(left)
            out, self._buf = bytes(self._buf[:n]), self._buf[n:]
        return out

    @property
    def in_waiting(self) -> int:
        return len(self._buf)

    def reset_input_buffer(self):
        with self._cv:
            self._buf.clear()

    def reset_output_buffer(self):
        pass

    def flush(self):
        pass


def open_ble(address: str | None = None, name_hint: str | None = None) -> BleSerial:
    """Connect and register the link with minimalmodbus as port "ble"."""
    import minimalmodbus

    link = BleSerial(address, name_hint)
    minimalmodbus._serialports[PORT] = link
    return link


def discover_ble_dots(max_address: int = 4, address: str | None = None,
                      name_hint: str | None = None):
    """Like datafeel.device.discover_devices, but over Bluetooth. Returns (link, dots)."""
    from datafeel.device import Dot

    link = open_ble(address, name_hint)
    dots = []
    for a in range(1, max_address + 1):
        try:
            dots.append(Dot(PORT, a))
            print(f"[ble] dot at address {a}")
        except Exception:
            print(f"[ble] no dot at address {a}")
    return link, dots
