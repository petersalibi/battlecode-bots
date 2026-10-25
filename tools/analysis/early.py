"""First 300 rounds per team per game: timeline, camps, grouping, distance, fights, shop.

python early.py <base> <raw> <ladder> <who: teto|opp|winners> <out.json>
  teto: our side; opp: the side that is not us; winners: winning sides with rating >= median
"""
import json, gzip, glob, os, sys, statistics as st
from collections import defaultdict, Counter
from concurrent.futures import ProcessPoolExecutor

BASE, RAW, LAD, WHO, OUT = sys.argv[1:6]
US = "Teto Territory"
END = 300
KIND = {0: "wolf", 1: "elk", 2: "gem cluster", 3: "gold cluster", 4: "monolith", 5: "golem"}


def cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def one(item):
    mid, v, cut = item
    f = f"{BASE}/{LAD}/{mid}.jsonl.gz"
    if not os.path.exists(f): return []
    teams = {0 if t["side"] == "a" else 1: t for t in v["teams"]}
    if WHO == "teto": keep = [k for k, t in teams.items() if t["name"] == US]
    elif WHO == "opp": keep = [k for k, t in teams.items() if t["name"] != US] if any(t["name"] == US for t in teams.values()) else []
    elif WHO == "all": keep = [0, 1]
    else: keep = [k for k, t in teams.items() if t["result"] == "win" and (t.get("rating") or 0) >= cut]
    if not keep: return []
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        stc = json.loads(next(fh))
        camp_at = {(c["x"], c["y"]): (c["group"], c["type"]) for c in stc["camps"]}
        sa = stc["spawnA"]; ca = ((sa["minX"] + sa["maxX"]) / 2, (sa["minY"] + sa["maxY"]) / 2)
        cb = (sum(x for x, _ in stc["spawnB"]) / len(stc["spawnB"]), sum(y for _, y in stc["spawnB"]) / len(stc["spawnB"]))
        centre = {0: ca, 1: cb}
        A = {t: dict(first_camp=None, first_gem=None, first_bank=None, first_shop=None, first_kill=None, first_death=None,
                     gathered={100: 0, 200: 0, 300: 0}, banked={150: 0, 300: 0}, groups=set(), kinds=Counter(), first_kinds=[],
                     camp_hits=0, champ_hits=0, shared_hits=0, deaths=0, kills_on_enemy=0, dist=[], enemy_half=0, turns=0,
                     spawn=0, still=0, recalls=0, wards=0, casts=0, gold300=None, items300=None, str300=None, hp300=None,
                     solo_groups=0) for t in keep}
        prev = {}; pos = {}; banked_seen = {0: 0, 1: 0}
        for l in fh:
            r = json.loads(l)
            if r.get("kind") != "turn": continue
            rd = r["round"]
            if rd > END: break
            t, c, s = r["team"], r["champ"], r["state"]["self"] or {}
            for tt in (0, 1):
                g = r["state"]["gems"][tt]
                if tt in A:
                    for cp in (150, 300):
                        if rd <= cp: A[tt]["banked"][cp] = g
                    if g > 0 and A[tt]["first_bank"] is None: A[tt]["first_bank"] = rd
            alive = s.get("alive", True) and s.get("x") is not None
            p = prev.get((t, c))
            if p is not None and p[0] and not alive:
                if t in A:
                    A[t]["deaths"] += 1
                    if A[t]["first_death"] is None: A[t]["first_death"] = rd
                if 1 - t in A:
                    A[1 - t]["kills_on_enemy"] += 1
                    if A[1 - t]["first_kill"] is None: A[1 - t]["first_kill"] = rd
            if t not in A:
                prev[(t, c)] = (alive, s.get("gems") or 0, (s.get("x"), s.get("y"))); continue
            a = A[t]; a["turns"] += 1
            if not alive:
                prev[(t, c)] = (False, 0, None); continue
            me = (s["x"], s["y"]); pos[(t, c)] = (rd, me)
            dm = cheb(me, (round(centre[t][0]), round(centre[t][1])))
            a["dist"].append(dm)
            de = (me[0] - centre[1 - t][0]) ** 2 + (me[1] - centre[1 - t][1]) ** 2
            if de < (me[0] - centre[t][0]) ** 2 + (me[1] - centre[t][1]) ** 2: a["enemy_half"] += 1
            if p and p[0] and p[2] == me: a["still"] += 1
            g = s.get("gems") or 0
            if p and p[0] and g > p[1]:
                for cp in (100, 200, 300):
                    if rd <= cp: a["gathered"][cp] += g - p[1]
                if a["first_gem"] is None: a["first_gem"] = rd
            allies_near = [q for (tt, cc), (r2, q) in pos.items() if tt == t and cc != c and r2 >= rd - 1 and cheb(q, me) <= 3]
            for act in r["actions"]:
                k = act["k"]
                if k == 5 and "to" in act:
                    tile = tuple(act["to"])
                    if tile in camp_at:
                        grp, kind = camp_at[tile]
                        a["camp_hits"] += 1
                        if allies_near: a["shared_hits"] += 1
                        if a["first_camp"] is None: a["first_camp"] = rd
                        if grp not in a["groups"]:
                            a["groups"].add(grp)
                            if len(a["first_kinds"]) < 4: a["first_kinds"].append(KIND.get(kind, str(kind)))
                        a["kinds"][KIND.get(kind, str(kind))] += 1
                    else:
                        a["champ_hits"] += 1
                elif k == 13:
                    if a["first_shop"] is None: a["first_shop"] = rd
                elif k == 11: a["recalls"] += 1
                elif k == 14: a["wards"] += 1
                elif k == 6: a["casts"] += 1
            if rd >= END - 2 and s.get("str") is not None:
                a["gold300"] = s.get("gold"); a["items300"] = sum(s.get("items") or []); a["str300"] = s["str"]; a["hp300"] = s["max"]
            prev[(t, c)] = (True, g, me)
    out = []
    for t, a in A.items():
        tm = teams[t]
        a["groups"] = len(a["groups"]); a["kinds"] = dict(a["kinds"])
        a["dist_med"] = st.median(a["dist"]) if a["dist"] else None; a["dist_max"] = max(a["dist"]) if a["dist"] else None; del a["dist"]
        out.append(dict(map=v["map"], team=tm["name"], result=tm["result"], size=stc["champsPerTeam"], **a))
    return out


if __name__ == "__main__":
    idx = json.load(open(f"{BASE}/{RAW}/index.json", encoding="utf-8"))
    rs = sorted(t.get("rating") or 0 for v in idx.values() for t in v["teams"])
    cut = rs[len(rs) // 2]
    items = [(m, v, cut) for m, v in idx.items() if v.get("status", "finished") == "finished"]
    rows = []
    with ProcessPoolExecutor(max_workers=int(os.environ.get("BC_JOBS", 8))) as ex:
        for r in ex.map(one, items, chunksize=2): rows.extend(r)
    json.dump(rows, open(OUT, "w"))
    print(len(rows), "team-games", file=sys.stderr)
