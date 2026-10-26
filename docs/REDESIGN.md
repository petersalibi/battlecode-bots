# The samit-sucks redesign: record and measurements

> **Superseded 2026-10-26.** This phased redesign (tags `samit-sucks-v1`, `samit-sucks-3a`) ended with only phase 3a a measured gain; the phase 3b-4 results reported here were mis-summed (see the correction in the old README in git history). `bots/samit-sucks` is now v2, a from-scratch bot: see its README and DESIGN.md.

Started 2026-10-25. The live bot stays `hybrid` until `bots/samit-sucks` beats it. This file
records why the redesign exists, what was built, and what was measured on the way; the phase
table and current status live in `bots/samit-sucks/README.md`.

## Why

An external review of the bot (2026-10-25) found the plateau structural rather than a tuning
problem. Cohort and top-ten studies line up:

| Metric | Top ten | Cohort | Us |
|---|---|---|---|
| Deaths a game | 4.1 | 13.5 | 21.9 |
| Contact share of living turns | 9% | 15% | 14% |
| Health when stepping away | 67% | 60% | 51% |
| Allies within 5 at death | 0.63 | 0.66 | 0.30 |

The bot has no picture of the map beyond its own sight and no plan beyond the current turn. The
review's ranked problems: behaviour depended on fuel remaining (the network gates), rules piled up
ahead of the mode selector, networks returned little for their cost, and six modes mixed
destinations (capture, tribute, recall) with micro (fight, flee).

Its proposed shape, adopted as the plan:

- Macro planned in the draft (25M fuel, 1,024 bytes): per-body group sequence and partner, bank
  altars, ward spots on the lanes, map class. Re-plan only on events.
- Micro as one evaluation per step: a local influence sum over the reachable tiles and dash
  landings (enemy next-turn reach, awake camp reach, decayed shared sightings, the objective's
  gradient, pull towards allies). No flee mode: a threat layer that is always on.
- Shared memory carrying positions (8 bytes: 5 position cells, an enemy sighting, claims).
- A fixed fuel schedule: no branch reads the fuel left.

Watching top-ten replays confirmed the review's inferences: they ward on the lanes between
their farm and the enemy, move in pairs, and turn away at sight range rather than at a health
threshold. Our v42 places about 248 wards a game against their ~38.

### Influence maps, briefly

A grid where each unit radiates a value that decays with distance (negative for threat, positive
for support), summed per tile. Layers: threat, support, control (support minus threat), tension
(their sum). Propagate by Chebyshev falloff (cheap, ignores walls) or by walking distance (a
`DistanceField`, respects walls). Whole-board maps do not fit Python's fuel; evaluate only the
tiles about to be scored (the 9 reachable tiles plus dash landings). v42 has no influence map:
`step_away` scores tiles by Chebyshev distance to the nearest threat only, and routes treat camp
tiles as walls but not their attack rings.

## Phase 0: baseline and tools

- `bots/samit-sucks` seeded from `hybrid-v42` (the live code, same as v39), not from the working
  copy of `hybrid` (its jungle-camp commit had fuel problems). It mirrored v42 40-40, margin 0.
- `tools/analysis/review_metrics.py`: the review's scorecard for any dumps (ladder, top ten, local
  batches), from true positions. Per-match deaths match the harness exactly. v42 local mirror:
  16.4 deaths a game, 15% contact, 59% health when stepping away, 0.47 allies within 5 at death,
  37% of turns with an ally within 4, 248 wards a game.
- Top-ten winning games since engine 1.0.3 were fetched to `data/replays/top10` (99 games).

## Phase 1: behaviour independent of fuel

### What was there

Ten places branched on fuel: four network gates (mode selector, contact, fight, camp scorer) and
the escape head, all with reserves sized from `act_peak` (the worst act this game, so history);
the retarget deferral (`retarget_peak`); optional field builds; the stepped setup in
`finish_init`; and the first-turn checks. The proof that this made the bot chaotic: adding one
print a turn to an identical copy turned a mirror into 5-7, and a 20K-a-turn fuel burn left only
2 of 40 maps mirroring.

### What changed

- Both live networks off (`USE_NN_MODE`, `USE_NN_CONTACT`): 36-44 against v42, inside noise.
  Network gates now ask `budget.fits(w, cost)`, an estimate from the map and what is in sight.
- Setup fixed by the map: round 1 never acts; `budget.first_turn_fits` splits setup over two
  turns on maps with many camps; `finish_init` runs one fixed step a turn; maps from 80x80 up
  take whole turns for setup (`World.setup_alone`).
- One heavy job a turn (`w.heavy`): a camp-list rebuild or a new field costing `HEAVY_FIELD` or
  more. A second waits a turn (`navigation.field_to` returns None, the body holds).
- An interim fuel guard on re-ranks was tried and removed: ~2,700 trips in 80 games, with no
  fewer exhaustions than without it.
