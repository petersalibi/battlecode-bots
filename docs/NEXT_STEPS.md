# Next steps for the hybrid bot

Written 2026-10-21, after submission 7. Deadline early November 2026. Team work (rotation plans,
shared blocks, escorts) is deliberately last: everything here improves one body's own decisions,
in the order they sit behind the main decision, the mode selector.

## Update 2026-10-22: v12 to v33 (read this first)

The sections below this one were written for v7 to v11. Since then the work moved from the
mirror to **unranked challenges against the +200 band**: 14 teams rated +170 to +230 above us,
`tools/challenges.py --band LOW,HIGH`. Games are deterministic, so `--replay-from
data/challenges/v14-band200 --limit 14` replays the same 70 (team, map, side) games for every
version, which makes before/after runs exact pairs. Challenges always play the live submission,
so submit first and wait for the run to finish before submitting again. Quota is 50 groups per
rolling hour. The full analysis is in the "Band Gap Analysis" Claude doc
(https://claude.ai/artifact/8UR65wjhKK3UgifhLuSmTc).
How to run the band tests and the comparison and analysis scripts: `tools/analysis/README.md`.

**Lesson: the mirror misleads on fight judgment.** Calibration 0.5 lost 34-44 in the mirror and
doubled band wins. Use the mirror only to catch regressions and exhaustions. Judge anything about
enemy behaviour on the band.

### Where we are (v33, c9cc289, live)

- Band wins on the same 70 games: v14 6, v26 12, v28 15, v31 16, v33 14. That's a plateau at
  14 to 16 over v28 to v33.
- Ladder since v28: 32-33, deaths 11.9 a game against our opponents' 18.9, rating about 1,631,
  rank about 207 (1,512 and rank 370 at v11). 1,750 needs about 60% against opponents near our
  rating.

### What worked (kept, in order of gain)

| Version | Change (config switch) | Evidence |
|---|---|---|
| v13 | Builds copied from top-25 winners' purchase orders (`BUILD_ORDER`), turn priority (`TURN_PRIORITY`) | 57-21 vs v12, deaths -30%. Biggest single gain of the project |
| v12 | Party captures, remembered camp damage, deferred retargets, leaner first turn | 45-33 vs v11 |
| v14 | Stand and fight when fleeing would cost the duel anyway (`STAND_GROUND`, 50% health floor), melee strikes back while fleeing (`FLEE_STRIKE_BACK`) | 52-26 vs v13 |
| v15 | Ward whenever the charge is back (`WARD_OFTEN`) | Band 5 to 8 wins, banked by r1000 142 to 188 |
| v18 | Byte-6 call age fix (`memory_codec.CONTEST_EPOCH = 4`, was 32: calls read as up to 31 rounds old) | Bug fix |
| v20 | Opening: fight beside an ally within 6 that has an enemy within 3 (`PAIR_JOIN`) | Band 7 to 10 |
| v21 | Fight steps path over live terrain, not every camp tile as a wall (`FIGHT_LIVE_FIELD`) | Idle in contact 28% to 26% |
| v22 to v23 | No shopping recall in contact (`NO_RECALL_IN_CONTACT`), bodies held on spawn hit enemies in reach (`SPAWN_STRIKE`), first turn ends quietly under 150K fuel (`FIRST_TURN_QUIET`) | Idle in contact 17% (band 16%), round-1 exhaustions gone |
| v25 to v26 | Per-champion power from the kits (`CHAMPION_POWER`, `CHAMPION_KIT`) plus calibration (`POWER_CALIBRATION 0.5`) | Band 7 to 12 |
| v28 | Cast spells already in reach before stepping (`CAST_BEFORE_STEP`) | Band 15 |
| v30 | Forced flee below 45% health (was 30%), recall below 60% (was 50%) | Fewest deaths (18.7) |
| v31 | Capture a group we just cleared right away (`CAPTURE_OWN_CLEAR`) | Band 16 (best) |
| v33 | Mode selector retrained on the band's own games (`data/band/*.npz`) | Band 14, best early banking. Kept live |

**How the kit power works.** Every ability slot has an 8-turn cooldown and a champion casts
once a turn. A spell dealing k times focus therefore adds k/8 focus per turn, an attack reset
adds 1/8 strength, Varg's splash adds 1/4, and shields count as extra health. Per champion:
damage per turn = strength × S + focus × F, effective health = hp + focus × shield (table in
`config.CHAMPION_KIT`). Checked on 129K band contact samples (scratchpad `weak/calib.py`), kit
power separates won and lost exchanges better than strength alone (AUC 0.860 against 0.828).
Kills only match deaths at an estimated balance of about 2, hence calibration 0.5. The selector
still sees the raw strength-only balance (`power_balance_raw`), because that's what it was
trained on.

### What failed (switched off or reverted; don't retry without a new reason)

| Idea | Switch | Result |
|---|---|---|
| Early camp pairs as a score nudge | `EARLY_PAIRS` (on only with the elk bonus) | Lost alone. Real pairing needs coordinated movement, not a score bonus |
| Opening alert: call allies to a sighting | `EARLY_ALERT` | No fewer early deaths (2.35 vs 2.41), banked by r300 fell 36.7 to 27.5 |
| Leave the camp early when an enemy is sighted | `EARLY_LEAVE` | 29-49 vs v14 |
| Short holds | `EARLY_HOLD_MAX 0` | Nothing over v15 |
| Party captures (working version) | `CAPTURE_PARTY` | 32-46 vs v15 |
| Regroup with allies | `REGROUP` | 35-43 |
| Multi-trip boss plan | `BOSS_TRIPS` | Lost. Winners chip bosses from r800 with itemised champions |
| Flat focus weight 0.25 | `FOCUS_POWER_WEIGHT` (superseded by kits) | Band 8 |
| Chase fleeing enemies | `CHASE` (on, kept with v28) | Band 10 on its own |
| Calibration 0.4 | | 30-48 |
| Danger weight 0.5 | | 26-52 |
| Casting while capturing | | 34-44 |
| Flee at 55% health | | 35-43 |
| Stand down to 20% health | `STAND_FUTILE_MIN_HEALTH` | 32-46 |
| Gold worth 0.2 of a gem | `GOLD_VALUE` (v32, back to 0.1) | Banked by r300 24 against 35 |
| Selector trained on band rows only, or outcome-weighted (beta) | | Worse than base plus band rows |

### What is still open, and why it needs a design discussion

1. **Damage traded in contact: 0.5 for us against the band's 2.3.** Damage per hit is equal.
   37% of our contact time is fleeing, and fleeing bodies attack 1.6% of the time. The band's
   retreats take 7 damage a turn and 3% die. Ours take 26 a turn and 17.5% die. Every
   single-body rule tried (stand, chase, flee earlier or later) either didn't move it or added
   deaths. The band fights as a group and picks its moment. One body can't decide that alone.
2. **Gathering: 464 gems against 936.** The band earns about twice our gems from the same five
   champions and dies half as often, so the draft isn't the gap. Gems per camp hit are 0.85
   against 1.14. Steals are stuck at 16%, and two thirds of them are contested camps the band
   finishes after driving us off.
3. **Contested-middle maps.** We're 0/7 on corridors (all 12 groups on the centre line: 107
   gems against 1,034, while dying less), and weak on packs, eight and starfish.

