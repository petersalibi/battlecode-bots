# samit-sucks

The redesign of `hybrid`, built beside it so `hybrid` stays the live submission until this bot
beats it on the cohort. It follows the external review of 2026-10-25: macro planned in the draft,
micro as one influence evaluation per step, and a fixed fuel schedule.

## Phases

| Phase | What | Done when |
|---|---|---|
| 0 (done) | Seed from `hybrid-v42`, scorecard script, top-ten replays | v42 scorecard and top-ten scorecard side by side |
| 1 (done but quiet turn) | Skeleton on a fixed fuel budget per turn phase; no branch reads fuel remaining | a copy with dormant code mirrors 40-40 exactly; no exhaustions; quiet turn under ~400K |
| 2 | Shared memory: 5 position bytes (16x16 cells), enemy sighting cell + age, claims by round parity | logged ally positions match the replay within a cell and a round |
| 3 | Micro: camp-reach cost board, local influence sum (enemy reach, camp reach, sightings, objective, allies), engage rule, focus fire. No flee mode | cohort deaths down and allies at death up against a same-hour control |
| 4 | Macro in the draft: map class, per-body group sequence and partner, bank altars, ward spots on lanes; re-plan only on events | cohort gem margin beats the greedy placeholder |
| 5 | Switch the submission, tune weights one at a time | |

## Phase 0 status

- `src/` is `hybrid-v42` exactly (the live code, same as v39), not the working copy of `hybrid`.
  Check: `python tools/run_matches.py samit-sucks hybrid@hybrid-v42` gives 40-40, margin +0.
- Record and measurements: `docs/REDESIGN.md`.
- Scorecard: `tools/analysis/review_metrics.py` (deaths, contact, health when stepping away, allies
  and enemies at death, ally within 4, wards). Local v42 mirror baseline (80 games, 1.0.3):

  | deaths/g | contact | hp@away | ally5@death | enemy5@death | ally4 | wards/g |
  |---|---|---|---|---|---|---|
  | 16.4 | 15% | 59% | 0.47 | 1.44 | 37% | 248 |

  The mirror is against ourselves, so these are not ladder numbers; the review's 21.9 deaths,
  51% and 0.30 come from ladder games.
- Still to do: fetch the top ten's ranked games and our own, then run the scorecard on both:

  ```
  python tools/fetch_replays.py --teams 10 --per-team 10 --since <engine 1.0.3 time> --won-only -o data/replays/top10/raw
  python tools/replay_dump.py data/replays/top10/raw -o data/replays/top10/ladder --jobs 6
  python tools/fetch_ours.py --since <ISO UTC> -o data/replays/ours-v42
  python tools/analysis/review_metrics.py ours=data/replays/ours-v42 top10=data/replays/top10@won
  ```

## Phase 1 status (2026-10-25): done, except the quiet-turn target

No decision reads the fuel left. `fuel.py` is for logs only; optional work is sized from the game
state (`budget.py`, constants under "Fuel plan" in config).

- Both live networks are off (`USE_NN_MODE`, `USE_NN_CONTACT`): 36-44 against v42, inside the
  mirror's noise. `nn.py` and the weights stay; a network would run only when `budget.fits(w, cost)`.
- Setup is fixed by the map: round 1 never acts; maps with many camps finish setup on round 2
  (`budget.first_turn_fits`, fit over 38 maps); `finish_init` runs one fixed step a turn; maps from
  80x80 up do all setup on turns of their own (`World.setup_alone`).
- One heavy job a turn (`w.heavy`): a camp-list rebuild or a new field of `HEAVY_FIELD` or more. A
  second one in the same turn waits for the next turn (`navigation.field_to` returns None and the
  body holds). This replaced v42's fuel-based retarget deferral and the interim safety guard,
  which fired ~2,700 times in 80 games without preventing exhaustions.
- A re-rank no longer re-scores the candidates the mode choice scored that round; claims decode
  through a table.

Results, 80 games each:

| Test | Result |
|---|---|
| vs itself plus 20K fuel a turn | **40/40 maps mirror exactly**, deaths 1,190 vs 1,190 (v42 under the same test: 2/40, deaths 1,328 vs 1,493) |
| vs `hybrid@hybrid-v42` | 38-42, margin -41, deaths 1,271 vs 1,250, **4 exhaustions** (v42: 54) |

Check with `python tools/analysis/mirror_check.py <run dir>`. Because behaviour no longer depends
on fuel, a pure optimisation must also mirror 40/40 against the version before it: run the new
code against a copy of the old one. That test caught one wrong assumption (a living camp in sight
does not always mean its group reads as alive).

Not met: the quiet turn. Average fuel a turn is 790K (v42: 969K). Profiled, the cost is spread
thinly over the old mode choice and actions (camp refresh ~170K, candidate rebuild ~450K plus
~45K per candidate scored, captures ~80K); two verified-exact rewrites (scoring with bound
constants, a spatial index for the group-vision loop) saved under 0.5% and were dropped. That
code is what phases 3 and 4 replace, so the 400K target moves to their design: give the new micro
layer a fixed budget and keep refresh lean. The camp-list rebuild is the expensive habit to
avoid in the new macro.

The remaining exhaustions are on lanes (42 groups) and yellow brick road, a handful a game pair.

Known v42 bug carried over: a hurt body in flee mode with an enemy at the edge of sight can stand
still for the rest of the game (pilgrimage, 1,450 rounds). Phase 3 removes flee mode.

## Phase 0 notes

- Confirmed by watching top-ten replays (2026-10-25): the review's inferences hold. They ward on
  the lanes between their farm and the enemy, move in pairs, and turn away at sight range
  rather than at a health threshold.
