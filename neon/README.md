# neon/

`neon_app/` is a **copy** of `neon_app/` from Jaidev's earlier project `~/Desktop/GIT-Projects/neon-gaze-app`
(copied 2026-10-10, original untouched). `backend/spidey/sources.py: NeonSource` uses it.

`open_source(kind)`: `capture` = Neon on USB → Pupil Capture on the Mac (Pupil Remote :50020);
`realtime` = Neon Companion phone over Wi-Fi (wireless, for the demo); `mock` = fake gaze.
`next()` → `Sample(frame BGR, x, y scene-camera px origin top-left, confidence, worn, t)`.

The Capture path needs Pupil Capture installed from source (see the original repo's README).
