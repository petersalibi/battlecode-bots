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

### Dashes cross holes (2026-10-21)

Holes cannot be walked on but a dash (Issac, Vesper Vale, Vendetta, Ren, Heart of Ruin: two
tiles, cooldown 8, compatible with walking) crosses them. For a champion with a dash, `World`
finds the thin holes, those with walkable tiles on opposite sides (four bitboard shifts, about
130K fuel, run from `finish_init`'s leftover fuel because the first turn is full on the big
maps), and removes them from `static_blocked`, so every cached field routes across them. Stepping
still never enters a hole: when the field's best step is a thin hole, `step_along` dashes to the
best landing in the ability's range square (`valid_tiles`, filtered by the blocked board; the
engine rejects a landing with no legal path and the next is tried) and then walks on from the
landing. On cooldown the body waits at the edge, which the stall guard tolerates (10 turns).

The gain is large wherever holes wall camps off. Camps within 60 turns of our spawn, walking
against with dashes: starvation 0 to 14, islands 30 to 114, shortcut 19 to 82, fracture 23 to 80,
concentrated-wealth 10 to 40, corridors 41 to 53. On the first draft turn (ten times the budget)
`draft.py` counts both and, where dashes at least double the reach, drafts the dash champions
first (`DASH_DRAFT_PRIORITY`); the flag travels in the plan. Starvation went from 0-0 draws to
1008-0 wins. Against v5 the whole package scores 39-31 (+63 gems) with no fuel exhaustions, and
54-16 (+241, deaths 763 against 1,363) against v4.

### Deaths (2026-10-21)

Mirror play cannot show how we die on the ladder, so the ladder replays were dumped and read.
Three findings became code: `SPAWN_GATE` (on spawn, a stronger enemy group within 10 keeps the
body home: recall mode shops and waits), `FLEE_GROUP` (two or more enemies count as near three
tiles further out), `FLEE_DASH_AWAY` (a fleeing dash champion dashes to the landing furthest from
the threats before stepping), and the capture standoff rule (`CAPTURE_FIGHT_BALANCE`,
`CAPTURE_STANDOFF_TURNS`). Mirror sweeps of the full set: 34-36 (-52 gems) against v6 and 46-24
(+83) against v5, level within noise; the standoff rule alone was 37-33 and 43-27. The judge is
the ladder: v6 sat at rank 450 (1,452) with 10 wins in its last 25, losing elk arena, collision and
contest by feeding 40 to 80 bodies a game. v7's first 50 ranked games: 28 wins, deaths per game
22.2 to 17.1 (`tools/ladder_deaths.py`).

**v8 (2026-10-21, engine 1.0.2): death memory and the spawn camp exit.** `DEATH_MEMORY`: a group
this body died at within 300 rounds scores half per death and is dropped after three
(concentrated wealth on the ladder: 45 of 100 deaths on one enemy-held cluster). The gate now
holds until the bodies on spawn have `SPAWN_BREAK_RATIO` (1.25) times the campers' power or
`SPAWN_GATE_MAX_TURNS` (60) pass; the first body to judge it posts a break-out signal in shared
memory byte 7 and every gated body leaves the same round in fight mode, so the team walks out
together instead of one at a time (swirl: full-health bodies died on the spawn tiles with four
enemies in sight, because the spawn blocks entry, not attacks). While gated a body stands on the
spawn tile furthest from the campers. Mirror: 34-36 against v7 (deaths 1103 vs 1154), 33-37
against v6; the problem maps (swirl, elk arena, collision, packs, hooks) 6-4 with +98 gems and
deaths 241 vs 301. Ladder, first windows (`tools/fetch_ours.py`): v8 4 of 10, v9 7 of 10, v10 12
of 20; rank 459 to 407 over the night. Near-spawn deaths fell to 2-5% of deaths in the v8 and v9
windows, but against stronger teams (rank 363-402) swirl, collision and elk arena still cost 53
to 56 deaths a game: full-health bodies flip from flee to capture with three to five enemies
beside them and die within five turns.

