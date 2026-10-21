"""Where and how our ladder bodies die, from replay dumps of our own ranked games.

    python tools/ladder_deaths.py data/replays/ours-v7/ladder --index data/replays/ours-v7/raw/index.json

Per game: result, our deaths and theirs, gems, and each death's context (round, turns walked from
our spawn, health one turn earlier, enemies in sight, rounds since the last respawn). Then the
totals: deaths per game, the share that happen within a few turns of spawn (spawn camping), the
share at full health (walked into a stack), the standoff signature (long games with few gems).
Compare two folders (v6 window against v7 window) by running it twice.
"""
import argparse
import gzip
import json
import os
import sys
from collections import Counter, defaultdict

TEAM_NAME = "Teto Territory"
NEAR_SPAWN = 8          # "died near spawn": within this many walking turns of our spawn
FRESH = 10              # "fresh": within this many rounds of the respawn
FULL = 0.9              # "at full health" one turn before dying
VISION = 10


def load(path):
    rows = [json.loads(l) for l in gzip.open(path, "rt", encoding="utf-8")]
    static = rows[0]
    end = rows[-1] if rows[-1].get("kind") == "end" else {}
    turns = [r for r in rows if r.get("kind") == "turn"]
    return static, turns, end


def game_deaths(path, our_team):
    static, turns, end = load(path)
    width = static["width"]
    fields = [static["spawnFieldA"], static.get("spawnFieldB")]
    last = {}               # (team, champ) -> last alive record
    deaths = defaultdict(list)
    last_round = 0
    for t in turns:
        key = (t["team"], t["champ"])
        s = t["state"]["self"]
        last_round = max(last_round, t["round"])
        alive = bool(s) and s.get("x") is not None and s.get("alive", True) and (s.get("resp") or 0) == 0
        prev = last.get(key)
        if prev is not None and prev.get("_alive", True) and not alive:
            ps = prev["state"]["self"]
            f = fields[t["team"]]
            pos = ps["y"] * width + ps["x"]
            dist = f[pos] if f is not None and pos < len(f) else -1
            enemies = [c for c in prev["state"]["champs"] if c["team"] != t["team"] and c.get("x") is not None
                       and max(abs(c["x"] - ps["x"]), abs(c["y"] - ps["y"])) <= VISION]
            deaths[t["team"]].append({
                "round": prev["round"], "champ": t["champ"], "dist": dist,
                "hp": ps["hp"] / max(ps["max"], 1), "enemies": len(enemies),
                "since_spawn": prev["round"] - prev.get("_spawned", 1), "gems": ps["gems"],
            })
        if alive:
            if prev is None or not prev.get("_alive", True):
                t["_spawned"] = t["round"]
            else:
                t["_spawned"] = prev.get("_spawned", 1)
            t["_alive"] = True
            last[key] = t
        else:
            if prev is not None:
                prev_copy = dict(prev); prev_copy["_alive"] = False
                last[key] = prev_copy
    gems = turns[-1]["state"].get("gems") or [0, 0]
    return deaths, last_round, gems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--index", required=True, help="index.json from fetch (match -> teams/side/result)")
    ap.add_argument("--team", default=TEAM_NAME)
    ap.add_argument("--ladder", help="ladder json (list of teams) for opponent ratings")
    args = ap.parse_args()
    index = json.load(open(args.index, encoding="utf-8"))
    ratings = {}
    if args.ladder:
        for t in json.load(open(args.ladder, encoding="utf-8")):
            ratings[t["teamId"]] = (t["rank"], t["rating"])
    rows = []
    tot = Counter()
    for mid, m in sorted(index.items(), key=lambda kv: kv[1]["createdAt"]):
        path = os.path.join(args.folder, mid + ".jsonl.gz")
        if not os.path.exists(path):
            continue
        us = next(t for t in m["teams"] if t["name"] == args.team)
        them = next(t for t in m["teams"] if t["name"] != args.team)
        our_team = 0 if us["side"] == "a" else 1
        deaths, rounds, gems = game_deaths(path, our_team)
        ours, theirs = deaths[our_team], deaths[1 - our_team]
        gems = f"{gems[our_team]}-{gems[1 - our_team]}"
        near = sum(1 for d in ours if 0 <= d["dist"] <= NEAR_SPAWN)
        fresh = sum(1 for d in ours if d["since_spawn"] <= FRESH)
        full = sum(1 for d in ours if d["hp"] >= FULL)
        outnum = sum(1 for d in ours if d["enemies"] >= 2)
        rk = ratings.get(them["teamId"], ("?", 0))
        rows.append((m["createdAt"][5:16], m["map"], us["side"], us["result"], them["name"][:24], rk[0], len(ours), len(theirs), near, fresh, full, outnum, rounds, gems))
        tot["games"] += 1; tot["wins"] += us["result"] == "win"
        tot["ours"] += len(ours); tot["theirs"] += len(theirs); tot["near"] += near; tot["fresh"] += fresh; tot["full"] += full; tot["outnum"] += outnum
    print(f"{'when':12} {'map':22} s res   {'opponent':24} {'rank':>5} {'dead':>4} {'kill':>4} {'near':>4} {'fresh':>5} {'full':>4} {'2+en':>4} {'rnds':>5}  gems")
    for r in rows:
        when, mp, side, res, opp, rank, ours, theirs, near, fresh, full, outnum, rounds, gems = r
        print(f"{when:12} {mp:22} {side} {res:5} {opp:24} {str(rank):>5} {ours:4} {theirs:4} {near:4} {fresh:5} {full:4} {outnum:4} {rounds:5}  {gems}")
    n = max(tot["games"], 1)
    print(f"\n{tot['games']} games, {tot['wins']} wins. per game: our deaths {tot['ours']/n:.1f}, their deaths {tot['theirs']/n:.1f}")
    d = max(tot["ours"], 1)
    print(f"of our deaths: near spawn (<= {NEAR_SPAWN} turns) {tot['near']/d:.0%}, fresh (<= {FRESH} rounds after respawn) {tot['fresh']/d:.0%}, "
          f"at full health {tot['full']/d:.0%}, with 2+ enemies in sight {tot['outnum']/d:.0%}")


if __name__ == "__main__":
    main()
