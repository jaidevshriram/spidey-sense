# World-sim evaluation

Real Haiku, world-style frames (no monitor), engine path incl. gaze cache. 3 reps per scenario. T_NEAR (person within 1 body-height of the water) = 2.8 s.

| Scenario | Segment | Expect | Pass | Fires (t s, fast/haiku, latency ms) |
|---|---|---|---|---|
| A `big_cat_look` | lion, reading the sign (0–4 s) | none | **3/3** | –; –; – |
| A `big_cat_look` | lion, LOOKING at it (from 4 s) (4–10.5 s) | fire | **3/3** | 4.27s fast 2.3; 4.21s fast 3.2; 4.24s fast 2.1 |
| A `big_cat_look` | dog, looking at it (10.5–16 s) | none | **3/3** | –; –; – |
| A `big_cat_look` | tiger in view, reading the sign (16–24 s) | none | **3/3** | –; –; – |
| B `lion_unnoticed` | lion, looking at it (+3 s noticed memory after 6 s) (0–8.8 s) | none | **3/3** | –; –; – |
| B `lion_unnoticed` | lion, reading the sign, memory expired (from 9 s) (8.8–18.5 s) | fire | **3/3** | 9.02s fast 1.9, 12.05s fast 1.8, 15.11s fast 2.8; 9.0s fast 2.6, 12.05s fast 2.7, 15.09s fast 3.1; 9.0s fast 2.6, 12.04s fast 2.3, 15.09s fast 3.4 |
| B `lion_unnoticed` | dog, reading the sign (18.5–24 s) | none | **3/3** | –; –; – |
| C `person_to_water` | far from the water (0–1.7999999999999998 s) | none | **3/3** | –; –; – |
| C `person_to_water` | near / at the edge (1.7999999999999998–16.5 s) | fire | **3/3** | 3.89s haiku 1466.1; 5.21s haiku 1632.6; 4.04s haiku 1482.4 |
| C `person_to_water` | walking back (16.5–21 s) | none | **3/3** | –; –; – |
| D1 `person_to_water_unwatched` | far, reading the sign (0–1.7999999999999998 s) | none | **3/3** | –; –; – |
| D1 `person_to_water_unwatched` | near the edge, reading the sign (1.7999999999999998–16.5 s) | fire | **3/3** | 5.16s haiku 1771.9; 4.7s haiku 1516.3; 3.33s haiku 1637.6 |
| D1 `person_to_water_unwatched` | walking back (16.5–21 s) | none | **3/3** | –; –; – |
| D2 `person_to_water_unwatched` | watching the person the whole time (0–21 s) | none | **3/3** | –; –; – |

- False alarms (fires in 'none' segments): **0**
- Gaze-cache fires: latency (gaze sample → buzz) median 3 ms (n=12)
- Haiku-path fires: frame → buzz median 1574 ms (n=6)
- A: gaze lands on the lion at 4 s → first buzz after 0.24 s median (0.27, 0.21, 0.24)

Runs in parallel (8 at a time), each in real time. Traces per run: `TRACE_DIR=<dir> ./scripts/traces.sh` with A-1 → `/Users/jaidev/Desktop/GIT-Projects/spidey-sense/logs/world_eval/A-1/trace/20261010-193018-config.world.sim`, A-2 → `/Users/jaidev/Desktop/GIT-Projects/spidey-sense/logs/world_eval/A-2/trace/20261010-193018-config.world.sim`, A-3 → `/Users/jaidev/Desktop/GIT-Projects/spidey-sense/logs/world_eval/A-3/trace/20261010-193018-config.world.sim`, B-1 → `/Users/jaidev/Desktop/GIT-Projects/spidey-sense/logs/world_eval/B-1/trace/20261010-193018-config.world.sim`, B-2 → `/Users/jaidev/Desktop/GIT-Projects/spidey-sense/logs/world_eval/B-2/trace/20261010-193018-config.world.sim`, B-3 → `/Users/jaidev/Desktop/GIT-Projects/spidey-sense/logs/world_eval/B-3/trace/20261010-193018-config.world.sim`, C-1 → `/Users/jaidev/Desktop/GIT-Projects/spidey-sense/logs/world_eval/C-1/trace/20261010-193018-config.world.sim`, C-2 → `/Users/jaidev/Desktop/GIT-Projects/spidey-sense/logs/world_eval/C-2/trace/20261010-193018-config.world.sim`, C-3 → `/Users/jaidev/Desktop/GIT-Projects/spidey-sense/logs/world_eval/C-3/trace/20261010-193041-config.world.sim`, D1-1 → `/Users/jaidev/Desktop/GIT-Projects/spidey-sense/logs/world_eval/D1-1/trace/20261010-193041-config.world.sim`, D1-2 → `/Users/jaidev/Desktop/GIT-Projects/spidey-sense/logs/world_eval/D1-2/trace/20261010-193044-config.world.sim`, D1-3 → `/Users/jaidev/Desktop/GIT-Projects/spidey-sense/logs/world_eval/D1-3/trace/20261010-193044-config.world.sim`, D2-1 → `/Users/jaidev/Desktop/GIT-Projects/spidey-sense/logs/world_eval/D2-1/trace/20261010-193044-config.world.sim`, D2-2 → `/Users/jaidev/Desktop/GIT-Projects/spidey-sense/logs/world_eval/D2-2/trace/20261010-193044-config.world.sim`, D2-3 → `/Users/jaidev/Desktop/GIT-Projects/spidey-sense/logs/world_eval/D2-3/trace/20261010-193044-config.world.sim`
