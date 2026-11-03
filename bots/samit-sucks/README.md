# samit-sucks (v2): status and handover

**Read this first, then `DESIGN.md`.** Written 2026-10-26 at the end of the session that built v2.

`samit-sucks` is a from-scratch rewrite (v2), not the hybrid-derived redesign it used to be. The
old code is tagged `samit-sucks-v1` (phases 0-4 with the losing parts switched off) and
`samit-sucks-3a` (phase 3a, the one measured gain of the old redesign); their record is in
`docs/REDESIGN.md` and in git history. **`hybrid` is still the live submission.** Nothing of
v2 has been pushed or submitted; all commits are local on `samitIsUnemployed`.

## Where it stands (engine 2.0.0, cambc 4.0.0)

v2 plays full games with no errors and few exhaustions, but it is **not yet competitive**.

| Test (both sides of each map) | v2 | old bot (`samit-sucks-v1`) |
|---|---|---|
| Head to head, 6 maps (starfish, ambush, the-farm, lanes, big-world, collision) | 0-12, 26 gems / 100 rounds vs 85, deaths 65 vs 11 | |
| Economy vs `starter-python` (rounds to 1,000 gems, or gems at round 2,000) | lanes 1,464-1,622; big-world ~1,695; collision 1,759-1,802; starfish 1,983 / 961 gems; the-farm 1,687 / **165 gems**; ambush **422 / 733 gems** | every map 900-1,470 rounds |

So the uncontested economy runs at roughly 55-70% of the old bot's speed, with two stalls still
open (the-farm side A, ambush), and it loses fights badly to a bot that fights.

## How it works (one paragraph per module)

- `draft.py` / `plan.py`: pick from `config.DRAFT_PRIORITY` (pre-patch list), then on our first
  draft turn (100x fuel) build the map model: walking turns from our spawn and theirs to every
  camp group, the nearest altar per group, and group-to-group walking turns (full table, or the
  k nearest when 1 KB is short). ~6-13M fuel. Read in game as `w.plan`.
