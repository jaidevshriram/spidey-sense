"""Run the spidey pipeline.

    python3 backend/run.py                        # configs/config.yaml (fake source/detector, console sink)
    python3 backend/run.py --sink datafeel        # same, but buzz the real dots (USB)
    python3 backend/run.py --sink datafeel-ble    # dots over Bluetooth (run from Terminal.app)
    python3 backend/run.py --config backend/configs/config.vlm.yaml --seconds 21   # Haiku vision
"""
import argparse

from spidey.engine import ROOT, load_config, run

ap = argparse.ArgumentParser()
ap.add_argument("--config", default=str(ROOT / "configs" / "config.yaml"))
ap.add_argument("--sink", choices=["console", "datafeel", "datafeel-ble", "phone"], help="override sink.type")
ap.add_argument("--source", choices=["fake", "neon"], help="override source.type")
ap.add_argument("--seconds", type=float, help="stop after this many seconds")
args = ap.parse_args()

cfg = load_config(args.config)
if args.sink:
    cfg["sink"] = {**({} if cfg.get("sink", {}).get("type") != args.sink else cfg["sink"]), "type": args.sink}
if args.source:
    cfg["source"] = {"type": args.source}
run(cfg, args.seconds)