**Proposed next step: a team-level planner.** The whole team, or fixed pairs, shares one target,
moves as a unit, and decides together when to fight and when to leave. Shared memory is the limit
(the claims board plus byte 6). The likely shape is a leader body that writes the plan and
followers that read it. This touches `decide.py`, `actions.py` and `memory_codec.py`, so agree on
the design with Tyler before building it.

### Squads: built 2026-10-22, in the code behind `SQUADS` (off)

What was built (design agreed with Tyler: pairs by default, whole team on contested-middle maps,
hard follow, no new memory layout):

- **Engine fact first**: allies out of our own 8-tile vision are opaque. `cambc.champions(ALLY)`
  lists them, but `position()`, `health()`, `strength()`, `gem_count()` and `state()` all raise
  "Unit is not in vision"; only `is_alive()` works team-wide. So a plan can only be expressed in
  camp groups through the claims board, never in positions.
- **Squads by slot** (`world._init_squads`): `SQUAD_SIZE` 2, the remainder joins the last squad
  (5 bodies: 2 + 3); everybody in one squad when `SQUAD_ALL_MIDDLE` (80%) of the groups are about
  as far from each spawn centre (corridors, reverse spiral, rushbait: 100%; starfish 75%, lost
  world 70%, hooks 50%). Every body computes the same split, so nothing is shared. The
  centre-line count must wait for `finish_init`'s leftover fuel: on the first turn it ran
  islands, boss battle, shortcut and yellow brick road out of fuel.
- **The leader's claim byte is the plan**: leader = lowest slot in the squad alive and not
  BUSY-with-no-group (`world._refresh_squad`, one `is_alive()` per mate per turn). FARM/IDLE + G:
  go to G (IDLE = holding for the rest). CAPTURE + G: wait beside it (`SQUAD_CAPTURE_WAIT`).
  BUSY + G (`actions.flee` writes it): retreating from G; followers retreat once it has lasted
  `SQUAD_RETREAT_ROUNDS` (2) rounds, flee with enemies in sight, stand without. `actions.fight`
  keeps the claim so the plan survives a fight. Help calls (IDLE holds) count only inside the
  squad.
- **Leader plans for its squad**: candidates' clear time and damage use the squad's available
  bodies (`candidates`), `score_camp` caps the helper plan at the squad (`max_bodies`), a group a
  squad mate claims scores ×`SQUAD_JOIN_BONUS` instead of ×`CLAIMED_PENALTY`.
