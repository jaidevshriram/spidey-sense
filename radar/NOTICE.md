# Radar: credits and ownership

- **Board owner:** the Infineon DEMO-BGT60TR13C (60 GHz FMCW radar, USB at `/dev/cu.usbmodem101`) is owned by the separate
  **radar-lab** workstream at `~/Downloads/radar-lab`. Its `server.py` is the only process that opens the board; it streams
  per-frame targets and alert states over WebSocket (`ws://localhost:8090/ws`). Spidey Sense (`backend/spidey/radar.py`)
  is a read-only client of that stream and never opens the USB/serial port itself.
- **Infineon Radar SDK 3.6.4** (`ifxradarsdk` Python wheel + prebuilt libraries), © Infineon Technologies AG. The Python
  wrapper is MIT-licensed; the SDK package as a whole ships under Infineon's (Cypress) End User License Agreement.
  radar-lab uses it unmodified; nothing from the SDK is copied into this repo.
- **SDK mirror:** radar-lab obtained the macOS wheel from the public GitHub mirror of the SDK 3.6.4 package,
  [`XiaoKChenEdu/XENSIV-60GHz-Radar`](https://github.com/XiaoKChenEdu/XENSIV-60GHz-Radar) (`reference/radar_sdk/`),
  sparse-cloned into `~/Downloads/radar-lab/third_party/xensiv/`. That repo's `LICENSE` is the Cypress/Infineon EULA.
- The signal processing (range-Doppler FFT, CFAR, EKF tracking, alert state machine) is radar-lab's own code, not part
  of this repo. `backend/tests/fake_radar_server.py` reproduces only its WebSocket message *schema* for tests.
