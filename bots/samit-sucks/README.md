# samit-sucks

The redesign of `hybrid`, built beside it so `hybrid` stays the live submission until this bot
beats it on the cohort. It follows the external review of 2026-10-25: macro planned in the draft,
micro as one influence evaluation per step, and a fixed fuel schedule.

## Phases

| Phase | What | Done when |
|---|---|---|
| 0 (done) | Seed from `hybrid-v42`, scorecard script, top-ten replays | v42 scorecard and top-ten scorecard side by side |
| 1 (done but quiet turn) | Skeleton on a fixed fuel budget per turn phase; no branch reads fuel remaining | a copy with dormant code mirrors 40-40 exactly; no exhaustions; quiet turn under ~400K |
| 2 (done) | Shared memory: 5 position bytes (16x16 cells), enemy sighting cell + age, claims by round parity | logged ally positions match the replay within a cell and a round |
| 3 (done) | Micro: camp-reach cost board, local influence sum (enemy reach, camp reach, sightings, objective, allies), engage rule, focus fire. No flee mode | cohort deaths down and allies at death up against a same-hour control |
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

## Phase 2 status (2026-10-26): done

Shared memory now carries positions (layout in `memory_codec.py`):

| Bytes | What |
|---|---|
| 0-4 | per body: its claim (status + group) or its position cell on a 16x16 grid |
| 5 | enemy sighting: the cell of the enemy nearest the body that saw it |
| 6 | sighting round mod 64 (bits 5-0) and enemies in sight 1-3+ (bits 7-6) |
| 7 | bits 4-0: which of bytes 0-4 hold a claim |

A body publishes its claim on the turn it changes (not twice running), every 32 rounds, and every
turn while dead; otherwise its position. Every body keeps a `memory_codec.Board` (`w.board`) with
each ally's latest claim and cell, how old the cell is (`board.age(k, round)`), and the sighting.
`w.claims` is the board's claim list, so the old macro reads it unchanged. The plan said "claims by
round parity"; the kind mask (byte 7) does the job better: positions go out on almost every turn
and a changed claim goes out at once. The contested and break-out bytes are gone (their writers
were all behind switches that are off; `w.contested` is always None).

Check: `config.LOG_BOARD` on, then `python tools/run_matches.py samit-sucks hybrid@hybrid-v42 --debug
--log-limit 50000000` and `python tools/analysis/board_check.py <run dir>`. 80 games, 1.65M
beliefs about living allies:

- 99.4% read this round or last, and **100.0% of those in the ally's cell now or one turn ago**.
- 88.6% read as current (age 0): 100% in the right cell. 10.8% a round old (a claim turn): 62%.
- Tile error: within 2 tiles 95.3%, within 5 tiles 99.9%.
- The debug and release runs gave the same results game for game, so the logging is inert.

Release results, 80 games each:

| Test | Result |
|---|---|
| vs `hybrid@hybrid-v42` | 41-39, margin -19, deaths 1,235 vs 1,261, 20 exhaustions |
| vs phase 1 (`samit-sucks@f7b4cf7`) | 45-35, margin +52, deaths 1,205 vs 1,214 |

Board cost on lanes: read ~33K, publish ~20K a turn (the old claim code was ~15K). Exhaustions
are not from that: phase 1 plus a ~30K-a-turn burn against phase 1 (37/40 maps identical) gave 13
exhaustions to the plain copy's 22. They come from lanes and big-world turns already at ~2.4M, and
the count swings with the game. Nothing reads positions or sightings yet; phase 3 is the consumer.

## Phase 3 status (2026-10-26): done (3c focus fire built but off)

Sub-steps: **3a** the threat layer in every step; **3b** no flee mode, an engage rule in place of
the fight/flee rule stack, retreat = home with the layer on; **3c** focus fire; **3d** a fixed fuel
budget for the layer.

### 3a: `micro.py`

`navigation.step_along` asks `Micro.step` first whenever something could hurt us. It scores
standing still and the free neighbours: progress along the objective field, minus damage that
could land on the tile next turn as a share of our effective health (`MICRO_THREAT_WEIGHT` 10,
`MICRO_FLEE_WEIGHT` 20 in flee, 0 in a fight for now), plus allies within 4 while there is danger.
Damage sources: visible enemies (reach + 1, a soft 25% two tiles further), awake camps outside the
group we are fighting for which we would be the nearest champion in reach (a tie counts half), and
the shared sighting when no enemy is in sight (reach 2 + age, fading over 6 rounds). Allies come
from vision, else from the phase 2 board. Flee's step is now this layer on the spawn field.

Cost: a tile costs ~13K fuel to score in this interpreter, so only sources whose edge passes
through our 3x3 neighbourhood are scored (the others add the same to every tile); with none, the
usual pathing runs. Scoring every source every time cost ~190K a call.

Results, 80 games each (same opponent, dumps scored with `review_metrics.py`):

