"""Deeper per team-game behaviour: spread, opening routes, casts per champion, tributes, kills, gold.

python deep.py <base> <raw> <ladder> <keep: teto|band|all> <out.json> [band team names file]
"""
import json, gzip, os, sys, statistics as st
from collections import defaultdict, Counter
from concurrent.futures import ProcessPoolExecutor

BASE, RAW, LAD, WHO, OUT = sys.argv[1:6]
BAND = set(json.load(open(sys.argv[6]))) if len(sys.argv) > 6 else set()
US = "Teto Territory"
NAMES = ["RYTH","VESPER_VALE","VENDETTA","CASSI","ISSAC","STELLAR","PRISM","MARCELLA","WILLOW","REN","VARG","TAVI","HEART_OF_RUIN"]


def cheb(a, b): return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def one(item):
    mid, v = item
    f = f"{BASE}/{LAD}/{mid}.jsonl.gz"
    if not os.path.exists(f): return []
    teams = {0 if t["side"] == "a" else 1: t for t in v["teams"]}
    if WHO == "teto": keep = [k for k, t in teams.items() if t["name"] == US]
    elif WHO == "band": keep = [k for k, t in teams.items() if t["name"] in BAND]
    else: keep = [0, 1]
    if not keep: return []
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        stc = json.loads(next(fh))
        camp_at = {(c["x"], c["y"]): c["group"] for c in stc["camps"]}
        sa = stc["spawnA"]
        spawn = {0: {(x, y) for x in range(sa["minX"], sa["maxX"] + 1) for y in range(sa["minY"], sa["maxY"] + 1)},
                 1: {tuple(t) for t in stc["spawnB"]}}
        sc = {t: (round(sum(x for x, _ in spawn[t]) / len(spawn[t])), round(sum(y for _, y in spawn[t]) / len(spawn[t]))) for t in (0, 1)}
        A = {t: dict(spread=defaultdict(list), clustered=defaultdict(int), alive_obs=defaultdict(int),
                     first_group={}, casts=Counter(), cast_on_champ=0, cast_total=0, turns_by_type=Counter(),
                     tributes=[], carried_at_death=[], kills=[], gold_at=defaultdict(list), prio=0, turns=0) for t in keep}
        last_pos = {}; prev = {}; seen_since = {}
        round_pos = defaultdict(dict)
        for l in fh:
            r = json.loads(l)
            if r.get("kind") != "turn": continue
            rd = r["round"]; t, c, s = r["team"], r["champ"], r["state"]["self"] or {}
            alive = s.get("alive", True) and s.get("x") is not None
            p = prev.get((t, c))
            if p is not None and p[0] and not alive:
                if t in A: A[t]["carried_at_death"].append(p[2])
                if 1 - t in A:
                    # the kill: how far from the victim's spawn, how long the victim had seen the killer's team
                    vp = p[1]
                    A[1 - t]["kills"].append(dict(round=rd, from_victim_spawn=cheb(vp, sc[t]), from_own_spawn=cheb(vp, sc[1 - t]),
                                                  victim_seen_for=rd - seen_since.get((t, c), rd)))
            if alive:
                me = (s["x"], s["y"])
                enemies_close = any(ch["team"] != t and cheb((ch["x"], ch["y"]), me) <= 8 for ch in r["state"]["champs"])
                if enemies_close: seen_since.setdefault((t, c), rd)
                else: seen_since.pop((t, c), None)
                round_pos[rd][(t, c)] = me
            if t in A:
                a = A[t]; a["turns"] += 1
                if any(x["k"] == 9 for x in r["actions"]): a["prio"] += 1
                if alive:
                    me = (s["x"], s["y"]); typ = s.get("type")
                    nm = NAMES[typ] if typ is not None and typ < 13 else "?"
                    a["turns_by_type"][nm] += 1
                    phase = "1-150" if rd <= 150 else "151-500" if rd <= 500 else "501+"
                    mates = [q for (tt, cc), q in round_pos[rd].items() if tt == t and cc != c] or \
                            [q for (tt, cc), q in round_pos[rd - 1].items() if tt == t and cc != c]
                    if mates:
                        d = min(cheb(me, q) for q in mates)
                        a["spread"][phase].append(d)
                        if d <= 4: a["clustered"][phase] += 1
                        a["alive_obs"][phase] += 1
                    for x in r["actions"]:
                        if x["k"] == 5 and "to" in x and tuple(x["to"]) in camp_at and c not in a["first_group"]:
                            a["first_group"][c] = (camp_at[tuple(x["to"])], rd, cheb(tuple(x["to"]), sc[t]))
                        if x["k"] == 6:
                            a["casts"][nm] += 1; a["cast_total"] += 1
                            if "to" in x and any(cheb(tuple(x["to"]), (ch["x"], ch["y"])) <= 1 for ch in r["state"]["champs"] if ch["team"] != t):
                                a["cast_on_champ"] += 1
                    g = s.get("gems") or 0
                    if p and p[0] and g < p[2] and me not in spawn[t]:
                        a["tributes"].append((rd, p[2] - g))
                    for cp in (150, 300, 600, 1000):
                        if rd == cp: a["gold_at"][cp].append(s.get("gold") or 0)
            prev[(t, c)] = (alive, (s.get("x"), s.get("y")) if alive else None, (s.get("gems") or 0) if alive else 0)
    out = []
    for t, a in A.items():
        fg = a["first_group"]
        shared_first = sum(1 for c, (g, _, _) in fg.items() if sum(1 for c2, (g2, _, _) in fg.items() if g2 == g) >= 2)
        out.append(dict(map=v["map"], team=teams[t]["name"], result=teams[t]["result"], size=stc["champsPerTeam"],
                        spread={k: (st.mean(x) if x else None) for k, x in a["spread"].items()},
                        clustered={k: a["clustered"][k] / max(1, a["alive_obs"][k]) for k in a["alive_obs"]},
                        first_trip=dict(bodies=len(fg), shared=shared_first, groups=len({g for g, _, _ in fg.values()}),
                                        dist=st.mean(d for _, _, d in fg.values()) if fg else None, round=st.mean(r_ for _, r_, _ in fg.values()) if fg else None),
                        casts={k: v_ / max(1, a["turns_by_type"][k]) * 100 for k, v_ in a["casts"].items()},
                        turns_by_type=dict(a["turns_by_type"]), cast_on_champ=a["cast_on_champ"], cast_total=a["cast_total"],
                        tributes=a["tributes"], carried_at_death=a["carried_at_death"], kills=a["kills"],
                        gold_at={k: st.median(x) for k, x in a["gold_at"].items()}, prio=a["prio"] / max(1, a["turns"]) * 100))
    return out


if __name__ == "__main__":
    idx = json.load(open(f"{BASE}/{RAW}/index.json", encoding="utf-8"))
    items = [(m, v) for m, v in idx.items() if v.get("status", "finished") == "finished"]
    rows = []
    with ProcessPoolExecutor(max_workers=int(os.environ.get("BC_JOBS", 8))) as ex:
        for r in ex.map(one, items, chunksize=2): rows.extend(r)
    json.dump(rows, open(OUT, "w"))
    print(len(rows), "team-games", file=sys.stderr)