**v11: outnumbered rule.** Two or more enemies within eight tiles and our local power below
`FLEE_POWER_RATIO` times theirs: flee as a hard rule above the network, and keep fleeing while
they are in sight and our power is still below theirs (`OUTNUMBERED_RULE`,
`OUTNUMBERED_STAY_BALANCE`). The ladder losses above were full-health bodies flipping from flee
to capture beside three to five enemies. Mirror: 38-32 (+18) against v10 with deaths 940 vs 1187,
35-35 against v9 with deaths 958 vs 1071. Ladder, first hour: 12 of 25 against ranks 361-425,
deaths per game 14.2 (v10's hour: 24.5), kills 20.8; swirl still 41 deaths with 22 near spawn.
After three hours: 48 of 85, deaths 14.6 vs 18.7 per game, rank 358 (1,507); the night began at
rank 459 (1,440).

### Dash maps: stalls (2026-10-21, v10)

Islands, concentrated wealth and fracture logged 36 to 51 stalled bodies a game. Dumps showed
three shapes: a body in a wide hole field crossing one thin hole per dash cooldown and idling
between, bodies walled off from an embedded group by another group's camps, and a body standing
at a group it had already cleared. Three fixes: `DASH_SAVE_STEPS` (no speed-up dash when the
route crosses a thin hole within six steps, so the dash is ready at the hole), waiting on the dash
cooldown at a hole no longer counts as a stall, and a body that cannot step towards its group
hits whatever camp is in range (`clearing` in the trace). Islands stalls 40 to 1-3 a game,
concentrated wealth and fracture roughly halved. Sweeps: 40-30 (+29 gems) against v9 and 43-27
(+95, deaths 1093 vs 1210) against v8.

## Camp targeting, measured (2026-10-23, engine 1.0.3)

The handbook says only that an awake camp attacks "the nearest champion" until it "does not take
damage for 2 turns". Both teams ran `bots/campprobe` on rushbait-v2: nobody attacks a champion,
one striker per side hits a chosen wolf on a schedule, and the other bodies stand on scripted
tiles around it (both teams, across the hole column, at Chebyshev ties that Euclidean and
Manhattan distance would break differently, and with positions swapped). `tools/camp_target_probe.py`
reads the replay, lines up each turn-start health loss with the end-of-turn tiles of the round
before, and scores each candidate rule. One rule fits all 103 scored rounds:

- An awake camp hits the champion nearest it by **Chebyshev** distance among those in its reach,
  of either team. It does not stay on whoever hit it: a body that steps closer takes the hits from
  the next round, enemies and bodies across holes included. Euclidean and Manhattan were refuted
  in rounds where they named the other champion.
- **Ties** go to the champion that last damaged the camp (it kept the enemy striker even though a
  lower ID stood at the same distance), else to the tied champion that moves first that round:
  the starting team alternates each round, so a tie between teams flips every round; within a
  team the lower draft slot wins whatever the tiles (priority was never set, so it may be
  priority first).
- A camp **sleeps after two rounds with no champion in its reach**, however long ago it was last
  damaged: a wolf kept hitting a striker that stood in reach for eleven rounds after its last hit.
  The handbook's "no damage for 2 turns" is wrong; its "two rounds without an in-range champion"
  line is right. A sleeping camp does not attack a champion standing beside it.
- A hit during round r wakes the camp; its first attack lands at the start of round r+1. Each hit
  is exactly the camp's strength times the champion's `camp_damage_multiplier` (1.0 base).

What this means for the bot: `world.camp_damage_incoming` counts every living camp with us in
reach, but a camp only hits us when we are its nearest champion (an ally or enemy standing closer
takes it), and the fight rules (`power_balance`, `should_stand`, the flee step) ignored camps
altogether until the camp-aware fights below. Whoever hits a camp keeps it on a tie, so a body can draw a camp off an ally by
standing one tile closer, and an enemy who walks in closer than us takes the camp's hits. Stepping
out of reach for two rounds is enough to put a camp to sleep. Only wolves were tested; elks and
golems are expected to follow the same code. Rerun with
`cambc run campprobe campprobe --map 00rushbait-v2 --rounds 185 --release -o replays/campprobe`,
then `python tools/camp_target_probe.py <bundle>.repl26a`.

### Camp-aware fights (2026-10-23, `CAMP_POWER`, `CAMP_CONTACT_GUARD`)

Before this the fight rules saw champions only: a body trading evenly with an enemy while a wolf
pack chewed on it read as a fair fight, `should_stand` counted champion damage only, and the flee
step stepped away from enemy champions only (camps were avoided only with no enemy in sight).

**Who a camp is on** (`World._refresh_camp_targets`, run from `_refresh_camps`). Using the rules
above, each awake group with us in reach is judged at its camp nearest us:

- *safe*: a champion that has already moved this round (`has_completed_turn`) stands nearer the
  camp than us, or one yet to move is nearer by more than it can change (`CAMP_CLOSE_STEPS`, plus
  two with a dash);
- *on us for certain*: nobody, moved or not, can end the round as near as us;
- *maybe on us*: everything else (a tie, or a champion yet to move within a step of it).

The result is `camp_on_us` and `camp_maybe_us` (damage a turn), `camp_threats` (the camps' tiles)
and `camp_on_them_eff` (power the visible enemies lose to camps of ours that are on them). We
cannot know for certain in every case: enemies that move after us this round pick their tiles
later, a champion hidden in a bush is unseen (camps see through bushes), and a hit on the camp
we did not see would change a tie. Our own end tile is taken as where we stand.

**Where it is used.**

- `power_balance` (the scripted fight rules: entry, staying, flee, the outnumbered rule,
  commitment, capture standoffs): our side loses camp damage x `CAMP_THREAT_TURNS` (5) x our
  damage, a maybe at `CAMP_MAYBE_WEIGHT` (0.5); the enemy side loses `camp_on_them_eff`.
  `power_balance_raw`, the networks' input, is untouched, so the trained heads see what they
  were trained on.
- `should_stand`: camp damage counts only against standing (how long we last where we are),
  not against the walk home, because camps never chase.
- The flee step and the recall's step away add the camps on us to the threats, so a retreat
  leaves their reach as well (two rounds out of reach and a camp sleeps).
- `CAMP_CONTACT_GUARD`: the contact head decides first with an enemy within 4 and cannot see the
  camp; its attack or close is overruled (the rules decide) while a camp is on us and the
  camp-counted balance is under `FLEE_POWER_RATIO`.
- `camp_damage_incoming` is unchanged (it is a feature of the mode selector).

**Fuel.** Native calls cost about 5-10K and Python loop iterations 4-5K in this runtime. The
first version scanned every visible camp each contact turn and cost lanes 100-230K a turn (24
fuel exhaustions over two games against 5). The shipped version runs only when a living camp has
us in reach with an enemy in sight (the list comes free from the existing reach loop), makes one
`awake_camp_tiles` call and one awake test per group (a group wakes together), judges at most
`CAMP_MAX_GROUPS` (2) groups, looks only at champions within twice the reach plus slack, and the
hidden-bush test was dropped (about 20K). Turns without a camp on us cost nothing beyond a few
attribute reads (class defaults on `World`). Sweep exhaustions: 74-76 for us against 67-68 for
the opponent in the same games and 54 in the v42 mirror; the excess is on lanes (7 a game
against 2-3).

**Results (mirror harness, 80 games each, 2026-10-23).** The control first: the same code with
`CAMP_POWER = False` is not an exact mirror of v42 (43-37, deaths 1337 vs 1326), because the
dormant `if` tests shift fuel and so the network gates, as `CLAUDE.md` warns. So both a sweep
against that switched-off copy and one against v42 were run:

| Version | vs same code switched off | vs v42 |
|---|---|---|
| switched off | | 43-37 (+17 gems, deaths 1337 vs 1326) |
| `CAMP_POWER` | 42-38 (-30, deaths 1398 vs 1330) | 44-36 (+13, deaths 1356 vs 1348) |
| `CAMP_POWER` + `CAMP_CONTACT_GUARD` (shipped) | 43-37 (-10, deaths 1310 vs 1336) | 45-35 (+43, deaths 1289 vs 1353) |

All inside the harness's plus or minus five games. The guard version is the only one ahead on
both sweeps with fewer deaths, so it is on, but the mirror cannot show the case it is for (strong
teams fighting us beside camps): judge it on the +200 band and the ladder before relying on it.
Next steps if it holds: tune `CAMP_THREAT_TURNS` (3-8), prefer a fight target a camp is already
hitting, keep the fight path out of awake camps' reach, and use the tie rule on purpose (step
one tile nearer a camp to take it off an ally).

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

1. `fetch_replays.py --teams 100 --per-team 10 --since <UTC ISO> --won-only -o
   data/replays/<engine>/raw` downloads finished ranked matches of the top ladder teams with
   `cambc matches download` and writes `index.json` (map, teams, ratings, results). `--since`
   starts at a balance patch and `--won-only` keeps the matches the listed team won. One folder
   per game version keeps rule sets apart. Needs `cambc login`.
2. `fetch_viewer.py` copies the engine wasm files and the decoder chunks out of a running
   `cambc view` into `replays/dev/viewer/` (re-run after a cambc upgrade; the replay's version
   bytes pick the wasm).
3. `replay_dump.py <zips or folder> -o data/replays/ladder --jobs 6` runs `replay_dump.mjs`
   (Node) per replay: it steps the engine one input at a time and, at every state that awaits a
   champion's input, reads that champion's perspective snapshot (exactly what the SDK would
   show), its vision buffer, every camp's health and the actions it then took. About a second a
   game. Seeking to each input instead re-simulates from a checkpoint and takes seven minutes.
4. `replay_features.py data/replays/<engine>/ladder --index data/replays/<engine>/raw/index.json
   --winners-only --jobs 10 -o data/replays/<engine>/modes.npz` rebuilds `World`, `Turn` and `Decider` from each dump with the SDK
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

**Post-patch data (engine 1.0.1, fetched 2026-10-20 evening):** 948 ranked wins of the top 100
teams from the two hours after the Heart of Ruin nerf, winning sides only (5.7M rows, mean
rating 1830), in `data/replays/1.0.1/`. None of the models trained on it beat v5:

| Training rows | vs v5 | vs v4 |
|---|---|---|
| all 658 games available at the time, winners only | 32-38 (-40) | 42-28 (+54) |
| those games, sides rated 1800+ (191 games) | 36-34 (+19) | 43-27 (+72) |
| all 948 games, sides rated 1800+ (479 games) | 28-42 (-14) | 37-33 (+24) |
| top 25 teams' wins over seven hours of 1.0.1 (180 games, mean rating 2117, 2026-10-21) | 36-34 (+19) vs v9, 36-34 (+8) vs v10 | |
| same set, sides rated 2100+ (295K rows) | 31-39 (-52) vs v9, 28-42 (-87) vs v10 | |
| shipped selector with the override margin at 0.25 / 1.0 logits instead of 0.5 | 46-24 / 42-28 vs v9, 35-35 / 33-37 vs v10 (level: v10 is itself 40-30 over v9) | |

So v5's recipe (62 games of the top 15 teams, both sides, winners weighted up) still stands, and
the spread between runs on similar data says a 70-game harness result is only good to about
plus or minus five games. Selection by rating matters more than volume; a bigger, better set
would be the top 15 to 25 teams over a longer post-patch window.

## The camp scorer (second head, 2026-10-21)

Which camp group to farm. The script's `score_camp` is a hand formula; the scorer ranks the
same candidate list (`candidates.py`, up to twelve groups in reach) from what strong ladder
teams actually farmed.

**Shape: bilinear, not an MLP.** `camp_features.py` builds a 20-input state vector (health,
power, bag, position, threat, game state) once and a 22-input candidate vector per group
(travel, wait, clear turns, expected damage, reward, kinds, allies claiming it, which half of the
map, how long since an enemy was seen near it, our own deaths there). A single linear layer maps
the state to one weight per candidate feature, and a candidate's score is that weight vector
dotted with its features (`nn.Bilinear`). The guide's factored MLP (42-32-16-1 per candidate) was
built first and measured at 5M fuel for twelve candidates: every requantised lane of a hidden
layer costs about 10K fuel per candidate, so per-candidate hidden layers are unaffordable. The
bilinear scorer costs 0.53M for twelve candidates (`bots/fuelbench/RESULTS.md`) and gave up only
1.6 points of accuracy (60.4% against 62.0% top-1).

**When it runs.** Not on the retarget turn: that turn already pays for the candidate build, the
script's ranking and an approach field, and the scorer on top exhausted the budget (14 to 100
exhaustions per ten games, invisible to the bot's own fuel counter because a suspended turn
resets it). It reviews the script's cached ranking the turn after (`Decider.review_camp`, from
`actions.farm`), over the script's best eight candidates only, and its choice persists across the
script's later retargets while the group stays a candidate (without that a body walked between
the script's pick and the scorer's every ten turns: 36 target changes in one broken-curve game).
The review's cost is kept out of `act_peak`, because the mode selector sizes its reserve from
the worst act and a 0.8M review on top had silently switched the selector off (23-47 against v6
with it off, 34-36 with it on).

