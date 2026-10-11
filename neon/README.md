# neon/

`neon_app/` is a **copy** of `neon_app/` from Jaidev's earlier project `~/Desktop/GIT-Projects/neon-gaze-app`
(copied 2026-10-10, original untouched). `backend/spidey/sources.py: NeonSource` uses it.

`open_source(kind)`: `capture` = Neon on USB → Pupil Capture on the Mac (Pupil Remote :50020);
`realtime` = Neon Companion phone over Wi-Fi (wireless, for the demo); `mock` = fake gaze.
`next()` → `Sample(frame BGR, x, y scene-camera px origin top-left, confidence, worn, t)`.

The Capture path needs Pupil Capture installed from source (see the original repo's README).

## Wireless via the Companion phone's hotspot (tested 2026-10-10)

1. Glasses into the Companion phone (USB-C), open the **Neon Companion** app (glasses icon shows connected).
2. Phone: turn on **Wi-Fi hotspot**. Mac: join that hotspot. The phone is now the Mac's default gateway
   (`route -n get default | grep gateway`, e.g. 192.168.16.142).
3. `./scripts/neon_check.sh` (or `python3 neon/check_companion.py`): it tries the gateway first
   (`GET http://<gateway>:8080/api/status`), then mDNS. mDNS discovery found nothing over the Android hotspot.
4. `python3 backend/run.py --config backend/configs/config.neon.yaml`: same order, or `source.host` if set.

`connect_companion()` in `neon_app/source.py` does the IP → gateway → mDNS order. `NeonRealtimeSource` estimates the
phone/Mac clock offset (Time Echo, ~+70–80 ms here) and returns `Sample.t` on the Mac clock.
Neon gaze has no per-sample confidence; the API gives `worn` plus eye state (pupil diameters, eyelid aperture).
If gaze is consistently off-target, use **Offset correction** in the Companion app (wearer's profile / gaze overlay).