- A re-rank reuses the scores the mode choice gave the same list that round; claims decode
  through a table.

### Results (80 games each)

| Test | v42 | samit-sucks |
|---|---|---|
| Itself vs a copy burning 20K fuel a turn: maps mirroring exactly | 2/40 | **40/40** |
| Fuel exhaustions | 54 | 4 |
| Average fuel a turn | 969K | 790K |
| Head to head | | 38-42, margin -41 |

Not met: a quiet turn under 400K (see below). Remaining exhaustions: lanes and yellow brick
road. Known v42 bug carried over: a hurt body in flee mode with an enemy at the edge of sight can
freeze for the rest of the game (pilgrimage: 1,450 rounds); phase 3 removes flee mode.

### Testing method that came out of it

Behaviour no longer depends on fuel, so:

- Determinism: `python tools/run_matches.py samit-sucks <copy with a fuel burn>` then
  `python tools/analysis/mirror_check.py <run dir>` must show 40/40.
- A pure optimisation must mirror 40/40 against a copy of the code before it. Any divergence is
  a behaviour change (it caught one: a living camp in sight does not imply its group reads alive).

## Phase 2: shared memory carrying positions

Bytes 0-4 hold each body's claim or its position cell (16x16 grid), byte 7 says which; bytes 5-6
an enemy sighting (cell, round mod 64, count). A claim goes out on the turn it changes, every 32
rounds and while dead; positions on the other turns. Readers keep a `Board` across turns.

- Accuracy (80 debug games, `tools/analysis/board_check.py`): 100% of beliefs at most a round old
  are in the ally's cell now or one turn ago; 99.4% of beliefs are that fresh. A refresh every 8
  rounds left 17% of beliefs a round old; every 32 rounds, 11%.
- Results: 41-39 against v42 (margin -19), 45-35 against phase 1 (+52).
- Cost ~50K a turn (read ~33K, publish ~20K) against ~15K for the old claims. Decoding is the
  cost: an unchanged read is 11K, and something changes most rounds.
- Exhaustions are not fuel-sensitive at this scale: phase 1 with a ~30K burn vs itself had 13 to
  the plain copy's 22, in games identical on 37 of 40 maps. Lanes and big-world peak at ~2.4M.

## Measurements worth keeping

### Turn cost (v42, 208K profiled turns, 40 maps)

Median 870K, p90 1.53M, p99 1.92M, max 4.4M. Refresh p50 290K with nothing in sight, +~80K per
enemy, +~20K per visible camp. Script decide p50 185K, p99 1.05M. A camp re-rank p50 335K, p99
970K.

Where a turn goes (samit-sucks, quiet turns on six large maps): camp refresh ~170K, candidate
rebuild ~450K (from-me field 125K, a pass over every group ~165K, candidate objects ~170K),
scoring ~45K per candidate (a dozen per rebuild), captures ~80K, claims ~28K, altar locks ~27K.
Two verified-exact rewrites (bound constants in scoring, a spatial index for the group-vision
loop) saved under 0.5%: the cost is spread thinly, so the new micro and macro layers need lean
designs rather than the old code polished.

### First-turn setup

camp_groups() plus Group objects cost about 36.5K per group plus 16K per camp (fit over 38 maps,
residual under 185K); the spawn field and the rest about 450K plus 80 per tile. Yellow brick road,
boss battle, road of riches and islands do not fit the first turn's 4.4M with everything.

### Per-operation fuel (release build, measured 2026-10-25)

| Operation | Fuel |
|---|---|
| `field.get(vec)`, `bitboard.is_set(vec)` | ~380 |
| Python attribute or simple op | ~170 |
| One pass of a small Python loop body | ~540 |
| `vec.chebyshev_distance` | ~480 |
| `champion.position()` / `.health()` / `.strength()` / `.focus()` | 1.7K-1.9K |
| `int(champion)`, `champion.champion_name()` | ~1.6K |
| `vision.visible_tiles()` | ~4K |
| `(bitboard & bitboard).is_empty()` | ~5.9K |
| `shared_memory.read` of 8 bytes | ~7.5K |
| `vision.camp_tiles().positions()` | ~8K |
| `field.export_linear_buffer()` | ~38K |
| `vision.champions(ALLY)` with four in sight | ~47K |
| `DistanceField.compute` radius 10 | ~69K |

Engine calls are cheap; listing units, combining boards and computing fields are not. The old
note that an empty loop pass costs about 1,000 fuel is high: measure with a benchmark bot, as
here, rather than guess.

## Tools added

- `tools/analysis/review_metrics.py`: the scorecard (`label=path[@selector]`).
- `tools/analysis/mirror_check.py`: which maps of a batch mirror exactly.
- `utils/parse_map.py`: ward positions from a match JSON (top-team study).