- `world.py`: static setup over the first turns (groups, camp lookup, spawn field, thin holes
  for dash bodies), then a lean per-turn view: me, enemies (kit power, `kits.py`), allies only
  when an enemy is in sight, camps (health read only for the job's group and awake camps),
  group states (job group every turn, others every 8 rounds).
- `board.py`: 8 bytes of shared memory: per body its claim or its 16x16 cell, an enemy sighting.
  Measured exact in phase 2 of the old redesign.
- `market.py`: jobs. FARM g (clear and capture), BANK altar, HOME (heal/shop, recall when far and
  safe), EVADE (a stronger enemy side within reach + 3: walk home through the threat layer until
  nothing is within 8). A FARM is priced as value / (walk + respawn wait + clear + capture +
  heal trips), with the fewest bodies that keep heal trips within `FARM_MAX_TRIPS` (parties,
  discounted per extra body), pruned by an optimistic bound and capped at `MAX_PRICED` groups a
  choice. Banking compares bag risk with trip cost. Jobs change only on events
  (`needs_review`).
- `tactics.py`: one evaluation a turn over stay / steps / dash landings: progress on the job's
  field, minus priced next-turn damage (enemies, awake camps that would pick us, the fading
  sighting), plus allies while threatened, plus attack opportunity. Then attack (kill > fight
  when the balance allows > the job's camp; never wake a camp we are not farming), casts by kit,
  attack reset, wards every 20 rounds. Holds for a party outside camp reach, steps off camp tiles
  after a capture, walks to living camps when the near ones are dead, gives way in corridors.
- `paths.py`: fields per target cached for the match, at most one new field a turn (a job whose
  field is not built waits a turn: deterministic, never fuel-gated).
- `kits.py`: champion data and builds for the new items (a first guess: Capture emblem first,
  then Jungle / Monster / Power, then Bruiser). **Untested.**

## How to work on it

```
python -m pyflakes bots/samit-sucks/src/*.py                      # before every run
python tools/run_matches.py samit-sucks starter-python --maps 01starfish,00ambush,01the-farm,00lanes,01big-world,00collision --out replays/batch/<new dir>
python tools/run_matches.py samit-sucks samit-sucks@samit-sucks-v1 --maps ... --out replays/batch/<new dir>
```

- **Results rows are the first bot's numbers on both sides** (`gems_a` is always ours). Never
  read `_b` for side-B games: that mistake corrupted the phase 3b-4 decisions.
- **Use a fresh `--out` directory**: the harness reuses an existing one and reports old games.
- **Edit code with an editor, not shell heredocs**: backslash line continuations got mangled
  several times.
- Every body prints a `stats r500/r1000 sN NAME: [...]` line: turns per job kind (`farm`, `bank`,
  `home`, `evade`), `hit` (turns it attacked), `hold` (waiting for a party), and stall counters:
  `pace` (back on the tile of two turns ago without attacking) and `still`; plus `gathered` and
  `banked` gems. A body with many farm turns and few hits is stuck: trace it.
- `config.TRACE_SLOT = n` prints one `t r...` line a turn for body n (position, job, group state,
  field value, attacks, fuel by stage: refresh / job choice / tactics) and, on a HOME job, the
  spawn field and the units around spawn. `config.LOG_JOBS` prints every job change.
- The economy test against `starter-python` isolates farming from fighting; the head-to-head
  against `samit-sucks-v1` measures both.

## Open problems, in the order I would take them

1. **the-farm side A: 165 gems in 2,000 rounds** with no deaths. Another stall; trace the body
   whose `stats` line shows many farm turns and few hits.
2. **ambush: 422-733 gems.** Most gem value is in elk/golem groups; check parties actually form
   (`hold` turns, claims) and that heal trips are priced right (`home` turns run 250-480 a body).
3. **Heal trips.** Bodies on big groups loop farm -> hurt -> home -> back every 10-15 rounds.
   Options: finish more (the `_can_finish` check exists), prefer soloable groups more strongly,
   use the Home emblem, or count Jungle/Durability in the damage model.
4. **Fighting.** Deaths are 6x the old bot's against a bot that fights; `EVADE` cut them from 136
   to ~65 over 12 games. Engagement is still crude (`FIGHT_BALANCE` on calibrated kit power).
   Measure on the band (CLAUDE.md), not the mirror, once the economy is level.
5. **Builds for the new items** (`kits.BUILDS`): test Capture-first against the old pre-patch
   orders; Prospector on cluster-heavy maps; Tribute for long banking walks.
6. **Fuel:** refresh ~150K, tactics ~150-400K a turn; the job choice is capped (~0.5M). Lanes and
   big-world are where exhaustions appear (~20 in 12 games now).
7. Then the full 70-map run against `samit-sucks-v1` and `hybrid`, and the band.

## Change log of this session (all measured against `samit-sucks-v1` or `starter-python`)

| Fix | Effect |
|---|---|
| groups no body can clear were taken at 0.25x score | Marcella soloed a 14-camp boss; now priced with heal trips and parties |
| dash over holes needed a 3-tile gain | Vesper stood 40 turns at a hole |
| bank target by straight line, never checked | bank jobs of 200+ turns; now draft table + unreachable altars dropped |
| capture tile read as a wall | altars marked unreachable after every capture: 0 banked |
| contested altar | Ryth died tributing 43 gems under two enemies; now holds off, then banks elsewhere |
| job choice priced 42 groups x 4 party sizes | 2M+ a choice, 483 exhaustions; now bound-pruned and capped: ~20 |
| party hold clock reset at every review | Vendetta held 882 turns |
| standing on a camp tile after a capture | bodies froze there (and blocked the respawn); now step off |
| ranged body in range of a camp hidden in a bush | paced forever; now goes in close |
| "done" fired on a deliberately awaited respawn | re-chose every turn at ~735K |
| near camps dead, living ones out of reach | Ryth paced 500 turns; now a small field to the living camps |
| spawn corridor deadlock | starfish economy 64-80 gems -> 961-1,004 |
| EVADE job | deaths 136 -> ~65 (12 games) |
