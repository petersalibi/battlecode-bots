# Next steps for the hybrid bot

Written 2026-10-21, after submission 6. Deadline early November 2026.

## Where we are

- **v6 on the ladder**: scripted bot, mode selector network trained on 62 ranked games of the
  top 15 teams, dash-aware routing with a dash-first draft on hole-walled maps. In the harness it
  beats v5 39-31 (+63 gems) and v4 54-16 (+241), with no fuel exhaustions.
- **Measurement**: `tools/run_matches.py` plays 70 games (35 maps, both sides) in about three
  minutes. A 70-game result is only good to about five games, so a change needs a clear win or
  two independent sweeps (against v5 and v4) before it ships.
- **Data path**: `fetch_replays.py` → `replay_dump.py` → `replay_features.py` →
  `train_mode_selector.py`, about a second per game end to end. 948 post-patch games of the top
  100 teams are in `data/replays/1.0.1/` (gitignored). More data from weaker teams made the
  selector worse; the top-15 set still gives the best model.

## Ranked by expected gain per day of work

### 1. Deaths (highest leverage, scripted) — first pass shipped as v7

Done 2026-10-21: spawn gate against spawn camping, group-aware flee range, dash away when
fleeing, capture standoff resolution. Still open: the lessons below, and the elk-arena pattern
of the whole enemy team sitting at our exit (a group exit, all bodies leaving together, or an
alternative exit through a dash over holes, would beat waiting).


A side still loses about 15 bodies a game, and against v4 the dash bot's win is mostly the death
gap (763 vs 1,363). Most deaths are champion kills of bodies already fleeing, plus the walk back
from spawn. Concrete pieces:

- **Dash to escape.** `step_away` ignores the dash; a fleeing dash champion should dash first,
  which doubles its lead over melee pursuers and crosses holes they cannot follow through.
- **Flee earlier against a stronger group**, using `power_balance` with the enemy's visible
  items and the team's recent damage, not just health.
- **Teleport crystals and wards** are never bought. A crystal at 3 tiles through terrain is a
  guaranteed escape; wards in the lanes to our farm would show enemies before they arrive.
- `tools/death_report.py` breaks a sweep down by killer, mode, health and map: start there.

### 2. Cooperative camps (contest, boss-battle, gold-rush, elk-arena)

These maps bank little because their gem sources are monoliths, golems and elk blocks that the
solo scorer refuses and the cooperative plan rarely triggers. A rotation plan (two or three
bodies assigned to one block via the claims board, everyone else farming small camps) would turn
four maps from ties on tiebreak into wins. The claims board already carries per-body status and
group; it needs a planner that assigns rather than one that only reports.

### 3. The next network heads

The mode selector is the only learned head. The guide's design has two more, in this order:

- **Camp scorer** (`USE_NN_CAMP`, factored candidate scorer): which group to farm. The script's
  `score_camp` is a hand formula; the ladder replays show which camps strong teams actually take,
  and `replay_features.py` already reconstructs the candidate list, so the labels are cheap.
- **Fight/flee scorer**: fight-or-flee from the local power balance and both sides' items, which
  is where the deaths are.

Keep the recipe that worked: inputs scaled to [0, 1], penalties on the legal options' logits
only, drop rows the mask forbids, winners of strong teams only, parity test before shipping.

### 4. Better training data, not more

- Fetch the top 15 to 25 teams over a longer post-patch window (the pool refreshes every ten
  minutes; a day gives a few hundred games of the right quality).
- Weight by rating more sharply (the current clip to [0.5, 1] barely distinguishes 1,800 from
  2,300).
- Hold out by map as well as by match, and report accuracy per map; the model's weak maps
  (fragment, starfish side B) are visible in the harness but not in the training metrics.

### 5. Fuel headroom

Islands and lanes (40+ camp groups) sit at the 2.5M limit on bad turns, and the network is
suppressed there to stay safe. Two cheap wins: cache the camp candidate scores across the
RETARGET_EVERY window instead of re-ranking every group (900K on islands), and skip
`vision.camps()` rebuilds when nothing in view changed. Each 300K saved on those maps is a turn
the network gets to run.

### 6. Known gaps that are cheap to close

- Hooks and corridors from side B fight at a disadvantage instead of farming elsewhere: an
  "enemy side" penalty in `score_camp` when the enemy spawn is nearer than ours.
- Ranged champions do not move and shoot; the draft avoids them, but the dash draft now picks Ren
  and Vesper Vale on hole maps, so a kite step after each attack would pay there.
- Camp rewards are assumed to split evenly; the lowest ID gets the remainder.

## Process

- Evaluate against both v5 and v4 (`python tools/run_matches.py hybrid hybrid@hybrid-v5`).
- Any `exh` in the harness is a bug until shown otherwise; look at the round with the match JSON's
  `fuelExhausted` events.
- Tag each submission `hybrid-vN` and push the tag; keep the README's results table current.
- Patch notes are JS-rendered at /game/patch-notes; a patch that breaks compilation gives 24
  hours before ranked losses start, so check the ladder page after each patch.
