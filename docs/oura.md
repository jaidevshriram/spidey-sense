# Oura ring heart rate (open_oura)

Status (2026-10-10): the heart sense, trigger logic, tests, app UI and engine hook are built and tested on
**replay / fake data only**. Nothing has talked to the real ring yet: no pairing, no reset, no Bluetooth.
Everything below the "At home" heading is for Jaidev to run, in order, with his explicit OK at the reset step.

## What open_oura is

[github.com/Th0rgal/open_oura](https://github.com/Th0rgal/open_oura) (MIT) is a cloud-free client for Oura Ring
Gen 3/4/5 that talks to the ring directly over Bluetooth LE. It is a **Rust** workspace (`crates/oura-cli` builds the
`oura` binary; `oura-link` does BLE via `btleplug`) plus a Python research bench (`tools/`, bleak). There is no
Python library to import, so `spidey/heart.py` runs the CLI as a subprocess.

What I read in its source (commit `e57c43c`, 2026-10-07), so these are **verified from code**, not from a ring:
- `oura --key-file K live-hr --seconds N [--raw]` switches the ring's daytime-HR feature to `CONNECTED_LIVE`, prints
  `Streaming live heart rate for Ns (Ctrl-C to stop early)...`, then **one line per valid beat**:
  `  72 bpm (IBI 833 ms)` (bpm = 60000 / IBI; IBIs outside 300–2000 ms are dropped), and after N seconds restores
  the feature to `AUTOMATIC` and exits. With no beats it prints `No beats captured. Make sure the ring is worn.`
  (`crates/oura-cli/src/main.rs` `cmd_live_hr`, `crates/oura-link/src/client.rs` `live_heart_rate` / `parse_live_hr_frame`).
- Live HR needs the ring's 16-byte **app-auth key**, as hex in a one-line file passed with `--key-file`. There is
  no environment variable for it. `oura pair` (on a factory-reset ring) generates a key, saves it 0600 to
  `--key-file` (or `oura-<serial>.key`) **before** installing it, then authenticates and prints the battery.
- Global flags: `--name` (scan filter, default `Oura`), `--address`, `--scan-timeout` (25 s), `--db` (SQLite,
  default `./oura.db`; `live-hr` also stores readings there), `--key-file`.
- `oura factory-reset --yes` (tag `0x1a`) wipes the auth key, every BLE bond, the on-ring event buffer and the
  user profile. It needs an already bonded + authenticated host. A button-free hardware reset on the Gen3 / Ring 4
  dock is documented in its `docs/factory-reset.md` (flip the dock: blue → red → magenta → yellow; blinking blue when done).
- After pairing with open_oura, measurement features are **off** (the official app normally turns them on):
  run `oura --key-file K features --enable-hr` once.
- macOS: the terminal needs Bluetooth permission (same rule as the El Jefe: run from Terminal.app).

**Assumptions (not verified; only a real ring can confirm):**
- That `live-hr` beats arrive at roughly 1 per heartbeat on Jaidev's ring generation (the code path is the
  daytime-HR live subscription; the repo reports it working on Ring 3 Horizon and Ring 5).
- That the ring keeps advertising a name containing "Oura" after re-pairing (otherwise set `OURA_NAME=""` or `OURA_ADDRESS`).
- That killing the CLI mid-chunk (engine stop) leaves the ring in `CONNECTED_LIVE` until the next chunk restores it
  (the CLI has no Ctrl-C handler). `heart.py` keeps chunks to 10 min so the CLI restores `AUTOMATIC` regularly; after
  stopping, `oura --key-file K live-hr --seconds 3` restores it by hand.
- Reconnect time per chunk (~5 s scan + connect + auth, judging by the El Jefe numbers) — a small gap every 10 min.

## Why not Oura's cloud API

Oura API v2 (`/v2/usercollection/heartrate`) only has data after the ring has synced to the phone app and the app
has uploaded it: the ring syncs in batches when the app is opened / in the background, and the heart-rate series is
documented at 5-minute granularity (denser at times, with gaps for movement or no sync). Third-party integrators
report delays of minutes up to about an hour; webhooks only shorten the cloud → us hop, not the ring → cloud one.
A "heart racing" buzz has to land within seconds, so the cloud is unusable for it. open_oura reads the beats live
from the ring itself. (Sources: Oura API v2 docs; third-party notes e.g. the Open Wearables Oura integration guide
and Home Assistant community threads. I found no official Oura latency figure.)

## How it plugs in

- `backend/spidey/heart.py`: `HeartRateSense(backend="open_oura")` runs
  `oura --key-file <key> --db backend/../logs/oura_live.db [--name N] [--address A] live-hr --seconds 600`, parses the
  bpm lines, keeps a rolling 2-min median, restarts the CLI when a chunk ends (0.5 s) and with backoff 2/5/10/20/30 s
  when it fails, and reports `status()` → the app's Oura card.
