# hybrid

The team's main bot. A scripted bot whose decisions are scoring functions, so that small packed-int8
networks can replace them one at a time behind the same interface (see `docs/NEURAL_NET_GUIDE.md`).
No networks are wired in yet: every decision below is the scripted version.

## Run

```
.\.venv\Scripts\cambc run hybrid starter-python --map corners --release --statistics out.json --json
```

Bot output is in the replay zip (`team-a.stdout`). Lines are padded with NUL bytes, so strip `\x00`
before parsing. `--statistics` only writes a file when the match finishes (not with `--rounds`).
Every 100 rounds each body prints a line with its mode, bag, gold and fuel (`avg=`, `max=`, and the
breakdown of its worst turn so far). The dev switches in `config.py` (`LOG_DECISIONS`, `TRACE_SLOT`,
`LOG_GROUPS`, `PROFILE_EVERY`) print much more and cost fuel; keep them off for submissions.

## Turn structure (`bot.py`)

1. First turn only: `World.init_static()` (map, camp groups, altar and spawn distance fields). This
   costs about 2.6M to 2.8M of the first turn's 4.55M, so turn one farms the nearest sensible camp
   without ranking candidates. Second turn: `finish_init()` builds the per-group vision masks.
2. `World.refresh()`: one pass of SDK reads into plain attributes (self, visible enemies and allies,
   visible camps, camp group states with the round they were seen, team claims from shared memory).
3. Dead: shop. Channelling (recall, tribute, teleport): leave it running. On spawn: shop.
4. `Decider.choose_mode()` picks one of six modes with masks applied before the argmax.
5. `Actions.run(mode)` executes it, then the body writes its claim (status + camp group) to shared memory.

## Decisions and their scripted strategies (`decide.py`)

**Mode selector** (farm, capture, tribute, recall, fight, flee). Utility scores on a common scale,
with the current mode given a small stickiness bonus. Hard rules: flee is forced below 30% health
with an enemy within 5 tiles and kept while the enemy stays in sight; capture requires a cleared
group within 8 turns; tribute requires gems and a usable altar; recall is masked while taking damage.

- Farm: 0.6 plus the best camp's score. Halved below 40% health, a fifth below 30%.
- Capture: 2.2 minus 0.03 per turn of travel. Captures are skipped when another ally already claims
  the group and we are not beside it (extra bodies do not speed a capture, they only split it).
- Tribute: grows with the bag; bonuses for a big bag, a near altar, low health, the endgame, no camp
  worth farming, and a bag that would reach the winning total.
- Recall: heal below 40% health with no enemy near, or spend a pile of gold with an empty bag. On
  spawn it holds until healed to 95%.
- Fight: kill steals on enemies under 25% health, otherwise only with a local power advantage
  (health times strength of what each side can see), plus a trade when an enemy in range is already
  hitting us.
- Flee: when the power balance is poor or health is low with an enemy near.

**Camp target** (`candidates.py` + `score_camp`). Candidates are the nearest reachable groups by a
distance field from our tile (one field, one read per group), excluding groups cleared recently or
respawning too late. Score = (gems + 0.15 gold) / bodies / (travel + wait + clear turns + 5), scaled
down by the damage the monsters would deal us relative to our health budget. Camps we cannot survive
alone are scored as a plan for `m` bodies (clear time divided by m, damage shared), discounted, and
zero if even the whole team could not. Allies already claiming a group count as bodies. Stacking on
an easy camp is penalised. Candidate lists are cached for 5 turns unless something happens.

**Fight target**: in range first, then lowest health, then biggest bag.

**Shop** (`actions.py`): shards of the body's build stat, Jungle family first (30% less camp damage),
then Monster, Power, Durability. Works on spawn and while dead.

**Draft** (`draft.py`): a safe pick is locked in before any analysis. Order: Vendetta, Varg, Ryth,
Marcella, Ren, then ranged picks. Small maps move Marcella and Ryth up. Draft memory carries the team
size, an aggression byte and the per-body shard stat.

## Execution rules worth knowing (`actions.py`, `navigation.py`)

- Fields are rooted on goal tiles and cached for the match; each step is one `next_moves_from` plus
  occupancy checks, with a reachable-tile fallback when the optimal steps are blocked (a sideways
  step of equal distance is allowed when it has a free step of its own, so units get walked round).
