"""Opening (first rounds) in detail, per team per game: turn activity, camp clears, first trips, early deaths.

python opening.py <base> <raw> <ladder> <who: teto|opp> <out.json>
"""
import json, gzip, os, sys
from collections import defaultdict, Counter
from concurrent.futures import ProcessPoolExecutor

BASE, RAW, LAD, WHO, OUT = sys.argv[1:6]
US = "Teto Territory"
ACT_END, CLEAR_END = 150, 300
KIND = {0: "wolf", 1: "elk", 2: "gem", 3: "gold", 4: "monolith", 5: "golem"}


def cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def one(item):
    mid, v = item
    f = f"{BASE}/{LAD}/{mid}.jsonl.gz"
    if not os.path.exists(f): return []
    teams = {0 if t["side"] == "a" else 1: t for t in v["teams"]}
    if not any(t["name"] == US for t in teams.values()): return []
    keep = [k for k, t in teams.items() if (t["name"] == US) == (WHO == "teto")]
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        stc = json.loads(next(fh))
        camp_group = {(c["x"], c["y"]): c["group"] for c in stc["camps"]}
        camp_kind = {(c["x"], c["y"]): c["type"] for c in stc["camps"]}
        group_tiles = defaultdict(list)
        for c in stc["camps"]: group_tiles[c["group"]].append((c["x"], c["y"]))
        gems_of = {g["index"]: g["gems"] for g in stc["groups"]}
        sa = stc["spawnA"]
        spawn = {0: {(x, y) for x in range(sa["minX"], sa["maxX"] + 1) for y in range(sa["minY"], sa["maxY"] + 1)},
                 1: {tuple(t) for t in stc["spawnB"]}}
        sc = {t: (sum(x for x, _ in spawn[t]) / len(spawn[t]), sum(y for _, y in spawn[t]) / len(spawn[t])) for t in (0, 1)}
        A = {t: dict(act=Counter(), clears=[], first_hit=[], first_gem=[], deaths=[], bodies=0) for t in keep}
        prev = {}; pos = {}
        eng = {t: {} for t in keep}       # group -> dict(start, hitters, last)
        first_hit = {}; first_gem = {}; alive_since = {}
        cleared = set()
        for l in fh:
            r = json.loads(l)
            if r.get("kind") != "turn": continue
            rd = r["round"]
            if rd > CLEAR_END: break
            t, c, s = r["team"], r["champ"], r["state"]["self"] or {}
            hp_now = r["campHp"]
            # clears: a group engaged by a kept team whose camps are all gone or at 0
            for tt in keep:
                for grp, e in list(eng[tt].items()):
                    if all(hp_now.get(f"{x},{y}", 0) <= 0 for x, y in group_tiles[grp]):
                        A[tt]["clears"].append(dict(kind="+".join(sorted({KIND.get(camp_kind[q], "?") for q in group_tiles[grp]})),
                                                    turns=rd - e["start"], hitters=len(e["hitters"]), start=e["start"], gems=gems_of.get(grp, 0),
                                                    dist=round(min(cheb(q, (round(sc[tt][0]), round(sc[tt][1]))) for q in group_tiles[grp]))))
                        del eng[tt][grp]
            alive = s.get("alive", True) and s.get("x") is not None
            p = prev.get((t, c))
            if t in A and p is not None and p[0] and not alive and rd <= ACT_END:
                me = p[1]
                enemies = sum(1 for (tt, cc), (r2, q) in pos.items() if tt != t and r2 >= rd - 2 and cheb(q, me) <= 6)
                camp_near = any(cheb(q, me) <= 5 and hp_now.get(f"{q[0]},{q[1]}", 0) > 0 for q in camp_group)
                A[t]["deaths"].append(dict(round=rd, enemies=enemies, camp_near=camp_near,
                                           half="own" if (me[0]-sc[t][0])**2+(me[1]-sc[t][1])**2 < (me[0]-sc[1-t][0])**2+(me[1]-sc[1-t][1])**2 else "theirs"))
            if not alive:
                prev[(t, c)] = (False, None, 0)
                if t in A and rd <= ACT_END: A[t]["act"]["dead"] += 1
                continue
            me = (s["x"], s["y"]); pos[(t, c)] = (rd, me)
            if t not in A:
                prev[(t, c)] = (True, me, s.get("gems") or 0); continue
            a = A[t]
            if (t, c) not in first_hit: a["bodies"] += 1 if rd <= 2 else 0
            ks = [x["k"] for x in r["actions"]]
            hit_camp = hit_champ = False
            for x in r["actions"]:
                if x["k"] == 5 and "to" in x:
                    tile = tuple(x["to"])
                    if tile in camp_group:
                        hit_camp = True
                        grp = camp_group[tile]
                        e = eng[t].setdefault(grp, dict(start=rd, hitters=set()))
                        e["hitters"].add(c)
                        if (t, c) not in first_hit: first_hit[(t, c)] = rd
                    else:
                        hit_champ = True
            g = s.get("gems") or 0
            if p and p[0] and g > p[2] and (t, c) not in first_gem: first_gem[(t, c)] = rd
            if rd <= ACT_END:
                moved = p is not None and p[0] and p[1] != me
                camp_close = any(cheb(q, me) <= 4 and hp_now.get(f"{q[0]},{q[1]}", 0) > 0 for q in camp_group)
                if me in spawn[t]: k = "on spawn"
                elif 11 in ks or 12 in ks or (p and p[0] and not moved and not hit_camp and not hit_champ and 6 not in ks and 9 in ks and False): k = "channel"
                elif hit_champ: k = "hit champion"
                elif hit_camp: k = "hit camp"
                elif moved: k = "walk"
                elif camp_close: k = "idle at camp"
                else: k = "idle elsewhere"
                a["act"][k] += 1
            prev[(t, c)] = (True, me, g)
    out = []
    for t, a in A.items():
        bodies = [k for k in first_hit if k[0] == t]
        a["first_hit"] = sorted(first_hit[k] for k in first_hit if k[0] == t)
        a["first_gem"] = sorted(first_gem[k] for k in first_gem if k[0] == t)
        a["act"] = dict(a["act"])
        out.append(dict(map=v["map"], team=teams[t]["name"], result=teams[t]["result"], size=stc["champsPerTeam"], **a))
    return out


if __name__ == "__main__":
    idx = json.load(open(f"{BASE}/{RAW}/index.json", encoding="utf-8"))
    items = [(m, v) for m, v in idx.items() if v.get("status") == "finished"]
    rows = []
    with ProcessPoolExecutor(max_workers=int(os.environ.get("BC_JOBS", 8))) as ex:
        for r in ex.map(one, items, chunksize=2): rows.extend(r)
    json.dump(rows, open(OUT, "w"))
    print(len(rows), "team-games", file=sys.stderr)