| vs `hybrid@hybrid-v42` | W-L | margin | deaths/g | ally5@death | ally4 | our exhaustions | our stalls |
|---|---|---|---|---|---|---|---|
| phase 2 | 39-38 | -6 | 15.4 | 0.55 | 37% | 44 | 213 |
| 3a | 44-34 | +78 | 15.1 | 0.59 | 39% | 56 | 250 |

Against phase 2 directly: 38-42, margin +54, deaths 1,065 vs 1,233.

The extra stalls are bodies that will not walk into an awake camp's reach (as its nearest champion)
until the stall guard retargets (fracture, islands). Removing the camp layer drops them (91 to 59 on
those two maps, 4 games) but the margin falls from +1,077 to +124; removing the sighting, +389. The
real fix is a re-plan when the objective sits behind a threat (phase 4).

### 3b, first try (reverted): flee decisions converted

Every flee decision was turned into something else (`decide._set_mode`), RECALL walking home
through the layer at flee weight with flee's shield, stun and parting shot:

| vs v42, 80 games | W-L | deaths/g | ally5@death |
|---|---|---|---|
| 3a | 44-34 | 15.1 | 0.59 |
| healthy bodies keep their objective, hurt ones go home | 18-60 | 20.3 | 0.47 |
| every flee goes home | 39-39 | 15.3 | |

Keeping the objective fails because the layer only votes when a step is taken: a body capturing,
or standing at its camp attacking, never steps, so it stands while the enemy closes. Going home
always costs farm time. Removing flee needs the layer to weigh staying to act against moving on
every turn (the objective's value of standing here against the damage that could land here), not
only inside `step_along`. That is the next attempt.

### 3b: one engage rule, no flee mode, the guard step

With `ENGAGE_RULE` the mode choice is `decide._choose_engage`: the spawn gate, then `_engage`, then
the script over farm, capture, tribute and recall only. `_engage` replaces the hard flee rules, the
contact head, the commitment, outnumbered, squad, early-leave, pair and chase rules:

- hurt (below `FLEE_HEALTH` with an enemy within 5): fight if running would not save us
  (`should_stand`), else RECALL, which now walks home through the layer at flee weight with
  flee's shield, stun and parting shot, and channels only once nothing hits us;
- a camp winning with no enemy about: RECALL, the group avoided for a while;
- FIGHT for a weak enemy close by (kill), the advantage (`FIGHT_POWER_RATIO`), a fight still even
  (`FIGHT_STAY_RATIO`), an ally fighting beside us, or a target in reach at balance 1 or better;
- otherwise the objective holds in contact, re-scored every `ENGAGE_RESCORE_TURNS` (scoring is a
  candidate rebuild, ~450K; the old flee and fight rules skipped it).

`Actions.guard` runs before farm, capture and tribute: when the damage that could land where we
stand costs more than acting here is worth (`GUARD_*`), it steps away first through the layer, and
the action can still attack. `camp_damage_incoming` now uses the nearest-champion rule
(`CAMP_NEAREST_RULE`).

| vs v42, 80 games | W-L | margin/g | deaths | our exhaustions |
|---|---|---|---|---|
| 3a | 44-34 | +47 | 1,299 | 56 |
| engage rule, threat horizon 5 when outnumbered | 36-42 | -16 | 1,204 | 90 |
| the same without the guard | 36-42 | +9 | 1,318 | 61 |
| **horizon 2 (kept)** | **44-34** | **+62** | **1,118** | 75 |

Turning away at sight range (an outnumbered horizon of 5) cost wins; the guard is what brings
deaths down (1,318 to 1,204-1,118).

### 3c: focus fire (off)

A fighting body posts its target's cell in the sighting bytes with byte 7 bit 7 set; allies in
a fight prefer an enemy in that cell after reach (`FOCUS_FIRE`). On: 42-36, -1 a game, deaths
1,286; off: 44-34, +62, 1,124. A 16x16 cell is ~5 tiles on big maps, so the call is coarse and
pulls bodies off the weakest enemy in reach. The threat layer in fights (`MICRO_FIGHT_WEIGHT` 3)
also lost: 41-37, -50 a game.

### 3d: fixed budget (half)

The layer scores at most the nearest `MICRO_MAX_SOURCES` enemies and `MICRO_MAX_CAMPS` camps.
Behaviour is still independent of fuel: a copy burning ~30K a turn mirrors 33 of 40 maps, and
every diverging map had exhaustions in the pair (a suspended turn loses its actions). Not met:
the turn cost. Lanes averages ~1.1M a turn in every version (3b adds ~40K); its worst turns are
the old refresh (0.6-1.1M) plus the candidate rebuild in decide (~1.1M). Phase 4's routes are
the way to drop the per-turn re-ranking.

## Phase 0 notes

- Confirmed by watching top-ten replays (2026-10-25): the review's inferences hold. They ward on
  the lanes between their farm and the enemy, move in pairs, and turn away at sight range
  rather than at a health threshold.
