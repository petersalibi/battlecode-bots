"""Behaviour profile per team per game from replay dumps (tools/replay_dump.py output).

python profile.py <base dir with raw/ and ladder/> <raw subdir> <ladder subdir> <who: teto|all> <out.json>
"""
import json, gzip, os, sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

BASE, RAW, LAD, WHO, OUT = sys.argv[1:6]
CHECK = (300, 600, 1000, 1500)


def cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def one(item):
    mid, v = item
    f = f"{BASE}/{LAD}/{mid}.jsonl.gz"
    if not os.path.exists(f):
        return None
    teams = {0 if t["side"] == "a" else 1: t for t in v["teams"]}
    if WHO == "teto":
        keep = [k for k, t in teams.items() if "Teto" in t.get("name", "")]
    else:
        keep = [0, 1]
    if not keep:
        return None
    P = {t: dict(turns=0, dead=0, spawn=0, enemy_half=0, near_enemy=0, ally_near=0, ally_dist_sum=0, ally_dist_n=0,
                 carry=0, tributes=0, trib_gems=0, deaths=0, lost=0, gathered=0, acts=Counter(), att_champ=0, att_camp=0,
                 r1000=[], banked={}) for t in (0, 1)}
    prev = {}
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        static = json.loads(next(fh))
        W, H = static["width"], static["height"]
        sa = static["spawnA"]
        spawnA = {(x, y) for x in range(sa["minX"], sa["maxX"] + 1) for y in range(sa["minY"], sa["maxY"] + 1)}
        spawnB = {tuple(t) for t in static["spawnB"]}
        spawn = {0: spawnA, 1: spawnB}
        ca = (sum(x for x, _ in spawnA) / len(spawnA), sum(y for _, y in spawnA) / len(spawnA))
        cb = (sum(x for x, _ in spawnB) / len(spawnB), sum(y for _, y in spawnB) / len(spawnB))
        centre = {0: ca, 1: cb}
        camp_tiles = {(c["x"], c["y"]) for c in static["camps"]}
        last_round = 0
        for l in fh:
            r = json.loads(l)
            if r.get("kind") != "turn":
                continue
            t, c, st = r["team"], r["champ"], r["state"]
            s = st["self"] or {}
            rd = r["round"]; last_round = rd
            a = P[t]
            for cp in CHECK:
                if rd >= cp and cp not in a["banked"]:
                    a["banked"][cp] = st["gems"][t]
            a["turns"] += 1
            for act in r["actions"]:
                a["acts"][act["k"]] += 1
                if act["k"] == 5 and "to" in act:
                    if tuple(act["to"]) in camp_tiles: a["att_camp"] += 1
                    else: a["att_champ"] += 1
            alive = s.get("alive", True) and s.get("x") is not None
            p = prev.get((t, c))
            g = s.get("gems") or 0
            if not alive:
                a["dead"] += 1
                if p and p[0]:
                    a["deaths"] += 1; a["lost"] += p[2]
                prev[(t, c)] = (False, None, 0)
                continue
            pos = (s["x"], s["y"])
            if pos in spawn[t]: a["spawn"] += 1
            # enemy half: nearer the enemy spawn centre than ours
            dm = (pos[0] - centre[t][0]) ** 2 + (pos[1] - centre[t][1]) ** 2
            de = (pos[0] - centre[1 - t][0]) ** 2 + (pos[1] - centre[1 - t][1]) ** 2
            if de < dm: a["enemy_half"] += 1
            allies = [(ch["x"], ch["y"]) for ch in st["champs"] if ch["team"] == t and ch["i"] != c]
            enemies = [(ch["x"], ch["y"]) for ch in st["champs"] if ch["team"] != t]
            if enemies and min(cheb(pos, e) for e in enemies) <= 5: a["near_enemy"] += 1
            if allies:
                dmin = min(cheb(pos, q) for q in allies)
                a["ally_dist_sum"] += dmin; a["ally_dist_n"] += 1
                if dmin <= 4: a["ally_near"] += 1
            a["carry"] += g
            if p and p[0]:
                d = g - p[2]
                if d > 0: a["gathered"] += d
                elif d < 0 and any(x["k"] == 12 for x in r["actions"]) or (d < 0 and p[1] is not None):
                    if d < 0:
                        a["tributes"] += 1; a["trib_gems"] += -d
            if 990 <= rd <= 1010 and s.get("str") is not None:
                a["r1000"].append((s["type"], s["str"], s.get("focus"), s["max"], s.get("gold") or 0, sum(s.get("items") or [])))
            prev[(t, c)] = (True, pos, g)
    out = []
    for t in keep:
        tm = teams[t]
        a = P[t]
        a["acts"] = dict(a["acts"])
        out.append(dict(map=v["map"], side=tm["side"], opp=next((x["name"] for x in v["teams"] if x is not tm), None), team=tm["name"], result=tm["result"], rating=tm.get("rating"), picks=tm.get("draftPicks"),
                        rounds=last_round, size=static["champsPerTeam"], end=v.get("endReason"), **a))
    return out


if __name__ == "__main__":
    idx = json.load(open(f"{BASE}/{RAW}/index.json", encoding="utf-8"))
    items = list(idx.items())
    rows = []
    with ProcessPoolExecutor(max_workers=int(os.environ.get("BC_JOBS", 8))) as ex:
        for i, r in enumerate(ex.map(one, items, chunksize=2)):
            if r: rows.extend(r)
            if i % 20 == 0:
                print(f"\r{i}/{len(items)}", end="", file=sys.stderr, flush=True)
    json.dump(rows, open(OUT, "w"))
    print(f"\n{len(rows)} team-games", file=sys.stderr)
