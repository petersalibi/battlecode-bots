# Band testing and replay analysis

How v14 to v33 were judged: unranked challenges against teams rated about 200 above us, replayed
identically for every version, then compared with the scripts in this folder. Run everything
from the repo root. Every script reads the layout written by `tools/challenges.py collect`
(`<dir>/raw/*.zip` + `raw/index.json` and `<dir>/ladder/*.jsonl.gz` dumps), which is also the
layout of `tools/fetch_replays.py` and `tools/fetch_ours.py`, so ranked games work too.

## 1. Unranked challenges (`tools/challenges.py`)

- A match group is five unranked games against one team, on maps and sides we choose. The
  quota is **50 groups per rolling hour**, shared with anyone on the team using the website.
- **Challenges play whatever submission is live when the games run**, not a chosen version.
  Submit, wait a minute or two, queue, and don't submit again until the run's games are
  finished (about 5 minutes for 14 groups).
- **Games are deterministic.** The same two submissions, map and side replay identically, so
  re-queueing the same groups after a change gives exact before/after pairs.

```
# first run: 14 teams rated +170..+230 above us, all maps
python tools/challenges.py queue -o data/challenges/v14-band200 --band 170,230 --top 14 --maps all --label v14
python tools/challenges.py collect -o data/challenges/v14-band200     # repeat until "0 still queued"
python tools/challenges.py report  -o data/challenges/v14-band200

# every later version: replay the same 14 groups (70 games)
python tools/challenges.py queue -o data/challenges/v33-band200 --replay-from data/challenges/v14-band200 --limit 14 --label v33
```

`data/challenges/v14-band200` is the reference set: v14 to v33 all have a run of the same 70
games (`data/challenges/vN-band200`). The band has drifted since 2026-10-22, so pick a fresh
band with `--band` if our rating moves a lot. Other options: `--teams a,b`, `--top N`,
`--maps weak|all|names`, `--wait` (sleep through the quota instead of stopping).

## 2. Comparing runs

`python tools/analysis/compare_runs.py v14=data/challenges/v14-band200 v33=data/challenges/v33-band200 ...`
is the headline table. It uses only the games every run shares and shows wins, deaths, % turns
dead, gems gathered, banked by r300/600/1000, ally within 4, and first-150-round deaths. It
builds per-run profiles once (with `profile.py` and `opening.py`) into `data/analysis/` (gitignored).

**Fight execution** (contact = an enemy champion within 4):
- `fightstats.py <dir> raw ladder teto <out.json>`: per contact turn, the share attacking,
  casting or idle, and the damage dealt/taken ratio. Use `opp` for the opponents' side, or
  `band <names.json>` for band teams. Our ratio is 0.5 at v33 against the band's 2.3.
- `contact_modes.py <dir>`: our contact turns split by the mode the body was in, read from our
  stdout in the zips.

`tools/analysis/bandtest.sh <tag> <HH:MM UTC> <earlier tags...>` runs the whole loop
unattended. **It tags HEAD, submits and pushes to `testing`**, so only run it with Tyler's go-ahead.
It waits until the given UTC time (for the quota), queues the 14 replay groups, collects them,
and prints `compare_runs` against the earlier tags plus the fight table.

## 3. Behaviour studies (one-off; each writes a JSON for a later read)

| Script | Question it answers |
|---|---|
| `profile.py <base> raw ladder teto\|all <out>` | Per team-game profile: gathered, banked over time, deaths, wards, ally distance |
| `early.py`, `opening.py` (same arguments) | First 300 / 150 rounds: first camp, first gem, first bank, shared camp hits, early deaths |
| `early_deaths.py <dir> <out>` | Our deaths before r150: mode history, warning time, allies, enemies |
| `sightings.py <base> raw ladder <keep> <out>` | What each side does in the rounds after it first sees an enemy |
| `fights.py <dir> <out>` | Every death on both sides: attackers, helpers, casts, mode, half of the map |
| `retreats.py <out> <dirs...>` | Retreat episodes: damage per turn and death rate while retreating |
| `calib.py <out> <dirs...>` | Local power balance (kit power) vs who won the next 15 rounds. Source of `POWER_CALIBRATION` |
| `champs.py <out> <dirs...>` | Per champion: gems, deaths, damage. Showed the draft isn't the gap |
| `farmeff.py <out> <dirs...>` | What living turns go on, and gems per camp hit |
| `campfate.py <out> <dirs...>` | Each camp engagement: cleared, captured by whom, stolen |
| `deep.py`, `deep_report.py` | Spread, opening routes, casts per champion, tributes, kills, gold |
| `econ.py <base> <n> <out>` | Gathered, lost on death, banked; dead/at-spawn/still/moving shares |
| `buys.py <out>`, `order.py <ladder dir> <n>` | Top winners' shard purchase order (source of `BUILD_ORDER`); who acts first in a round |
| `compare.py <dir>` | Ad hoc comparison of `top25.json` and `ours.json` profiles |
| `fetch_band.py <n>` | The band teams' own ranked games, into `data/replays/band-1.0.2` |
| `rating.py` | Our current rating and rank |

These were written fast during the investigation. They work, but expect hardcoded team names
("Teto Territory") and minimal argument checking.