- `.env` (repo root, gitignored) holds the key, never the repo:
  - `OURA_KEY_FILE=/Users/jaidev/.config/oura/oura.key` (preferred: the file `oura pair` writes; keep it outside the repo), **or**
  - `OURA_AUTH_KEY=<32 hex chars>` (heart.py writes it to a private 0600 temp file for the CLI and deletes it on close).
  - `OURA_BIN=/path/to/open_oura/target/release/oura` (if `oura` isn't on PATH), optional `OURA_NAME`, `OURA_ADDRESS`.
  - heart.py never prints or logs the key; it shows the key file as `<key-file>` in its command line.
- Trigger: the app's "Heart racing" template (`source: heart_rate`, +15 bpm or +20 % over the 2-min median for 8 s,
  `pulse`, cooldown 60 s). Protocol: `docs/protocol.md` "Heart rate (Oura)".

## At home: step by step

Do these from **Terminal.app** (Bluetooth permission), repo root, `source .venv/bin/activate`.

1. **Build open_oura** (outside this repo, nothing installed system-wide):
   ```bash
   cd ~/Desktop/GIT-Projects && git clone https://github.com/Th0rgal/open_oura.git && cd open_oura
   cargo build --release            # -> target/release/oura
   ./target/release/oura --help
   ./target/release/oura scan       # read-only: should list the ring (name, address)
   ```
   Put `OURA_BIN=$HOME/Desktop/GIT-Projects/open_oura/target/release/oura` in the spidey-sense `.env`.

2. **Sync anything you care about in the official Oura app first.** The reset erases the ring's unsynced data.

3. **Factory reset (only with Jaidev's explicit OK).** This removes the ring from the Oura app: the app loses it
   until it is set up again, and **setting it up again in the Oura app installs Oura's key and locks open_oura out**
   (and vice versa: re-pairing with open_oura needs another reset). Options:
   - In the Oura app: remove/reset the ring (the app is the bonded, authenticated host), or
   - Hardware, Gen3 / Ring 4 dock: open_oura `docs/factory-reset.md` path B (flip sequence, ends blinking blue), or
   - `oura --key-file <existing key> factory-reset --yes` (only if you already have a key, i.e. not the first time).

4. **Pair with open_oura** to mint the auth key (ring on/near the Mac):
   ```bash
   mkdir -p ~/.config/oura && chmod 700 ~/.config/oura
   $OURA_BIN --name "" --key-file ~/.config/oura/oura.key pair     # --name "": a reset ring may not advertise "Oura"
   $OURA_BIN --key-file ~/.config/oura/oura.key info               # firmware, battery: proves the key works
   $OURA_BIN --key-file ~/.config/oura/oura.key features --enable-hr
   ```
   Then in `.env`: `OURA_KEY_FILE=/Users/jaidev/.config/oura/oura.key`. Never copy the key into the repo or chat.

5. **See live bpm through Spidey** (ring on your finger, wait a minute for beats):
   ```bash
   cd backend && python -m spidey.heart --backend open_oura
   ```
   You should see `[heart] open_oura: first beat`, then `bpm … smooth … median …` lines (the median says
   "warming up" for 30 s). Jog on the spot / climb stairs: after ~8 s at +15 bpm over your median it prints
   `*** FIRE Heart racing`. Ctrl-C to stop (then optionally `$OURA_BIN --key-file … live-hr --seconds 3` to
   restore the ring's automatic HR mode right away).

6. **Then the engine** (the hook is in engine.py already): in your config add
   ```yaml
   heart: {enabled: true, backend: open_oura}
   ```
   run the engine from Terminal.app as usual, add the "Heart racing" trigger in the app (Senses → + → Heart rate,
   or the template), and check the Devices tab: the Oura card shows `open_oura`, last HR, 2-min median, connected.
   Only one program can hold the ring's BLE link: don't run `python -m spidey.heart` and the engine at the same time.

## Risks

- **Ring firmware.** open_oura is reverse-engineered; a ring firmware update (pushed by the Oura app) can change the
  protocol or auth flow and break it. Don't let the Oura app update the ring while you depend on this; open_oura's
  own `docs/firmware-update.md` covers DFU, which we never send.
- **The official app re-pairing.** Opening the Oura app and "setting up" the ring again re-keys it and locks
  open_oura out until another factory reset. The ring can't be in both worlds: while it's paired to open_oura the
  Oura app shows it as missing and its cloud data (sleep/readiness scores) stops.
- **Data loss on reset.** Unsynced on-ring history is erased; sync in the Oura app first.
- **Battery.** `CONNECTED_LIVE` HR is power-hungry. The CLI restores `AUTOMATIC` at the end of every chunk, but a
  killed process may leave live mode on until the next chunk/restore.
- **One BLE central at a time** and the macOS Bluetooth permission rule (Terminal.app only), same as the El Jefe.
- **Accuracy.** Per-beat bpm from a finger PPG is noisy during movement; we compare a 5-s median against a 2-min
  median, which damps single bad beats but a long noisy stretch can still fire. Tune `min_delta_bpm` / `sustain_s`.
- **Destructive commands.** `factory-reset`, `rdata stop/clear` and DFU exist in the CLI; spidey only ever runs `live-hr`.
