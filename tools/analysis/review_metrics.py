"""The review's scorecard: how often a side dies, how often it is in contact, how healthy it is
when it steps away, and how alone it is when it dies. Run on any set of dumps, so the same numbers
come out for our ladder games, the top ten's games and local harness batches.

    python tools/analysis/review_metrics.py ours=data/replays/ours-v42 top10=data/replays/top10@won
    python tools/analysis/review_metrics.py v42=replays/dumps/p0-baseline@file --json out.json

Each argument is label=path[@selector]. The path is a folder of *.jsonl.gz dumps, or a folder with
a ladder/ subfolder of them and raw/index.json beside it (the layout of fetch_replays.py,
fetch_ours.py and challenges.py). The selector picks which side of each game counts:
    us (default with an index)  our team (--us)
    opp                         whoever played us
    won                         the side with more tributed gems
    all (default without one)   both sides
    file                        side A for dumps named *-A.jsonl.gz, side B for *-B (run_matches.py
                                names each match after the first bot's side)
    otherfile                   the other side of the same (the opponent in a batch)
    Name One,Name Two           those teams, by name in the index

Positions are true, not fogged: every champion's own turn record gives its position, and the
others are read from their latest record, so they are at most one turn stale.

Columns:
    deaths/g      deaths per game per side (a body going from alive to dead)
    contact       share of living turns with an enemy champion within CONTACT (4)
    hp@away       health share at the start of a turn that steps away from the nearest enemy while in
                  contact, first turn of each retreat only
    ally5@death   living allies within 5 at the last living turn before a death
    enemy5@death  enemies within 5 at the same moment
    ally4         share of living turns with an ally within 4
    wards/g       wards placed per game per side
"""
import argparse
import glob
import gzip
import json
import os
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

CONTACT = 4
NEAR_DEATH = 5
WARD_PLACE = 14     # action kind for placing a ward (matches ward charge drops one for one)


def cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def sides_for(path, selector, index, us):
    """Which sides (0 = a, 1 = b) of this game count, or None to skip it."""
    if selector == "all":
        return (0, 1)
    if selector == "file":
        stem = os.path.basename(path).split(".")[0]
        return (0,) if stem.endswith("-A") else (1,) if stem.endswith("-B") else None
    if selector == "otherfile":                 # the opponent in a run_matches.py batch
        stem = os.path.basename(path).split(".")[0]
        return (1,) if stem.endswith("-A") else (0,) if stem.endswith("-B") else None
    if selector == "won":
        return "won"
    entry = index.get(os.path.basename(path).split(".")[0]) if index else None
    if not entry:
        return None
    side_of = {t["name"]: (0 if t["side"] == "a" else 1) for t in entry["teams"]}
    if selector == "us":
        return (side_of[us],) if us in side_of else None
    if selector == "opp":
        return (1 - side_of[us],) if us in side_of else None
    names = set(selector.split(","))
    picked = tuple(s for n, s in side_of.items() if n in names)
    return picked or None


def one(job):
    """Per-side totals for one dumped game."""
    path, selector, index, us = job
    sides = sides_for(path, selector, index, us)
    if sides is None:
        return None
    t = {s: defaultdict(float) for s in (0, 1)}
    where = {}                   # (team, champ) -> (x, y) of the living, latest record
    last = {}                    # (team, champ) -> (alive, pos, hp share) at its previous turn
    away_prev = set()            # bodies whose previous turn was a step away
    pending = {}                 # (team, champ) -> (pos, hp share, enemy tiles) of a contact turn
    gems = [0, 0]
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        next(fh)
        for line in fh:
            r = json.loads(line)
            if r.get("kind") != "turn":
                continue
            team, key = r["team"], (r["team"], r["champ"])
            s = r["state"]["self"] or {}
            gems = r["state"].get("gems", gems)
            alive = s.get("alive", True) and s.get("x") is not None
            prev = last.get(key)
            if not alive:
                where.pop(key, None)
                pending.pop(key, None)
                away_prev.discard(key)
                if prev and prev[0]:
                    tt = t[team]
                    tt["deaths"] += 1
                    me = prev[1]
                    tt["ally_at_death"] += sum(1 for k, p in where.items() if k[0] == team and cheb(p, me) <= NEAR_DEATH)
                    tt["enemy_at_death"] += sum(1 for k, p in where.items() if k[0] != team and cheb(p, me) <= NEAR_DEATH)
                last[key] = (False, None, 0.0)
                continue
            me = (s["x"], s["y"])
            hp = s["hp"] / max(1, s["max"])
            where[key] = me
            tt = t[team]
            # Did last turn's contact turn step away? Judged from where we stand now.
            p = pending.pop(key, None)
            stepped = False
            if p is not None:
                before = min(cheb(p[0], e) for e in p[2])
                after = min(cheb(me, e) for e in p[2])
                if after > before:
                    stepped = True
                    if key not in away_prev:
                        tt["away_n"] += 1
                        tt["away_hp"] += p[1]
            if stepped:
                away_prev.add(key)
            else:
                away_prev.discard(key)
            enemies = [q for k, q in where.items() if k[0] != team]
            allies = [q for k, q in where.items() if k[0] == team and k != key]
            tt["living"] += 1
            if enemies and min(cheb(me, e) for e in enemies) <= CONTACT:
                tt["contact"] += 1
                pending[key] = (me, hp, enemies)
            if allies and min(cheb(me, a) for a in allies) <= 4:
                tt["ally4"] += 1
            tt["wards"] += sum(1 for a in r["actions"] if a["k"] == WARD_PLACE)
            last[key] = (True, me, hp)
    if sides == "won":
        if gems[0] == gems[1]:
            return None
        sides = (0 if gems[0] > gems[1] else 1,)
    out = []
    for side in sides:
        tt = dict(t[side])
        tt["games"] = 1
        tt["margin"] = gems[side] - gems[1 - side]
        tt["wins"] = 1 if gems[side] > gems[1 - side] else 0
        tt["ties"] = 1 if gems[side] == gems[1 - side] else 0
        out.append(tt)
    return out


