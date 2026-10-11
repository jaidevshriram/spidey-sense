# World-sim evaluation

Real Haiku, world-style frames (no monitor), engine path incl. gaze cache. 1 reps per scenario. T_NEAR (person within 1 body-height of the water) = 2.8 s.

| Scenario | Segment | Expect | Pass | Fires (t s, fast/haiku, latency ms) |
|---|---|---|---|---|
| A `big_cat_look` | lion, reading the sign (0–4 s) | none | **1/1** | – |
| A `big_cat_look` | lion, LOOKING at it (from 4 s) (4–10.5 s) | fire | **1/1** | 4.29s fast 3.7 |
| A `big_cat_look` | dog, looking at it (10.5–16 s) | none | **1/1** | – |
| A `big_cat_look` | tiger in view, reading the sign (16–24 s) | none | **1/1** | – |
| B `lion_unnoticed` | lion, looking at it (+3 s noticed memory after 6 s) (0–8.8 s) | none | **1/1** | – |
| B `lion_unnoticed` | lion, reading the sign, memory expired (from 9 s) (8.8–18.5 s) | fire | **1/1** | 8.97s fast 1.9, 12.0s fast 2.2, 15.02s fast 2.4 |
| B `lion_unnoticed` | dog, reading the sign (18.5–24 s) | none | **1/1** | – |
| C `person_to_water` | far from the water (0–1.7999999999999998 s) | none | **1/1** | – |
| C `person_to_water` | near / at the edge (1.7999999999999998–16.5 s) | fire | **1/1** | 5.02s haiku 1625.4 |
| C `person_to_water` | walking back (16.5–21 s) | none | **1/1** | – |
| D1 `person_to_water_unwatched` | far, reading the sign (0–1.7999999999999998 s) | none | **1/1** | – |
| D1 `person_to_water_unwatched` | near the edge, reading the sign (1.7999999999999998–16.5 s) | fire | **1/1** | 3.84s haiku 1570.8 |
| D1 `person_to_water_unwatched` | walking back (16.5–21 s) | none | **1/1** | – |
| D2 `person_to_water_unwatched` | watching the person the whole time (0–21 s) | none | **1/1** | – |

- False alarms (fires in 'none' segments): **0**
- Gaze-cache fires: latency (gaze sample → buzz) median 2 ms (n=4)
- Haiku-path fires: frame → buzz median 1598 ms (n=2)
- A: gaze lands on the lion at 4 s → first buzz after 0.29 s median (0.29)

Runs in parallel (5 at a time), each in real time. Traces per run: `TRACE_DIR=<dir> ./scripts/traces.sh` with A-1 → `/Users/jaidev/Desktop/GIT-Projects/spidey-sense/logs/world_eval/A-1/trace/20261011-111435-config.world.sim`, B-1 → `/Users/jaidev/Desktop/GIT-Projects/spidey-sense/logs/world_eval/B-1/trace/20261011-111435-config.world.sim`, C-1 → `/Users/jaidev/Desktop/GIT-Projects/spidey-sense/logs/world_eval/C-1/trace/20261011-111435-config.world.sim`, D1-1 → `/Users/jaidev/Desktop/GIT-Projects/spidey-sense/logs/world_eval/D1-1/trace/20261011-111435-config.world.sim`, D2-1 → `/Users/jaidev/Desktop/GIT-Projects/spidey-sense/logs/world_eval/D2-1/trace/20261011-111435-config.world.sim`