**Data and training.** `tools/replay_camp_dataset.py` turns the ladder dumps into rows at every
retarget a farming body would make (every ten rounds, not hitting a camp, inferred mode farm):
the state, the candidates, and the group the body attacked within the next 60 rounds; rows where
it died first, attacked nothing, or went to a group outside our list are dropped. 286K rows from
the 948 post-patch games, 159K of them winning sides. `--check` against a debug build's camplog
matched all 428 logged retargets of a lanes game (state features within 2 on 90%, candidate
features on 68%: the rest is the bot's belief against the dump's truth, such as respawn state and
early claims). `tools/train_camp_scorer.py` trains with a listwise cross entropy over the masked
candidates, the selector's penalties on the legal scores, winners weighted up, and exports through
`tools/weights_io.py`, which rewrites one head in `weights.py` without touching the other.

| Rows | Held-out top-1 | Nearest-candidate baseline |
|---|---|---|
| 948 post-patch games, all winners (bilinear, shipped weights) | 60.4% | 48.1% |
| same, factored MLP | 62.0% | 48.1% |
| same, rated 1800+ only | 61.5% | 48.0% |
| 89 games of the top 15 teams | 58.4% | 46.0% |

Offline the scorer disagrees with the script on 43% of winners' rows and is right on 49% of
those against the script's 18%, on every map. In the mirror it is level: 33-37 (+26 gems) against
v7 and 34-36 (-28) against v6 with the 0.3-logit margin, 29-41 and 40-30 with a 1.0-logit margin,
one fuel exhaustion in 140 games either way, so it stays off (`USE_NN_CAMP = False`) until the
fight and escape heads let a body hold what it is sent to. What
strong teams farm (contested middles, with escorts) is not always what a lone body of ours can
hold, which the mirror cannot show and the ladder can.