def collect(spec, us, jobs):
    label, _, rest = spec.partition("=")
    path, _, selector = rest.partition("@")
    files = glob.glob(os.path.join(path, "ladder", "*.jsonl.gz")) or glob.glob(os.path.join(path, "*.jsonl.gz"))
    index_path = os.path.join(path, "raw", "index.json")
    index = json.load(open(index_path, encoding="utf-8")) if os.path.exists(index_path) else None
    selector = selector or ("us" if index else "all")
    if selector not in ("all", "file", "otherfile", "won") and index is None:
        sys.exit(f"{label}: selector '{selector}' needs {index_path}")
    total = defaultdict(float)
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        for rows in ex.map(one, [(f, selector, index, us) for f in files], chunksize=2):
            for row in rows or ():
                for k, v in row.items():
                    total[k] += v
    return label, selector, len(files), total


def summarise(total):
    g, living = max(1, total["games"]), max(1, total["living"])
    deaths = max(1, total["deaths"])
    return {
        "games": int(total["games"]),
        "wins": int(total["wins"]),
        "ties": int(total["ties"]),
        "margin": total["margin"] / g,
        "deaths/g": total["deaths"] / g,
        "contact": total["contact"] / living,
        "hp@away": total["away_hp"] / max(1, total["away_n"]),
        "ally5@death": total["ally_at_death"] / deaths,
        "enemy5@death": total["enemy_at_death"] / deaths,
        "ally4": total["ally4"] / living,
        "wards/g": total["wards"] / g,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("runs", nargs="+", help="label=path[@selector]")
    ap.add_argument("--us", default="Teto Territory", help="our team name in index.json")
    ap.add_argument("--jobs", type=int, default=int(os.environ.get("BC_JOBS", 8)))
    ap.add_argument("--json", help="also write the table here")
    args = ap.parse_args()
    rows = []
    for spec in args.runs:
        label, selector, n, total = collect(spec, args.us, args.jobs)
        if not total["games"]:
            print(f"{label}: no games matched ({n} dumps, selector {selector})", file=sys.stderr)
            continue
        rows.append((label, selector, summarise(total)))
    cols = ["games", "W-L", "margin", "deaths/g", "contact", "hp@away", "ally5@death", "enemy5@death", "ally4", "wards/g"]
    print(f"{'run':<14}{'side':<8}" + "".join(f"{c:>13}" for c in cols))
    for label, selector, m in rows:
        cells = [str(m["games"]), f"{m['wins']}-{m['games'] - m['wins'] - m['ties']}", f"{m['margin']:+.0f}",
                 f"{m['deaths/g']:.1f}", f"{m['contact']:.0%}", f"{m['hp@away']:.0%}", f"{m['ally5@death']:.2f}",
                 f"{m['enemy5@death']:.2f}", f"{m['ally4']:.0%}", f"{m['wards/g']:.1f}"]
        print(f"{label:<14}{selector[:7]:<8}" + "".join(f"{c:>13}" for c in cells))
    if args.json:
        json.dump({label: dict(m, selector=selector) for label, selector, m in rows}, open(args.json, "w"), indent=1)


if __name__ == "__main__":
    main()
