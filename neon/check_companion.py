"""Find the Neon Companion phone on Wi-Fi and measure the stream the engine will use.

    python3 neon/check_companion.py                     # auto: default gateway (phone hotspot), then mDNS
    python3 neon/check_companion.py 192.168.1.23        # or the phone's IP (Companion app → stream screen)
    python3 neon/check_companion.py [ip] --view         # then a live window: scene + gaze circle + delay
    python3 neon/check_companion.py [ip] --view --seconds 10 --save logs/neon_frame.jpg
    python3 neon/check_companion.py --mock --view       # no device: test the window with fake gaze

Reports: phone name/IP/battery, glasses connected, scene frame rate + resolution, gaze rate,
worn %, eye state (pupil diameters, eyelid aperture), and latency phone capture -> received on the
Mac, corrected for the phone/Mac clock offset (Pupil Labs Time Echo).
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

argv = sys.argv[1:]
VIEW, MOCK = "--view" in argv, "--mock" in argv


def opt(name, default=None):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default


SECONDS, SAVE = opt("--seconds"), opt("--save")
skip = {SECONDS, SAVE} - {None}
SECONDS = float(SECONDS) if SECONDS else None
args = [a for a in argv if not a.startswith("--") and a not in skip]
ip = args[0] if args else None


def view(get, seconds=None, save=None):
    """Scene with gaze circle and capture->Mac delay. q / Esc closes. Saves the last worn frame."""
    import cv2
    t_end = time.time() + seconds if seconds else None
    best, last = None, None
    while not t_end or time.time() < t_end:
        img, gx, gy, worn, ts = get()
        if img is None:
            continue
        delay = (time.time() - ts) * 1000
        img = img.copy()
        cv2.circle(img, (int(gx), int(gy)), 30, (0, 0, 255) if worn else (0, 200, 255), 5)
        cv2.putText(img, f"capture->Mac {delay:4.0f} ms  gaze ({gx:.0f},{gy:.0f})  "
                    f"{'worn' if worn else 'NOT WORN'}", (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2,
                    (255, 255, 255), 3)
        last = img
        if worn:
            best = img
        cv2.imshow("Neon check (q to quit)", cv2.resize(img, None, fx=0.6, fy=0.6))
        if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
            break
    cv2.destroyAllWindows()
    cv2.waitKey(1)
    out = best if best is not None else last
    if save and out is not None:
        Path(save).parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(save, out)
        print(f"saved annotated frame -> {save}{'' if best is not None else ' (glasses NOT WORN in it)'}")


if MOCK:
    from neon_app import open_source
    src = open_source("mock", with_video=True)
    def get_mock():
        s = src.next()
        return s.frame, s.x, s.y, s.worn, s.t - 0.05   # pretend 50 ms network delay
    print("mock source: 30 Hz figure-8 gaze")
    if VIEW:
        view(get_mock, seconds=SECONDS or (None if sys.stdin.isatty() else 3), save=SAVE)
    print("NEON CHECK DONE (mock)")
    sys.exit(0)

from neon_app import connect_companion  # noqa: E402

t0 = time.time()
try:
    dev = connect_companion(ip, 8080)
except RuntimeError as e:
    sys.exit(f"{e}\nIs the Neon Companion app open? Phone and Mac on the same Wi-Fi (or the Mac on the "
             "phone's hotspot), and client isolation off on that network?")
print(f"found {dev.phone_name} @ {dev.phone_ip} in {time.time() - t0:.1f} s · battery {dev.battery_level_percent}% "
      f"({dev.battery_state}) · glasses module {dev.module_serial or 'NOT CONNECTED'}")

try:
    off = 0.0
    est = dev.estimate_time_offset(number_of_measurements=30)
    if est is not None:
        off = est.time_offset_ms.median / 1000
        print(f"clock offset Mac-phone {off * 1000:+.0f} ms · Time Echo round trip median "
              f"{est.roundtrip_duration_ms.median:.0f} ms")

    # The stream needs ~2 s to start and flush its decoder backlog; measure after that.
    t_warm = time.time() + 2
    while time.time() < t_warm:
        dev.receive_matched_scene_video_frame_and_gaze(timeout_seconds=2)
    n, lat, raw, worn, dur, shape = 0, [], [], 0, 5, None
    t_end = time.time() + dur
    while time.time() < t_end:
        item = dev.receive_matched_scene_video_frame_and_gaze(timeout_seconds=2)
        if item is None:
            continue
        now = time.time()
        n += 1
        shape = item.frame.bgr_pixels.shape
        raw.append((now - item.frame.timestamp_unix_seconds) * 1000)
        lat.append((now - item.frame.timestamp_unix_seconds - off) * 1000)
        worn += bool(item.gaze.worn)
    if not n:
        sys.exit("Connected, but no scene video arrived in 5 s. Are the glasses plugged into the phone?")
    lat.sort(); raw.sort()
    print(f"scene+gaze: {n / dur:.1f} fps · {shape[1]}x{shape[0]} · worn {100 * worn / n:.0f}% · "
          f"capture->Mac latency median {lat[len(lat) // 2]:.0f} ms, p90 {lat[int(len(lat) * .9)]:.0f} ms "
          f"(uncorrected clocks: {raw[len(raw) // 2]:.0f} ms)")
    g = [dev.receive_gaze_datum(timeout_seconds=1) for _ in range(200)]
    g = [x for x in g if x]
    if len(g) > 1:
        rate = (len(g) - 1) / (g[-1].timestamp_unix_seconds - g[0].timestamp_unix_seconds)
        last = g[-1]
        print(f"gaze only: {rate:.0f} Hz · last gaze px ({last.x:.0f}, {last.y:.0f}) · worn {last.worn}")
        if hasattr(last, "pupil_diameter_left"):   # Neon eye state (no per-sample confidence on Neon)
            print(f"eye state: pupil L {last.pupil_diameter_left:.1f} mm / R {last.pupil_diameter_right:.1f} mm"
                  + (f" · eyelid aperture L {last.eyelid_aperture_left:.1f} / R {last.eyelid_aperture_right:.1f} mm"
                     if hasattr(last, "eyelid_aperture_left") else ""))
    if VIEW:
        def get_dev():
            it = dev.receive_matched_scene_video_frame_and_gaze(timeout_seconds=2)
            if it is None:
                return None, 0, 0, False, 0
            return (it.frame.bgr_pixels, it.gaze.x, it.gaze.y, it.gaze.worn,
                    it.frame.timestamp_unix_seconds + off)
        print("live view: q to quit")
        view(get_dev, seconds=SECONDS, save=SAVE)
finally:
    dev.close()
print("NEON CHECK DONE")