## The escape and fight heads (2026-10-21, both off)

Built in the same shape as the camp scorer (bilinear, one dot product per candidate) and both
level or worse in the mirror, so both stay behind their switches (`USE_NN_ESCAPE`,
`USE_NN_FIGHT`); the code, data tools and weights are in place for another attempt.

- **Escape head** (`escape_features.py`, `tools/replay_escape_dataset.py`,
  `tools/train_escape_head.py`): where a fleeing body steps. Candidates are standing still and
  the four neighbours furthest from the nearest threat; the label is survival over the next 20
  rounds, not imitation (459K rows, held-out AUC 0.75). In play it raised deaths: 33-37 against
  v8 with deaths 1220 vs 1054, 31-39 against v7, and a state-only bias term changed nothing. The
  likely reason is selection: bodies that stood still with an enemy near survived because they
  were not in danger, and a survival label on observed steps learns the situation's risk, not the
  step's value. The first version scored nine tiles with eight engine calls each and cost 540 to
  590 exhaustions a sweep; flee turns already carry an ally refresh and the mode selector.
- **Fight head** (`fight_features.py`, `tools/replay_fight_dataset.py`, trained with
  `tools/train_camp_scorer.py --head FIGHT_HEAD`): which visible enemy to attack. 178K fight rows,
  81.6% held-out agreement against a 79.8% nearest-enemy baseline: strong teams attack the nearest
  enemy almost always, so there was little to learn. 32-38 against v8 with equal deaths, 37-33
  against v7.

