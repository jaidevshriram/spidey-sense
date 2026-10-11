# vlm/

Vision-language "hazard" detection. The detector itself is `backend/spidey/detectors.py: VLMDetector`
(Claude `claude-haiku-5-5`, 512 px JPEG with the gaze drawn as a red dot, strict-JSON reply
`{hazard, x, seen_by_user, urgency}`; direction left/ahead/right is computed from `x`).

- `assets/`: synthetic test scenes (street + car, empty hallway, kitchen knife); regenerate with
  `python3 vlm/assets/make_test_scenes.py`.
- `latency_report.py`: per-call latency + outputs from a run's JSONL log.
- Test: `python3 backend/run.py --config backend/configs/config.vlm.yaml --seconds 21`

The key is read from `ANTHROPIC_API_KEY` or the repo-root `.env` (`scripts/set_api_key.sh`).
