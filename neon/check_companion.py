"""Find the Neon Companion phone on Wi-Fi and measure the stream the engine will use.

    python3 neon/check_companion.py                     # auto-discover (phone + Mac on the same Wi-Fi)
    python3 neon/check_companion.py 192.168.1.23        # or the phone's IP (Companion app → stream screen)
    python3 neon/check_companion.py [ip] --view         # then a live window: scene + gaze dot + delay
    python3 neon/check_companion.py --mock --view       # no device: test the window with fake gaze

Reports: phone name/IP/battery, glasses connected, scene frame rate + resolution, gaze rate,
worn %, and latency (phone timestamp -> received on the Mac; assumes both clocks are NTP-synced).
"""
import sys
import time

args = [a for a in sys.argv[1:] if not a.startswith("--")]
VIEW, MOCK = "--view" in sys.argv, "--mock" in sys.argv
ip = args[0] if args else None


def view(get, seconds=None):
    """Scene with gaze dot and frame->Mac delay. q / Esc closes."""
    import cv2
    t_end = time.time() + seconds if seconds else None
    while not t_end or time.time() < t_end:
        img, gx, gy, worn, ts = get()
        if img is None:
            continue
        delay = (time.time() - ts) * 1000
        img = img.copy()
        if worn:
            cv2.circle(img, (int(gx), int(gy)), 30, (0, 0, 255), 5)
        cv2.putText(img, f"frame->Mac {delay:4.0f} ms  {'worn' if worn else 'NOT WORN'}", (30, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 3)
        cv2.imshow("Neon check (q to quit)", cv2.resize(img, None, fx=0.6, fy=0.6))
        if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
            break
    cv2.destroyAllWindows()


if MOCK:
    from neon_app import open_source
    src = open_source("mock", with_video=True)
    def get_mock():
        s = src.next()
        return s.frame, s.x, s.y, s.worn, s.t - 0.05   # pretend 50 ms network delay
    print("mock source: 30 Hz figure-8 gaze")
    if VIEW:
        view(get_mock, seconds=None if sys.stdin.isatty() else 3)
    print("NEON CHECK DONE (mock)")
    sys.exit(0)

from pupil_labs.realtime_api.simple import Device, discover_one_device  # noqa: E402

t0 = time.time()
if ip:
    dev = Device(address=ip, port=8080)
else:
    print("Searching for a Neon Companion on this Wi-Fi (10 s) ...")
    dev = discover_one_device(max_search_duration_seconds=10)
    if dev is None:
        sys.exit("No Companion found. Is the Neon app open, the phone on the SAME Wi-Fi as the Mac, "
                 "and is client isolation off on that network? Try passing the phone's IP.")
print(f"found {dev.phone_name} @ {dev.phone_ip} in {time.time() - t0:.1f} s · battery {dev.battery_level_percent}% "
      f"· glasses serial {dev.module_serial or 'NOT CONNECTED'}")

try:
    n, lat, worn, t_end, shape = 0, [], 0, time.time() + 5, None
    while time.time() < t_end:
        item = dev.receive_matched_scene_video_frame_and_gaze(timeout_seconds=2)
        if item is None:
            continue
        now = time.time()
        n += 1
        shape = item.frame.bgr_pixels.shape
        lat.append((now - item.frame.timestamp_unix_seconds) * 1000)
        worn += bool(item.gaze.worn)
    if not n:
        sys.exit("Connected, but no scene video arrived in 5 s. Are the glasses plugged into the phone?")
    lat.sort()
    print(f"scene+gaze: {n / 5:.1f} fps · {shape[1]}x{shape[0]} · worn {100 * worn / n:.0f}% · "
          f"latency median {lat[len(lat) // 2]:.0f} ms, p90 {lat[int(len(lat) * .9)]:.0f} ms")
    g = [dev.receive_gaze_datum(timeout_seconds=1) for _ in range(100)]
    g = [x for x in g if x]
    if len(g) > 1:
        rate = (len(g) - 1) / (g[-1].timestamp_unix_seconds - g[0].timestamp_unix_seconds)
        print(f"gaze only: {rate:.0f} Hz · last gaze px ({g[-1].x:.0f}, {g[-1].y:.0f})")
    if VIEW:
        def get_dev():
            it = dev.receive_matched_scene_video_frame_and_gaze(timeout_seconds=2)
            if it is None:
                return None, 0, 0, False, 0
            return it.frame.bgr_pixels, it.gaze.x, it.gaze.y, it.gaze.worn, it.frame.timestamp_unix_seconds
        print("live view: q to quit")
        view(get_dev)
finally:
    dev.close()
print("NEON CHECK DONE")
