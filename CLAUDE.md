# Working in this repo

- **Keep `README.md` up to date in the same change** whenever you add or change a feature, command, script,
  folder, config option, port, device, or measured number. The README is the entry point for the team:
  what Spidey Sense is, the architecture diagram, quickstart commands, the "Measured so far" table, and credits.
- **Append to `docs/BUILD_LOG.md`** after every meaningful step: a timestamp (PT), what was done, exact commands,
  files changed, results and measurements, gotchas. Keep its "How to reproduce from scratch" section current.
- Protocol changes between the app and the engine: update `docs/protocol.md` and `ios/.../Protocol.swift` together.
- Never commit or print secrets. The API key lives only in the repo-root `.env` (gitignored).
- Bluetooth (El Jefe "DataFeelCE") must run from Terminal.app; only one process can hold the BLE link.
- Don't buzz/heat the Dot without the user's OK; thermal must respect the 40 °C cap / 30 s auto-off.
- **Commit and push regularly**: after every piece lands and at least every ~20 minutes, including parallel WIP (keep `main`
  runnable; use a branch for broken WIP, then merge). Each push also updates README.md, docs/BUILD_LOG.md and docs/STATUS.md
  (one page: what works + commit, in progress, known issues, how to run each test, TBD numbers).