- **Death-risk lane** (`tools/train_risk_probe.py`, same day): a linear probe on the shipped
  selector's 16 hidden activations predicting death within 20 rounds, trained on 1.7M turns of
  both sides of the top-25 games (held-out AUC 0.78), appended as a seventh output lane so the six
  mode lanes stay bit-identical, and used as a gate: above a threshold only flee and recall are
  allowed. At a threshold flagging 25% of turns: 14-56 (-411 gems); flagging the riskiest 5%:
  17-53 (-221) with our own deaths up (829 vs 568). The readout is a function of the same trunk
  the selector already uses, so it adds no information, only a policy response (flee more) that
  strong teams' play says is wrong; the selector already flees where they fled. Off
  (`RISK_GATE`), weights reverted to six lanes.

- **Draft head** (`draft_features.py`, `tools/fetch_drafts.py`, `tools/build_draft_dataset.py`,
  `tools/train_draft_head.py`, `tools/retrain_draft.py`): 36 inputs (22 map features built from
  the engine on the first draft turn and stored in the plan, the pick index, the picks so far)
  to 12 logits, trained on 13,320 picks from the top 30 teams' 1.0.2 ranked games (held-out
  top-1 64%). Map features match the offline ones within two units on 20 of 22. It drafts what
  the top teams draft: Heart of Ruin first on every map, then Vesper Vale, Ryth, Marcella,
  Vendetta. In our hands that lost 27-43 (-131 gems) against v11 and 23-47 against v10: strong
  teams' picks suit strong teams' play, and our script plays Heart of Ruin and the dash
  champions worse than Varg and Vendetta. Off (`USE_NN_DRAFT`). The next version should learn
  from our own games with randomised drafts, where the label is our margin, not theirs.