- **Camp spawn tiles are walls in every cached field** (`world.static_blocked`), because a route
  that runs through a living camp leaves the body standing still forever: this cost whole games on
  collision and rows in v1. Per-turn stepping uses terrain plus visible units (`terrain_blocked`),
  so a body can still walk onto a cleared group's tiles to capture it, and a body standing on a
  captured tile steps off it towards the goal. Distances to a group are read at its **anchor**, the
  walkable tile beside it nearest our spawn, built by `finish_init` over the first few turns within
  a fuel reserve (`FINISH_INIT_RESERVE`); groups without an anchor are not candidates yet.
- **Stall guard** (`bot.py`): a body that neither moves nor attacks nor casts for `STUCK_TURNS`
  turns, while not capturing or holding for allies, drops its target, shuns that group for
  `HOLD_AVOID_ROUNDS` and logs `stuck`. `tools/run_matches.py` counts those lines per side.
- Bodies approach camps to a ring beside the spawn tiles and never stand on them once a group is
  cleared: an occupied spawn tile delays the respawn (this deadlocked the whole team before).
- When the static ring is reached but some camps of the group are dead and the rest out of range, a
  dynamic field to the living camps is computed.
- A body whose plan needs allies waits beside the sleeping camp until enough are within 4 tiles.
- Melee: move then attack. Ranged: attack if in range, else move. Damage, stun and area abilities
  are cast on the current target when ready; attack resets give a second basic attack; dashes
  shortcut along the current field; shields fire when taking damage; Willow heals a hurt ally.

## Team memory (`memory_codec.py`)

Shared memory: one claim byte per body (2-bit status, 6-bit group index), byte 6 the last round an
enemy was seen. Draft memory: `HY1` magic, team size, aggression, map class, per-body shard stat.

## Fuel (release builds, cambc 3.0.1 / engine 1.0.0, 2026-10-20)

The first champion turn has about 4.5M after startup. `game_map.camp_groups()` plus building a
`Group` per group costs 2.3M on big-world (24 groups, 100x100) and 3.1M on islands (46 groups),
and a whole-map `DistanceField` costs about 80 fuel per tile (750K at 100x100). So the first
turn builds only the spawn field; the altar field and the per-group anchors come from leftover
fuel at the end of later turns (`finish_init`), per-altar fields are built one per turn when
`FIELD_BUILD_RESERVE` allows, and fight pathing uses small uncached local fields. If setup itself
runs out, the turn is suspended into round 2 and the `init:` line prints misleading numbers, so
check the `exh` column of the harness, not the log.

| | 00lanes (42 groups, 90x30) | 01big-world (24 groups, 100x100) |
|---|---|---|
| First turn, setup | 3.5M | 4.1M |
| Normal turn, average | 0.85M | 1.0M |
| Worst normal turn seen | 2.1M | 2.4M |

The worst turns are camp re-rankings (the from-me field plus one read per group) with enemies in
sight. `vision.camps()` is never called: it rebuilds every camp group object on each call.

## Results so far (release)

`python tools/run_matches.py hybrid hybrid@hybrid-v1` plays the working copy against the tagged v1
on every map from both sides; a change stays only if it wins more than it loses there. The v1
baseline against itself (replays/batch/baseline-v1-mirror) was a perfect mirror, 17-17, which also
showed that the engine is deterministic and that v1 banked under 350 gems from one spawn side on
collision, eight and rows. Against the starter bot: corners 1032-0, lanes 1008-430.

## The mode selector network (first trained head, 2026-10-20)

