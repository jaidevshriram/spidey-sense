# World-sim evaluation

Real Haiku, world-style frames (no monitor), engine path incl. gaze cache. 2 reps per scenario. T_NEAR (person within 1 body-height of the water) = 2.8 s.

| Scenario | Segment | Expect | Pass | Fires (t s, fast/haiku, latency ms) |
|---|---|---|---|---|
| A `big_cat_look` | lion, reading the sign (0–4 s) | none | **2/2** | –; – |
| A `big_cat_look` | lion, LOOKING at it (from 4 s) (4–10.5 s) | fire | **2/2** | 4.59s fast 2.0; 4.75s fast 1.9 |
| A `big_cat_look` | dog, looking at it (10.5–16 s) | none | **2/2** | –; – |
| A `big_cat_look` | tiger in view, reading the sign (16–24 s) | none | **2/2** | –; – |
| B `lion_unnoticed` | lion, looking at it (+3 s noticed memory after 6 s) (0–8.8 s) | none | **2/2** | –; – |
| B `lion_unnoticed` | lion, reading the sign, memory expired (from 9 s) (8.8–18.5 s) | fire | **2/2** | 8.99s fast 2.2, 12.04s fast 2.0, 15.09s fast 2.2; 8.96s fast 2.0, 11.98s fast 2.0, 15.03s fast 1.8 |
| B `lion_unnoticed` | dog, reading the sign (18.5–24 s) | none | **2/2** | –; – |

- False alarms (fires in 'none' segments): **0**
- Gaze-cache fires: latency (gaze sample → buzz) median 2 ms (n=8)

- A: gaze lands on the lion at 4 s → first buzz after 0.67 s median (0.59, 0.75)

Runs in parallel (4 at a time), each in real time. Traces per run: `TRACE_DIR=<dir> ./scripts/traces.sh` with A-1 → `/Users/jaidev/Desktop/GIT-Projects/spidey-sense/logs/world_eval/A-1/trace/20261010-210755-config.world.sim`, A-2 → `/Users/jaidev/Desktop/GIT-Projects/spidey-sense/logs/world_eval/A-2/trace/20261010-210755-config.world.sim`, B-1 → `/Users/jaidev/Desktop/GIT-Projects/spidey-sense/logs/world_eval/B-1/trace/20261010-210755-config.world.sim`, B-2 → `/Users/jaidev/Desktop/GIT-Projects/spidey-sense/logs/world_eval/B-2/trace/20261010-210755-config.world.sim`
