# samit-sucks v2: design

Status, test commands and the open problems are in `README.md`.

A new bot, written from scratch on 2026-10-26 for engine 2.0.0. The old samit-sucks (hybrid plus
the redesign phases) is tagged `samit-sucks-v1`; its 3a state (the one measured gain) is
`samit-sucks-3a`. Nothing of hybrid's decision code is carried over.

## What we are answering

The external review (2026-10-25) and everything since:

| Finding | Where it came from | What v2 does about it |
|---|---|---|
| Behaviour depended on fuel left | review; phase 1 | No branch reads fuel. Every turn has a fixed shape: perceive, (maybe) re-plan on an event, one tactical evaluation, act |
| Rules piled up ahead of the decision (285 constants) | review; phase 3b lost when it tried to replace them inside hybrid | No modes, no rule stack. Two decisions only: which job (economics) and which action this turn (one evaluation). A short constant table, each with its reason |
| No picture beyond own sight, no plan beyond the turn | review | Draft-time map model (100x fuel now) + shared positions and sightings (phase 2 board, which worked) |
| Per-turn re-ranking of every camp (~450K-1.1M a turn, the exhaustions) | phase 1/3d profiling | Jobs are chosen only on events, from a group-to-group distance table made in the draft: no field per candidate |
| Deaths 2-3x the top ten, dying alone | review; ladder 14.9/g vs 6.4 | Threat priced into every step (3a's layer, the one gain), retreats that keep fighting back, pairs only where a camp needs two |
| Forced grouping costs gems (squads, macro routes, engage rule all lost) | NEXT_STEPS; today's corrected runs | Grouping is an economic choice per job (a camp that needs two bodies gets two), never a constraint |
| Gathering is the gap (band ~2x our gems) at equal per-turn farm rate | three studies | Less time dead, less time walking (table distances, respawn-timed circuits), faster captures (Capture emblem), bank on a risk model not a bag size |
| The mirror misleads; band decides; harness rows are first-bot POV | NEXT_STEPS; today's error | Judge every step against `samit-sucks-3a` and `hybrid` on the new engine with `_a` fields only, then the band |

## Shape

```
draft (first turn, 100x fuel)
  picks ............ priority list (data), then
  map model ........ fields from both spawns, every altar and every camp group:
                     group table (value, health, damage, side), group-to-group walking
                     distances, nearest safe altar per group  -> draft memory (1 KB)
every turn
  perceive ......... me, visible enemies/allies/camps, board (claims, positions, sighting)
  job .............. kept unless an event (done, failed, group state change, hurt, bag
                     full, enemy near the target, ally took it); then the market picks
                     the best job for this body given the others' claims
  tactics .......... one evaluation over stay / 8 steps / dash landings:
                       job progress  - priced threat  + support  + attack value from there
                     then the best attack and cast from where we end up
  publish .......... claim (job) or position, sighting
```

### Jobs (the economy)

A job is one of: `FARM g` (clear and capture group g), `BANK a` (tribute at altar a), `HOME`
(heal and shop: walk or recall), `GUARD g` (stand on a capture an ally needs, or help an ally
clear a camp that needs two). The market scores each candidate as reward per turn:

    value(g) / (travel + clear_turns(bodies) + capture_turns)  x  survival(g)

with travel from the draft's distance table, clear turns from our team damage against the
group's health, and survival from the threat seen near it (sightings, deaths there). A group
another body claims is skipped unless it needs a second body. Banking is chosen when the bag's
expected loss (bag x death risk until the next natural bank) exceeds the trip's cost, or at the
endgame; HOME when health cannot take the next job's expected damage.

### Tactics (no modes)

Every turn, the reachable tiles are scored once (3a's pruned influence: only threats whose edge
crosses our neighbourhood are scored, ~13K a tile):

- progress towards the job's tiles (one cached field per job),
- minus damage that could land there next turn (enemy reach, awake camps that would pick us,
  fading sightings), as a share of our effective health,
- plus allies within reach while threatened,
- plus the value of what we could attack from there this turn (a kill, the job's camp, an enemy
  in reach when the local balance favours us).

Then the attack and the cast are chosen from where we stand: kill first, then the job's camp,
then the enemy that hurts us most; spells by kit (stun the chaser, shield when hit, nuke the
weakest). Retreat is not a mode: when a job is abandoned for HOME the same evaluation walks
home, and a melee body still hits what is adjacent on the way.

### Builds

Builds are data, per champion, revisited once post-patch ladder games exist. The opening
candidates under the new items: Capture emblem first for every farmer (5 -> 3 round captures on
every capture it stands on), Jungle for melee front liners (30% less camp damage), then health or
the champion's damage stat; Prospector on maps rich in gem clusters.

### Fuel

Per turn: perception ~200K, tactics <= 300K, the market only on events (<= 600K), at most one new
field a turn. The first play turn (2x) builds the job field for the opening job only.

## Order of work

1. Skeleton that farms, captures and banks with no enemy logic: model, market, paths, shop.
   Beat nothing yet; no exhaustions; check the job log on a few maps.
2. Tactics: threat pricing, attacks and spells, retreat to HOME.
3. Coordination: claims, two-body jobs, capture-state API (ally capturing / enemy capturing).
4. Builds for the new items.
5. Judge: vs `samit-sucks-3a` and `hybrid` on the 70-map pool (first-bot fields), then the band.
