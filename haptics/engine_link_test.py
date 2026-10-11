"""Hardware check of the engine's haptics path over Bluetooth, through the app WebSocket:
El Jefe health check, link ping (+ green flash), one LED change, and ONE low-intensity (0.3) urgent buzz.

    ./scripts/engine_link_test.sh     # from Terminal.app (Bluetooth)
"""
import asyncio
import json
import threading
import time
from pathlib import Path

import websockets

from spidey.engine import ROOT, load_config, run

PORT = 8798
cfg = load_config(ROOT / "configs" / "config.yaml")
cfg.update(sink={"type": "datafeel-ble", "ble_name": "DataFeelCE"}, server={"enabled": True, "port": PORT},
           log="logs/engine_link_test.jsonl", rules_file="rules.engine_link_test.json",
           detector={"type": "fake", "appear": 999, "gone": 999, "period": 1000})   # no rule-triggered buzzes
threading.Thread(target=run, args=(cfg, 40), daemon=True).start()


async def main():
    for _ in range(60):   # wait for BLE connect + server
        try:
            ws = await websockets.connect(f"ws://127.0.0.1:{PORT}")
            break
        except OSError:
            await asyncio.sleep(0.5)
    async with ws:
        hello = json.loads(await ws.recv())
        await asyncio.sleep(2.5)   # let the first 2 s health check run

        async def req(m):
            await ws.send(json.dumps(m))
            while True:
                r = json.loads(await ws.recv())
                if r["type"] in ("ack", "link.pong"):
                    return r

        async def status():
            while True:
                r = json.loads(await ws.recv())
                if r["type"] == "device.status":
                    return [d for d in r["devices"] if d["id"] == "el_jefe"][0]
        j = await status()
        print(f"1) device.status el_jefe: {j['status']} | {j['detail']} | rssi {j['link'].get('rssi')} dBm")
        pings = []
        for i in range(5):
            t0 = time.time()
            p = await req({"type": "link.ping", "id": i, "t_app": t0, "flash": i == 0})
            rtt = (time.time() - t0) * 1000
            pings.append((rtt, p["ble_ms"]))
        print("2) link.ping x5 (first with green flash): "
              + ", ".join(f"rtt {r:.0f} ms / ble {b:.0f} ms" for r, b in pings))
        await asyncio.sleep(0.8)
        r = await req({"type": "haptic.led", "mode": "global", "rgb": [0, 0, 255]})
        print(f"3) LED blue: ok={r['ok']} {r['ms']} ms"); await asyncio.sleep(1)
        r = await req({"type": "command", "name": "set_intensity", "args": {"value": 0.3}})
        print(f"4) set_intensity 0.3: {r.get('detail')}")
        r = await req({"type": "haptic.preset", "name": "urgent"})
        print(f"5) urgent @0.3 (STRONG_CLICK_P30 + BUZZ5_P20): ok={r['ok']} {r['ms']} ms"); await asyncio.sleep(2)
        r = await req({"type": "haptic.stop_all"})
        print(f"6) stop_all: ok={r['ok']} {r['ms']} ms")
    print("ENGINE LINK TEST DONE")

asyncio.run(main())
