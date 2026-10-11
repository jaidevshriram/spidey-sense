"""Record the demo page (http://localhost:8096/demo) to an MP4 in the background, independent of the screen.

    python3 tools/record_demo.py [--url URL] [--fps 15] [--size 1920x1080]   -> logs/recordings/demo-<time>.mp4
Stop with Ctrl-C / SIGTERM (the file is finalized cleanly). Headless Chrome + DevTools screencast -> latest frame
re-emitted at a constant fps -> ffmpeg (H.264). Uses only websockets + the system Chrome + ffmpeg.
"""
import argparse
import asyncio
import base64
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[1]
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8096/demo")
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--out", default=None)
    ap.add_argument("--port", type=int, default=9333)
    a = ap.parse_args()
    w, h = (int(v) for v in a.size.split("x"))
    out = Path(a.out) if a.out else ROOT / "logs" / "recordings" / f"demo-{time.strftime('%Y%m%d-%H%M%S')}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    prof = tempfile.mkdtemp(prefix="spidey-rec-")
    chrome = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={a.port}", f"--user-data-dir={prof}",
                               "--hide-scrollbars", "--mute-audio", f"--window-size={w},{h}", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws_url = None
    for _ in range(50):
        try:
            tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{a.port}/json"))
            ws_url = next(t["webSocketDebuggerUrl"] for t in tabs if t.get("type") == "page")
            break
        except Exception:
            await asyncio.sleep(0.2)
    if not ws_url:
        sys.exit("Chrome DevTools didn't come up")

    ff = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "image2pipe", "-c:v", "mjpeg", "-r", str(a.fps),
                           "-i", "-", "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                           f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2", "-c:v", "libx264", "-preset", "veryfast",
                           "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
                          stdin=subprocess.PIPE)
    latest = {"jpg": None}
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    async with websockets.connect(ws_url, max_size=None) as ws:
        n = 0

        async def send(method, params=None):
            nonlocal n
            n += 1
            await ws.send(json.dumps({"id": n, "method": method, "params": params or {}}))

        await send("Emulation.setDeviceMetricsOverride", {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})
        await send("Page.enable")
        await send("Page.navigate", {"url": a.url})
        await asyncio.sleep(1.5)
        await send("Page.startScreencast", {"format": "jpeg", "quality": 88, "maxWidth": w, "maxHeight": h,
                                             "everyNthFrame": 1})

        async def reader():
            async for raw in ws:
                m = json.loads(raw)
                if m.get("method") == "Page.screencastFrame":
                    p = m["params"]
                    latest["jpg"] = base64.b64decode(p["data"])
                    await send("Page.screencastFrameAck", {"sessionId": p["sessionId"]})

        async def writer():
            t0, k = time.time(), 0
            while not stop.is_set():
                if latest["jpg"]:
                    ff.stdin.write(latest["jpg"])
                    k += 1
                    if k % (a.fps * 30) == 0:
                        print(f"recording… {k / a.fps:.0f} s", flush=True)
                await asyncio.sleep(max(0.0, t0 + k / a.fps - time.time()) if latest["jpg"] else 0.1)

        print(f"Recording {a.url} at {w}x{h} {a.fps} fps -> {out}  (stop: Ctrl-C / kill -TERM {os.getpid()})", flush=True)
        rt = asyncio.create_task(reader())
        await writer()
        rt.cancel()
    ff.stdin.close()
    ff.wait()
    chrome.terminate()
    shutil.rmtree(prof, ignore_errors=True)
    print(f"Saved {out} ({out.stat().st_size / 1e6:.1f} MB)", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