- **Hard follow** (`decide.choose_camp`): a follower takes the leader's group when it is a live
  candidate within `SQUAD_FOLLOW_TRAVEL` (25) turns; a leader change triggers one extra retarget;
  the rally is the existing hold-for-help (`actions.farm`), which ends once `SQUAD_WAIT_FOR` (1)
  mate is beside the camp. Follow outcomes and hold turns are counted in `d.squad_stats` and
  printed on the r100/r200... log lines with `SQUAD_LOG`.

What happened (mirror vs v33, 78 games each; `replays/batch/squads-v33b..e`):

| Sweep | Change | Result |
|---|---|---|
| a | as designed | 29-49, recursion bug in the squad give-up (fuel 5,701 exhausted turns) |
| b | bug fixed, helper plan capped at the squad | 36-42, gems/100 42.6 vs 45.9, deaths 820 vs 927, 64 exh (all round 1, the centre-line loop) |
| c | retreat only after 2 leader rounds, no target drop | 37-41, gems/100 42.0 vs 46.4, deaths 844 vs 845 |
| d | capture wait, hold for one mate, leader's group exempt from the follower's avoid marks, centre count off the first turn | 32-46, gems/100 41.0 vs 46.7, deaths 832 vs 887, 23 exh, stalls 1,370 (the exemption sends followers back into crowded camps) |
| e | exemption reverted | 28-50, gems/100 39.4 vs 51.3, deaths 879 vs 852 |

Why it costs gems: measured on dumps (true positions), squad mates were within 4 tiles 35% of
the time at first and 47-54% after the fixes; followers went solo at 40% of their retargets at
first (leader capturing, leader's group respawning or out of the follower's list, pair split by
a recall or a death on a big map: on expanse the pair was more than 25 tiles apart 19% of the
time) and still at 15-25%; a follower waits beside captures 4-13% of its turns and holds at camps
up to 11%. Two bodies on one camp halve the map coverage, and in the mirror that is a straight
gem loss while the safety gain only shows as fewer deaths. The per-map results swing by ±1,500
gems between sweeps (contest +2,000 then +200, hooks -1,826 then +1,672), so single games are
coin flips and the 28 to 37 wins are one reading: level to behind.

