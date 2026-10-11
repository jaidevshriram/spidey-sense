"""Bluetooth check for El Jefe: scan, connect, find dots, flash LEDs green (no vibration).

    python3 haptics/ble_test.py    # run from Terminal (macOS asks for Bluetooth once)
"""
import asyncio
import sys
import time

from bleak import BleakScanner

from dots.ble import NUS_SERVICE, discover_ble_dots


async def scan():
    found = await BleakScanner.discover(timeout=6.0, return_adv=True)
    print(f"scan: {len(found)} BLE devices")
    for addr, (d, adv) in sorted(found.items(), key=lambda kv: -kv[1][1].rssi):
        name = d.name or adv.local_name or ""
        nus = NUS_SERVICE in [u.lower() for u in adv.service_uuids]
        if name or nus:
            print(f"  {addr} rssi={adv.rssi:4} name={name!r}{'  <-- Nordic UART' if nus else ''}")


asyncio.run(scan())
link, dots = discover_ble_dots(4, name_hint=sys.argv[1] if len(sys.argv) > 1 else "DataFeelCE")
print(f"dots over BLE: {len(dots)}")
from datafeel.device import LedMode
for d in dots:
    t0 = time.perf_counter()
    d.registers.set_led_mode(LedMode.GLOBAL_MANUAL)
    d.registers.set_global_led(0, 255, 0)
    print(f"  dot {d.id}: LED green, sent in {(time.perf_counter() - t0) * 1000:.0f} ms;",
          "skin temp", round(d.registers.get_skin_temperature(), 1))
time.sleep(2)
for d in dots:
    d.set_led_off()
link.shutdown()
print("BLE TEST DONE")
