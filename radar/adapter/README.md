# radar-lab's Spidey adapter (reference copy)

Copied unchanged from `~/Downloads/radar-lab/spidey_adapter/` (the radar workstream; it owns the board via `server.py`).
- `PROTOCOL.md`: the authoritative radar feed protocol v2 (`ws://localhost:8090/ws`). **Our `backend/spidey/radar.py`
  follows it:** we act only on the server's debounced `state`, use `primary.where` (mount-aware, with hysteresis),
  `primary.closing` / `r` / `ttc`, and treat > 0.5 s without frames as no information (fail safe → calm).
- `radar_detector.py` / `config.radar.yaml`: their design plugs radar in as the *detector* (radar-only, or combined with
  the VLM where radar wins). We kept radar as a separate **sense** that runs alongside the Neon + Haiku detector,
  with app-editable `source: radar` triggers (distance / speed), so this file is **not wired in**. It's kept for reference
  and for radar-only runs if we ever want one.