Not band-tested (Tyler's call: one submission plus 14 groups). The mirror has misled on fight
judgment before (calibration 0.5 lost 34-44 in the mirror and doubled band wins), but this loss
is on gathering, not fights, and the band gathers twice our gems with only slightly more grouping
than we had (ally within 4: 52% against our 43%), so a hard pair is more grouping than they do.
If it is revisited: a loose pair (the follower picks its own camp near the leader's, joins only
when the leader holds or an enemy is posted) keeps the coverage; the capture wait and the
one-mate hold are the parts that measurably helped togetherness.

**Also fixed on the way**: `decide.py` never imported `memory_codec`, so the v8 break-out signal
(byte 7) raised inside its try/except on both the write and the read and never fired. Now
imported, behind `BREAKOUT_SIGNAL`, off: alone it went 35-43 vs v33 with deaths 915 against 866
(`replays/batch/breakout-v33`), so the gated bodies leaving together is not an improvement as
the gate stands. With both switches off the code plays exactly as v33.

## Where we were at v7 (2026-10-21)


- **v7 on the ladder** (tag `hybrid-v7`): scripted bot, mode selector network trained on 62
  ranked games of the top 15 teams, dash-aware routing with a dash-first draft on hole-walled
  maps, and the deaths pass (spawn gate, group-aware flee, dash away, capture standoff rule).
- **First 50 ranked games of v7**: 28 wins (v6's last 25: 10). In the first 30, deaths per game
  22.2 → 17.1, kills 17.9 → 21.3, deaths within 8 turns of spawn 27% → 18% of all deaths.
  Opponent strength unchanged (mean rating about 1,450). `python tools/ladder_deaths.py
  data/replays/ours-v7/ladder --index data/replays/ours-v7/raw/index.json` produced those
  numbers; the ladder replay zips also carry our own `team-a.stdout`, so the mode log of any
  ranked game is readable without re-running it.
- **Measurement**: `tools/run_matches.py` plays 70 games in about three minutes, standard
  deviation about 4 wins. Run every candidate against both v5 and v6 (140 games) and treat
  anything under ±8 wins as noise. The mirror cannot show ladder deaths: what the ladder
  punishes (spawn camps, enemy-held camps, stacks pushing our side) never happens between clones,
  so ladder replays decide death work and the mirror only guards against regressions.
- **Data path**: `fetch_replays.py` → `replay_dump.py` → `replay_features.py` →
  `train_mode_selector.py`, about a second per game. 948 post-patch games of the top 100 teams
  are in `data/replays/1.0.1/`; more data from weaker teams made the selector worse.

## What the v7 ladder games still lose to

1. **Returning to an enemy-held camp.** concentrated_wealth vs dominos: 100 deaths, 0 gems in
   2,000 rounds, 45 of the deaths on one camp cluster the targeter kept sending bodies back to.
   fragment twice (37 and 42 deaths) with the same shape. `score_camp` has no memory of deaths.
2. **A whole enemy team at our spawn exit.** swirl: 64 deaths, 45 near spawn, their four bodies
   at our exit for 1,800 rounds. The gate holds bodies on spawn, but spawn tiles only block entry,
   not attacks: full-health bodies died on the spawn tiles with four or five enemies in sight, and
   the fight/flee rules flip every turn at balance ≈ 1 because four of us "match" four of them on
   paper while they have the items.
3. **A stack pushing our camps early.** collision, rows, split vs Rogue Noogers: few deaths but
   18 to 230 gems, because each body fought the stack alone at round 60, recalled, and came back.
4. **Stuck at holes on dash maps.** islands, concentrated_wealth and fracture log 38 to 67
   "stuck 10 turns" bodies per game even in wins: a body whose route crosses a hole waits out the
   dash cooldown instead of walking round or farming nearer.
5. stampede, rushbait_v2, contest, boss_battle: near-zero gems for both sides. Cooperative maps,
   out of scope for now.

## Ranked plan (individual components, behind the mode selector)

### 1. Death memory in the camp scorer (shipped in v8, 2026-10-21)

When a body dies, record its target group in the claims board (the plan bytes already carry a
per-body status; add the group and the round). `score_camp` multiplies a group's score by a factor
that decays over about 200 rounds per recent team death there, and skips the group entirely after
three. Later the same memory becomes a mode selector input (it is a feature of the world, not of
the script). Expected: fixes loss shape 1 outright. Mirror check: must not cost gems against v6
(a camp-near-visible-enemy penalty did, because clones contest every camp; a memory of actual
deaths should fire far less in the mirror).

Tried and dropped the same day: a team death memory through the claims board (a dead body posts
BUSY with its target group; allies count it at half weight) lost 31-39 (-83 gems) and 29-41 with
more deaths. In the mirror both sides die symmetrically, so shunning every group an ally died at
mostly costs camps; a ladder-only test would need a switch.

### 2. Spawn camp exit (first pass shipped in v8, 2026-10-21: far-edge stance, group break-out; the dash exit is still open)

The gate is right to hold bodies, wrong to hold them on the near spawn tiles and wrong to release
them one at a time. Pieces, in order of value:

- **Stand on the spawn tile furthest from the visible enemies** (out of ranged reach, 3 tiles,
  and of most abilities) while gated, and buy health and items there: the spawn heals, so a gated
  body should come out stronger than it went in.
- **Group exit**: the plan byte carries a "break out at round R" once the gated bodies' summed
  power exceeds the campers' by a margin; everybody leaves on that round towards the same camp,
  fighting the nearest camper together. Until then, nobody leaves.
- **Alternate exit** for dash champions: a landing across a hole or wall the campers do not
  cover, from `valid_tiles() & ~blocked` with the enemies' reach masked out.
- `power_balance` should count the enemy's items (visible in `champs[].items`) and recent
  damage taken, so four fresh bodies do not read as a match for four geared ones.

### 3. Fight/flee scorer (built 2026-10-21 as the escape and fight heads; both off: see the README)

An outcome label on observed steps learns the situation's risk, not the step's value; the next attempt needs counterfactual value (a simulator-rolled step, or regret from both sides' replays), and fight target choice barely matters (strong teams attack the nearest). What remains open from this item is the flee decision itself, which the mode selector owns.

The deaths that remain after 1 and 2 are bodies already fleeing, caught by faster champions, and
bodies that fought at parity and lost. The script's fight/flee rules are thresholds on
`power_balance` and health. Replace them with a small scorer over both sides' health, strength,
items, cooldowns, allies within reach, distance home and the dash state, trained from the ladder
replays: the label is what the winning side's bodies did in that situation and whether they lived
through the next 20 rounds. `replay_features.py` reconstructs all of those inputs already; the
recipe is the mode selector's (inputs in [0, 1], penalties on legal logits only, parity test).

### 3b. Camp deaths (swirl, 2026-10-21)

A body waiting for help at a camp it cannot clear alone fights it as soon as the camp hits it
(`farm`: the hold only applies while `damage_taken <= 0`), takes 120 a turn, and the overrun
check (`camp_overrun`) only runs below `CAMP_FLEE_HEALTH` (45%); by then walking out of the
camp's range costs more health than is left. Running the overrun check at any health as a hard
rule was far worse (27-43 vs v11, 23-47 vs v10, -100 gems: the formula flags most solo camp
fights at full health). The targeted fix, backing off when hit while waiting for help, was worse still (19-51 vs v11,
-179 gems): in the mirror the allies do arrive, and a body that backs off and returns never
clears the shared camps. Both dropped. What remains is the ladder-only case of a camp fight with
an enemy team arriving, which the outnumbered rule (v11) now covers once they are in sight.

### 4a. Stalls (triaged and mostly fixed 2026-10-22 evening)

The harness counted 700 to 1,400 ten-turn stalls per 78-game sweep. Classified on engine dumps
(scratchpad `stalls/classify.py`: for each `stuck` log line, the body's true position against
the target group's living camps, free neighbours, holes and allies), 465 stalls in the twelve
worst games:

| Share | Shape | Cause |
|---|---|---|
| 72% | Beside a dead camp of the target, the living one two or three tiles away, free tiles around | `_approach`'s fallback pathed over the static board, where every camp tile is a wall, so a living camp inside a cluster of dead ones (packs, gem fields, pilgrimage) was unreachable |
| 16% | Ranged body at the three-tile ring, camp in range by distance but not attacked | Line of sight: the ring is Chebyshev, the camp sits behind a wall (corridors) |
| 6% | Target group has no living camp | Stale state, walked to the ring of a cleared group |
| 3% | No target, cannot move | Standing inside a captured seven-camp cluster (boss battle): every neighbour a camp tile, so every cached field reads unreachable, no altar "reachable", and the body blocks the respawn: 1,200 rounds |

Fixes, all in: `LIVE_APPROACH` (the fallback paths over live terrain, dead camps walkable, to the
ring of the living camps in sight, else next to the group's unseen tiles: a neighbour is never out
of sight); `OFF_WALL_RADIUS` (a body whose tile and neighbours are all unreachable walks over live
terrain to the nearest tile its field reaches); `SEAL_ROUNDS` plus sealing only on an engine
refusal (a hole sealed from inside a ring of holes on big world trapped a body for 1,000 rounds:
the dash was "failing" because no landing improved the route, not because the engine refused).

Mirror vs v33 (`replays/batch/liveapproach3-v33`): 39-39, gems per 100 rounds 49.6 against 48.3,
deaths 828 against 900, stalls 158 against about 900 on v33's side (ours were 736 before).
Three sweeps of this work read 38-40, 40-38 and 39-39 with deaths 30 to 90 lower each time.
Corridors, packs and gem fields each went 2-0. On engine 1.0.3 (local cambc 3.0.5, 80 games
with road of riches): 45-35, gems per 100 rounds 51.8 against 48.3, deaths 848 against 951,
stalls 152 (`replays/batch/v34-v33-engine103`). Road of riches exhausts six first-turn bodies
a game on both sides: a big-map setup cost, not new.

**Submitted as v34 (f766a99, 2026-10-22 13:35 UTC).** Band replay (`data/challenges/v34-band200`):
11 wins against v33's 14 on the same 70 game slots, gathered 485 (best), banked by r300 44
(best), by r1000 217 (v33 238), deaths 20.4 (v33 19.1). Not a paired reading: engine 1.0.3
went live between the two runs, 17 games flipped (10 against, 7 for), and no patch notes were
published. Judge v34 on the ladder, or pay two submissions for a paired band run. What remains (48 stalls in the six worst games):
the dash-cooldown wait at holes on islands and shortcut (item 4 below) and a few stale cleared
groups on contest.

### 4d. Fight commitment and the ranged kite (built 2026-10-22 evening, submitted as v35)

Trace of a gold-rush leader at 80% health with two enemies in sight: flee, farm, capture, flee
every other turn, each flip a free hit (the outnumbered rule turned on and off with the enemy
count, the balance sat near 1). Built with the tuning pass in mind: every constant carries its
search range in `config.py`.

- `FIGHT_COMMIT`: a fight decision is held for `FIGHT_COMMIT_TURNS` (3) in `decide._commitment`,
  released early when the calibrated balance drops under `FIGHT_STAY_RATIO` (0.9), nothing is in
  sight, or health crosses `FLEE_HEALTH`; `script_scores` keeps the fight lane at 1.1 while a
  fight is on. The flee side (`FLEE_COMMIT`) is off: holding retreats for three turns turned
  flicker into real retreats and raised deaths (931 against 880 in the v34 mirror, 36-44).
- `OUTNUMBERED_TURNS` (2): the outnumbered rule needs two consecutive turns before it fires
  unless the body is already fleeing. This is what fixed the flicker without the flee hold.
- `KITE`: a ranged body in a fight with a melee enemy beside it dashes to a landing that keeps
  the target in attack range and `KITE_MIN_GAP` (2) from every melee enemy, then attacks. A dash
  is an ability, so it does not spend the ranged move-or-attack allowance (probe: dash then attack
  runs; step then attack raises "Attack already used").
- Mirror, 80 games each on engine 1.0.3: vs v34 43-37 (gems/100 50.6 vs 47.2, deaths 978 vs 957),
  vs v31 46-34 (49.5 vs 46.2, deaths 1012 vs 955), vs v28 48-32 (+185, deaths 784 vs 1141),
  vs v26 63-17 (+314, deaths 662 vs 1179). Exhaustions 31-35 (road-of-riches/big-world/ambush
  first-turn setup, baseline), stalls 137-163.

**Submitted as v35 (e8dd755, 2026-10-22 ~15:20 UTC).** Band replay queued to
`data/challenges/v35-band200`, paired with v34's engine-1.0.3 run (same slots, same engine):
paired with v34 (same engine), collected 15:39 UTC: **17 wins** (v34 11, v33 14, v31 16:
the best band run so far), gathered 577 (v34 485), banked by r300 48.6 / r600 125 / r1000 253
(v34 43.7 / 110 / 217), deaths 20.3 (v34 20.4). Contact turns (fightstats): attack rate 28% to
30%, damage dealt 1,639K to 2,103K against taken 3,267K to 3,429K, so the traded ratio moved
0.50 to 0.61; ranged attack rate in contact 38% to 41%. The ladder read at the same time: v34
32-18 over its 50 ranked games (v33 on engine 1.0.3: 20-25), rank 179 at 1,663.

### 4e. Flee rework and the ranked-game loops (built 2026-10-22 evening, after v35)

Contact study on v35's band run (tools/analysis/fightstats.py, contact_modes.py, flee_turns.py,
cornered.py): the band attacks on 43% of contact turns, we on 30%, on the same champions. Our
contact turns split fight 47% (58% attacks), flee 36% (1.7% attacks, 97 damage taken a turn,
a fifth dead within five turns), farm/capture 17%. 4,455 melee flee turns had an enemy
adjacent at the start and 245 hit it. A melee body at 30-45% with a melee chaser adjacent died
32-36% of the time fleeing and 9-15% fighting; escape distance barely changed the flee rate.

- `PARTING_SHOT`: a fleeing melee body hits the weakest adjacent enemy before stepping (the
  engine allows attack then move: probe, 147 such turns). Alone: 51-29 (+105) vs v35.
- Stand knobs for the tuning pass: `STAND_FUTILE_MIN_HEALTH` [0.25-0.5], `STAND_MIN_BALANCE`
  [0-0.9], `STAND_ESCAPE` [0-4] (a bush or spawn that close, or a ready dash, means run). The
  first tools/tune.py sweep of the health knob (0.5/0.35/0.25) was 33-47 / 28-52 / 34-46,
  all with a wrong first version of the rule (allies counted as an escape); unswept since.
- `DOOMED_RULE` (Tyler: a guaranteed death is spent on damage, not on running): dead within
  `DOOMED_TURNS` to the enemies already in reach, nothing safe that close, no dash: fight the
  finishable kill. Off: 41-39 with it against 51-29 without, deaths 947 vs 857. The chasers in
  the mirror give up more often than the rule assumes; retry with the real band.
- Ranked-game loops (Tyler's report, replays in data/replays/ours-v35): (1) yellow brick road
  vs ExCode: both 30-camp rows cleared by r100, ExCode parked one body at the far end of each,
  out of sight, so the capture never completed and two of ours waited 1,900 rounds in capture;
  three others paced two tiles for 1,000+ rounds beside a living gem cluster (the static
  field's walk-off and the live approach pull opposite ways on a dead camp tile). (2) reverse
  spiral vs :3 <3: the two spawns touch at the centre; our bodies brawled on spawn (the mirror
  scored 0-0 on this map) while the opponent farmed the outer ring; a body also stood in flee
  for 1,950 rounds from enemies behind the spiral wall. Fixes: `CAPTURE_BLIND_TURNS` (no reward
  and nobody in sight after 14 turns on the region: the lowest claiming slot holds it, denial,
  `CAPTURE_BLIND_HOLD`; the others leave; leaving both rows lost to v35 twice), a greedy step
  towards a living camp in sight before the walk-off (`actions._greedy_to_camp`; a live field
  there exhausted the 100x100 maps), `SPAWN_SITTERS_DISTANT` (an enemy on its own spawn is not
  a target nor, beyond distance 1, a threat), `THREAT_DETOUR` (an enemy in sight whose walk to
  us is much longer than the straight line, by the spawn-field difference or a small-map walking
  field, goes to `w.distant_enemies`), and the stall guard now counts pacing between two tiles
  (`OSCILLATION_TURNS`) and idle flee, so the harness's stall column sees these.
- Mirror vs v35, all 40 maps both sides: **51-29, gem margin +210, gems/100 61.3 vs 46.4,
  deaths 1047 vs 1189**, exhaustions 39 (baseline), stalls 234 with the new counters (corridors
  70, fracture 26, great wall 19). Reverse spiral 1015-387 both sides (was 0-0), yellow brick
  road 1-1. The log line now carries `src=` (the decision source) for contact_modes.py.
- **Submitted as v36 (851576c, 2026-10-22 17:45 UTC).** Band, paired with v34/v35 on the same
  70 games: **26 wins** (v35 17, v34 11), gathered 757 (v35 577), banked by r300 / r600 / r1000
  48.6 / 135.5 / 283.7 (v35 48.6 / 125.1 / 253.3), deaths 19.3 (20.3), dead 19.6% of turns.
- The real reverse-spiral loop (Tyler's correction: Ryth from r713, Marcella from r925, both at
  the top edge with 35 gems in tribute mode, and back into it after every flee): the opponent
  put an altar lock on the (2,2) altar at r376 (one of ours hit it four of the five times
  needed). `vision.altar_locks` only shows a lock within 8 tiles, so at x=10 the altar came into
  sight locked, the choice flipped to the far altar, one step east it was out of sight and
  unlocked again. Fix (after v36): locks are remembered once seen until the altar is seen bare
  (`w.altar_locks`), a locked altar costs `ALTAR_LOCK_PENALTY` turns in the choice instead of
  vanishing at the sight line, and a body at a locked altar attacks the lock (`actions.tribute`,
  five hits) instead of leaving. Lesson for every rule: anything that keys off vision-limited
  state at the sight boundary can oscillate; remember it or add hysteresis.
- Open: boss battle is scoreless (a body with 1,230 gems and no reachable altar idles 1,000
  rounds); the blind-contest give-up repeats every 60 rounds per body (`CAPTURE_STANDOFF_AVOID`
  is per body, not shared); we never place altar locks ourselves (10 gold, cancels an enemy
  tribute, costs them five attacks).
- **Submitted as v37 (d96bd94, 2026-10-22 17:57 UTC).** Band, same 70 games: 24 wins (v36 26,
  within the +-4 noise), banked by r300 / r600 / r1000 51.1 / 156.1 / 320.5 (v36 48.6 / 135.5 /
  283.7: the best banking curve of any version), deaths 18.9 (v36 19.3), gathered 703 (757).
- Bushes (Tyler's question, tools/analysis/bushes.py and unseen.py on the v36 band): both sides
  spend 10.5% of living turns in bushes, hide 36 (us) / 43 (band) times a game with contact
  broken 57% / 55% of the time; after the other side hides, we reach the bush 13% and walk away
  42%, the band 17% / 37%; a weak hider near us is killed within 6 turns 24% (band 29%). The band
  dies less after its own hides (6% vs 10%) and attacks from bushes a little more (17% vs 14%).
  Not a big lever. Cheap later: chase a weak target to its last seen tile for three turns when it
  vanishes within three tiles (the chase rule drops it the moment it is unseen).

### Where we are (v37, d96bd94, live 2026-10-22 17:57 UTC)

- Band wins on the same 70 games: v14 6, v26 12, v28 15, v31 16, v33 14, v34 11 (engine 1.0.3
  from here), v35 17, v36 26, v37 24. Deaths per game 19 to 20 throughout; gathering is the lever that
  moved (485 -> 577 -> 757 a game).
- Ladder: v34 64% over 50 games, rank 179 / 1,663. 1,750 still needs about 60% against the
  opponents that come with it.
- Open, in order: the parameter tuning pass (every new constant carries its [range]; harness
  paired gem margin with successive halving, then band-threshold fits, then ladder arms chosen by
  a hash of map, side and drafts), the dash-cooldown wait at holes (item 4), the damage traded
  ratio (0.61 against the band's own, see tools/analysis/fightstats.py), wards.

### 4. Routing at holes (looked at 2026-10-21; still open)

Hole sealing after three failed crossings with the dash ready fires a few times a game but the
stalls stay (fracture 51, islands 36-50, concentrated wealth 25-43 a game). Dumps show three
shapes: a body inside a wide hole field dashing one thin hole per cooldown and idling between
(fracture, 35,45 and 17,43), bodies walled off from an embedded group by another group's camps
(concentrated wealth 27,29; the clearing fallback now hits what is in range), and a body
targeting a group it has walked to that is already dead (fracture r283: `alive []`). Pieces:
save the dash for hole crossings when the route ahead has one within a few steps (today
`try_dash` spends it on any two-tile gain), let the stall guard ignore turns spent waiting on the
dash cooldown at a hole, and re-check the target's state on arrival.

A body whose next step is a thin hole and whose dash is on cooldown should take the walking route
if it is at most `cooldown` turns longer, else wait; and the camp scorer should price a route with
a hole crossing at walking time plus the expected wait. Kills the stuck counts in loss shape 4.

### 4b. Death-risk head (built 2026-10-21 afternoon, off)

A risk readout from the selector's own trunk cannot beat the selector's policy: the trunk already
encodes the danger, and the only thing the gate adds is more fleeing, which lost 17-53 even at a
5% flag rate. If risk is to help it must bring new information, which means a privileged teacher
(the full-map truth in the dumps: unseen enemies, their items) distilled into visible features,
and a soft use (a score adjustment the selector can outweigh), not a mask.

### 4c. Draft head (built 2026-10-21 afternoon, off)

Imitation of top teams' picks lost 27-43 and 23-47: their drafts suit their play. The same head
trained on our own randomised-draft games, labelled by our gem margin, would be a bandit over
compositions per map and is the cheapest real self-play target we have (a game's draft is one
decision, the harness plays 1,400 games an hour, and the pipeline is built). `tools/retrain_draft.py`
refreshes the imitation version after a patch.

Self-play version (same day): random and near-list exploration against v11, a value head over
compositions, greedy planning on the first draft turn. Level with the list after two iterations
(36-34 +35 vs v11, 31-39 +18 vs v10) with more early exhaustions; off. The loop works and is the
template for the selector trunk: exploration switch, fixed opponent pool, margin labels, retrain,
harness gate.

### 5. Camp scorer network (built 2026-10-21, off: level in the mirror)

Shipped in the code behind `USE_NN_CAMP` (bilinear, see the README). Level against v6 and v7
over four sweeps; its picks match strong teams far better than the script's, but a lone body
cannot hold the camps strong teams take. Revisit after 2 and 3, or with a value head.

`score_camp` is a hand formula with eight tuned constants; the ladder replays show which groups
strong teams actually farm from where. Candidate list and labels come from `replay_features.py`.
Only after 1 and 4, which change what the scorer should learn.

### 6. Cheap individual gaps

- Wards are never placed: a ward on the lane to our farm shows a push before it arrives
  (teammates' views are not shared, so each body only sees its own). Teleport crystals are not an
  escape: the channel is three owner turn-ends during which the body cannot act (rules, 2026-10-21).
- Ranged champions kite since v35 (`KITE`, a dash before the attack); a plain step-kite is still not possible (step then attack is refused).
- Hooks and corridors from side B fight at a disadvantage: an enemy-side penalty in `score_camp`
  when the enemy spawn is nearer than ours.
- Camp reward split assumes even shares; the lowest ID gets the remainder.

### 7. Fuel headroom (open; profiled 2026-10-21)

v9 exhausts 9 to 13 times per 70-game sweep (v7: 2). The exhausted turns are retargets: on
corners, selector 0.8M + candidate rebuild and re-rank 0.75M + approach field 0.27M; on
big-world, candidate build 0.94M (a whole-map field) + approach field 0.86M + re-rank 0.25M.
Two attempts failed: a bigger selector reserve on retarget turns alone changed nothing (the
big-map turns have no selector in them), and capping the approach field to the expected walk
doubled exhaustions and stalls (capped fields that miss the body are rebuilt uncapped). The real
fix is structural: build the candidate list from one field and reuse it for the approach, or
spread the retarget over two turns like the camp review.

Islands and lanes (40+ camp groups) sit at the 2.5M limit on bad turns and the network is
suppressed there. Cache camp candidate scores across the RETARGET_EVERY window and skip
`vision.camps()` rebuilds when nothing in view changed.

### 8. Better training data, not more

Top 15 to 25 teams over a longer post-patch window; weight by rating more sharply; hold out by
map and report accuracy per map.

### Scoreless maps are gold games (tried 2026-10-21, dropped)

At the 2,000-round limit a 0-0 game goes to lifetime gold earned, then kills, and each kill
pays 100 gold: on contest, stampede and boss battle every body fed is the tiebreak. A rule that
fled at anything under a 1.5 power edge, fought only at 2x or for a kill in reach, and valued gold
camps at half a gem per gold made it worse: 49 deaths on contest against 12 (v10) and 0-400, 4-6
on those maps, 34-36 and 32-38 overall with far more deaths. Fleeing bodies get chased down
and the gold camps on those maps sit in the contested middle. What those maps need is the
cooperative plan, not a timidity switch.

### Last: cooperation

Rotation plans for contest, boss_battle, gold_rush, elk_arena, stampede and rushbait, escorts
for tributes, and shared fights. The claims board already carries per-body status and group; it
needs a planner that assigns rather than one that only reports.

## Tuning sweeps that changed nothing (2026-10-21 night, all restored)

Selector override margin 0.25 and 1.0 logits (level), retarget every 15 turns (level,
exhaustions unchanged), `FLEE_POWER_RATIO` 0.85 (30-40 vs v11). The mirror's floor is about
plus or minus four wins; none of these cleared it.

## Process

- Mirror first (`python tools/run_matches.py hybrid hybrid@hybrid-v33`) to catch regressions and `exh`, then submit and replay the 70 band games (`python tools/challenges.py --replay-from data/challenges/v14-band200 --limit 14`) to judge. Band wins decide fight and behaviour changes, not the mirror.
- After each submission, fetch our own ranked games into `data/replays/ours-vN/` and run
  `tools/ladder_deaths.py`; compare with the previous version's window before judging.
- Any `exh` in the harness is a bug until shown otherwise.
- Tag each submission `hybrid-vN` and push the tag; keep the README's results table current.
- Patch notes are JS-rendered at /game/patch-notes; a patch that breaks compilation gives 24
  hours before ranked losses start, so check the ladder page after each patch.