`features.build()` produces 66 inputs (the guide's 64 plus two more champion one-hot slots) from
`World`, `Turn` and `Decider` only. `nn.py` is the packed int8 engine; `weights.py` is generated by
`tools/train_mode_selector.py` and never edited by hand. Each turn the script scores the modes as
before, then, when `USE_NN_MODE` is on and fuel allows (500K for the pass plus a reserve that grows
with the map), the network scores the same masked options and takes over when its top two scores
are at least `MODE_SELECTOR_MARGIN` apart; otherwise the script's choice stands.

Pipeline, all under `tools/`:

1. `run_matches.py <bot> <opponent> --debug --log-limit 16000000` with `config.DATALOG = True`
   logs one `nnlog` line per body-turn (features as seen before the decision, mask, script
   scores, chosen mode) through `debug_block`, which is free in debug builds and never runs on
   the platform.
2. `build_dataset.py` joins those lines with the statistics JSON: each row is labelled with the
   gems that body tributed and collected in the next 60 rounds, minus a penalty if it died.
3. `train_mode_selector.py` trains 66-32-16-6 in PyTorch by outcome-weighted imitation of the
   script (examples weighted by exp(0.5 x standardised advantage, per mode), clipped), quantises
   after training to int8 weights, uint8 activations and an integer multiplier and shift per
   layer, checks the integer network against the float one, and writes `weights.py`.
4. `parity_test.py` checks `nn.py` against a numpy integer reference bit for bit.

First model: 548K rows from 84 game sides against v3 and v4; 98.0% agreement with the script on
held-out matches; forward pass about 350K fuel, feature pass 100 to 250K on top of the script. In
the harness against v4: 23-19, gem margin +123, deaths 481 vs 545, 5 fuel exhaustions. It plays
fragment badly (about 240 gems against 850), which the script does not.

The point of this first head is the pipeline: the data is the bot's own play, so the network can
only re-weight what the script already does. `USE_NN_CAMP` is reserved for the factored
candidate scorer (not implemented).

### Training on other teams' ladder replays

A platform replay holds the inputs only (draft picks and each champion's actions), so the state
has to be re-simulated. The visualiser ships the engine as WebAssembly with a timeline API, and
`tools/` drives it offline:

1. `fetch_replays.py --teams 15 --per-team 20` downloads the finished ranked matches of the top
   ladder teams with `cambc matches download` and writes `index.json` (map, teams, ratings,
   results). Needs `cambc login`.
2. `fetch_viewer.py` copies the engine wasm files and the decoder chunks out of a running
   `cambc view` into `replays/dev/viewer/` (re-run after a cambc upgrade; the replay's version
   bytes pick the wasm).
3. `replay_dump.py <zips or folder> -o data/replays/ladder --jobs 6` runs `replay_dump.mjs`
   (Node) per replay: it steps the engine one input at a time and, at every state that awaits a
   champion's input, reads that champion's perspective snapshot (exactly what the SDK would
   show), its vision buffer, every camp's health and the actions it then took. About a second a
   game. Seeking to each input instead re-simulates from a checkpoint and takes seven minutes.
4. `replay_features.py data/replays/ladder --index data/replays/raw/index.json --jobs 10 -o
   data/ladder_modes.npz` rebuilds `World`, `Turn` and `Decider` from each dump with the SDK
   stubbed out, runs the bot's own `features.build()` and candidate generation, infers the mode
   the other team's champion was in from what it did (attack a camp it stood next to, walk toward
   an altar carrying gems, fight, flee, recall, otherwise farm) and labels the row with the
   60-round outcome, the side's result and ladder rating. `--check <nnlog>` compares the
   reconstruction with the bot's own log of the same game: 89% of mode labels and 74% of inputs
   (within 2) agree, the rest being candidate-ranking details the dump cannot see.
5. `train_mode_selector.py data/ladder_modes.npz --winners` weights the winning and higher-rated
   side up, then quantises and exports as before.

The float model trains on inputs scaled to [0, 1], with penalties that keep the legal modes'
logits centred and small (`--logit-l2`) and with rows whose label the bot's own mask forbids
dropped (1.4% of ladder rows); the export folds the scaling into the first layer. Without this a
ladder-trained model produced logits around -8,000 that the int8 export could not resolve (36%
agreement with the float model, 3-67 against v4). The penalties must act on the legal modes only:
a masked mode gets no gradient from the loss, so a penalty over all modes is satisfied by pushing
the masked logits anywhere.

**Ladder model (2026-10-21):** 62 ranked games of the top ladder teams, 587K rows, winning and
higher-rated sides weighted up; 89.7% agreement with the inferred labels on held-out games, 99.5%
int8-to-float agreement. Against v4 in the harness: 46-24, gem margin +109, 42.7 gems per 100
rounds against 36.3, deaths 758 against 1,342, one fuel exhaustion. The own-play model under the
same bot code scores 39-31 (+25); head to head the two tie 35-35 with the ladder model +86 gems
and 754 deaths against 1,303. The bot now skips the network for the first six rounds and sizes
its fuel reserve from the worst act cost seen in the game (`NN_SKIP_ROUNDS`, `NN_ACT_MARGIN`),
which took exhaustions from 14 to 1. Training the same recipe on all 89 fetched games (876K
rows, only 36% from winning sides) scored 40-30 (+47), so the 62-game weights are the ones kept;
the extra games were mostly losses of the top teams, which the loser weighting does not fully
offset.

## Map pool

`cambc maps pull` fails with an HTTP 303 from the platform (CLI 2.2.5). The eight maps missing from
the `cambc init` set (ambush, contest, eight, fragment, pilgrimage, rushbait_v2, stampede, swirl) were
recovered from downloaded ladder replays, which embed the map verbatim after a 14-byte header; the
length was found by binary search on the engine's parse errors (6 = truncated, 7 = trailing bytes).
All 17 platform maps run without errors for 300+ rounds.

## Week 2 patch (engine 1.0.0, 2026-10-20)

The rules changed under us: wolves pay 4 gems and 40 gold (were 22 and 90), elks 15 and 90, gem
clusters 7, kills pay 100 gold, elks and golems reach 4 tiles and monoliths 5, wards recharge over
10 turns, altar locks take 5 hits, there is a Golem camp (5,000 health, 120 strength) and a
twelfth champion, Heart of Ruin. Games now run 1,100 to 2,000 rounds at roughly 40 to 70 gems per
100 rounds, and a side dies about 10 to 15 times a game. The bot reads camp values and ranges
from the SDK, so the amendments were to the tuning: `TRIBUTE_BAG` 45 to 24, `GOLD_VALUE` 0.15 to
0.1, the draft order and build tables (Heart of Ruin, Marcella up), the altar chosen by walking
distance (the straight-line choice sent whole teams to an unreachable altar on islands), and the
fuel budgeting above. Sweep against the v1 tag on the 21-map pool: 31-11.

## Known gaps

- **01starvation scores nothing.** Holes wall the spawn pocket off; the only walking route to any
  camp or altar is about 100 turns, beyond `MAX_CAMP_TURNS` (60), so no camp ever qualifies and
  every body sits on the spawn (v4) or wanders (the network, which the stall guard then logs).
  Dashes cross holes (the hole strip south of the pocket is one tile wide), so the answer is
  dash-aware routing, not a bigger search radius: a whole-map search with a soft score floor was
  tried on 2026-10-21 and lost 29-41 against v4 overall while still scoring 0 there.

- **Deaths.** Down from 14 to 8.5 per side per game after the 2026-10-20 tuning (recall at 50%
  health, no new camp below 45%, kill steals below 40%, and a retreat from camps that would
  finish us: camp deaths fell from 322 to 55 per sweep). What remains is almost all champion
  kills of bodies already in flee mode: fleeing at walking speed from melee does not work, and
  fleeing towards allies tested worse. `tools/death_report.py` breaks a sweep down by killer,
  mode, health and map.
- **Crowding stalls.** `stuck` fires 20 to 50 times a game on concentrated-wealth and boss-battle:
  several bodies converge on one camp block with one free approach tile. The guard releases them,
  but a helper cap based on the block's free ring would avoid the wait.
- **Contest, boss-battle, gold-rush and elk-arena bank little or nothing.** Their gem sources are
  monoliths, golems or elk blocks that the scorer refuses for a solo body and the cooperative plan
  rarely triggers. Taking them needs an equipped team and a rotation plan.
- **Hooks and corridors from side B** bank under 250 gems while losing 20 to 40 bodies: the team
  fights at a disadvantage instead of farming elsewhere.
- Elks and wolf packs are only taken when the cooperative plan triggers; a body that waits 25 turns
  for allies gives up on that group for 60 rounds.
- Wards are only dropped during captures. Altar locks and teleport crystals are never bought or used.
- Ranged champions farm slowly (no move-and-shoot); the draft avoids them where it can.
- Camp rewards are assumed to split evenly; the lowest ID gets the remainder in reality.
- Tools still missing: `fuel_report.py` (the harness reports peak fuel and exhaustions per match,
  which has covered the need so far). The weights export lives in `train_mode_selector.py`.
