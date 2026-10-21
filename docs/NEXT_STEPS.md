# Next steps for the hybrid bot

Written 2026-10-21, after submission 7. Deadline early November 2026. Team work (rotation plans,
shared blocks, escorts) is deliberately last: everything here improves one body's own decisions,
in the order they sit behind the main decision, the mode selector.

## Where we are

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
- Ranged champions (Ren, Vesper Vale, Heart of Ruin) do not kite: a step after each attack.
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

### Last: cooperation

Rotation plans for contest, boss_battle, gold_rush, elk_arena, stampede and rushbait, escorts
for tributes, and shared fights. The claims board already carries per-body status and group; it
needs a planner that assigns rather than one that only reports.

## Process

- Evaluate against both v5 and v6 (`python tools/run_matches.py hybrid hybrid@hybrid-v6`).
- After each submission, fetch our own ranked games into `data/replays/ours-vN/` and run
  `tools/ladder_deaths.py`; compare with the previous version's window before judging.
- Any `exh` in the harness is a bug until shown otherwise.
- Tag each submission `hybrid-vN` and push the tag; keep the README's results table current.
- Patch notes are JS-rendered at /game/patch-notes; a patch that breaks compilation gives 24
  hours before ranked losses start, so check the ladder page after each patch.