- **Self-play draft** (`tools/selfplay_drafts.py`, `tools/train_draft_value.py`, same day):
  the bot's draft gets an exploration switch (`DRAFT_RANDOM`: each pick is a random allowed
  champion with that probability, seeded from a per-sweep salt and the map, data runs only). The
  driver plays sweeps against a fixed tag, reads each game's roster and gem margin from the
  harness, and a value head (36 inputs, the draft layout with the full composition set, -> 16 -> 1)
  learns our expected margin for a composition on a map. The draft is then planned greedily on
  the first draft turn (the later turns have a tenth of the fuel) and carried in the plan.
  5,250 games: 3,150 fully random (we win 16%) and 2,100 exploring 25% around the list and then
  around the head's own drafts. Held-out correlation with the margin 0.54; the quarter of
  compositions it rates best average +27 against -217 overall. In play it is level with the hand
  list after two iterations: 35-35 (+33) and 36-34 (+35) against v11, 32-38 (+9) and 31-39 (+18)
  against v10 with fewer deaths, but about 35 exhaustions a sweep against v11's 10, in the
  first hundred rounds of bodies on compositions with two ranged dash champions. Off
  (`USE_NN_DRAFT_VALUE`); the loop is the first working self-play path and is cheap to continue.

**Draft for patch 1.0.2** (same day): Vesper Vale moved up and Heart of Ruin and Ren down in both
priority lists, after the patch cut Heart of Ruin's strength to 30 and Ren's stun damage and gave
Vesper Vale and Vendetta more health. 41-29 (+48 gems, deaths 1130 vs 1216) against v8 and 38-32
(+44, deaths 1047 vs 1217) against v7.

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

- **Islands runs out of fuel once or twice a game** with the dash draft (six bodies, 46 groups,
  878 holes): the script's own refresh, re-rank and act can add up to 2.9M in one turn. The
  network's reserve grows by 40K per group beyond 30 there, which removed the exhaustions in the
  sweeps, at the price of the network running less on maps with many groups (lanes, islands).

- **Deaths.** About 17 per side per game in mirror play; top ladder winners lose 5 to 6 and
  losers 12 to 14. On the ladder our losses average 28 deaths a game. The 2026-10-21 pass found
  the pattern in our own ladder replays rather than in the mirror: a team camping our spawn
  exit (elk arena: 79 deaths, most within 8 turns of spawn at full health with three enemies in
  sight) and a 1,800-round standoff on a captured region (corridors, 0 gems). Fixes: a spawn
  gate (stay on spawn while a stronger group is in sight: it heals and they cannot enter), a
  group-aware flee range, a dash away from pursuers, and on a contested region fight the
  intruder unless clearly weaker, else give the region up for 60 rounds. Threshold tweaks alone
  (flee at 40%, flee at parity, recall at 60%, fight only at 2x) all tested level or worse in
  the mirror, and a penalty on camps near remembered enemies cut deaths but cost more gems than
  it saved. `tools/death_report.py` breaks a sweep down by killer, mode, health and map; the
  replay dumps of our ladder games (`data/replays/ours/`) show the ladder-specific patterns.
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
