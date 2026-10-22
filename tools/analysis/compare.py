import json, statistics as st, sys
from collections import Counter
S = sys.argv[1]
top = json.load(open(f"{S}/top25.json")); ours = json.load(open(f"{S}/ours.json"))
ratings = sorted((r["rating"] or 0) for r in top)
cut = ratings[len(ratings) // 2]
groups = {
    "top winners": [r for r in top if r["result"] == "win" and (r["rating"] or 0) >= cut],
    "top all": [r for r in top if (r["rating"] or 0) >= cut],
    "ours all": ours,
    "ours losses": [r for r in ours if r["result"] == "loss"],
}
def per(r, k): return r[k] / max(1, r["turns"]) * 100
def mean(xs): xs = list(xs); return st.mean(xs) if xs else float("nan")
rows = [
    ("rating (median)", lambda g: st.median([r["rating"] or 0 for r in g])),
    ("rounds played", lambda g: mean(r["rounds"] for r in g)),
    ("ended by gem target %", lambda g: 100 * mean(1 if r["end"] == "gems" else 0 for r in g)),
    ("banked by r300", lambda g: mean(r["banked"].get("300", 0) for r in g)),
    ("banked by r600", lambda g: mean(r["banked"].get("600", 0) for r in g)),
    ("banked by r1000", lambda g: mean(r["banked"].get("1000", r["banked"].get("600", 0)) for r in g)),
    ("gathered / game", lambda g: mean(r["gathered"] for r in g)),
    ("tributes / game", lambda g: mean(r["tributes"] for r in g)),
    ("gems per tribute", lambda g: sum(r["trib_gems"] for r in g) / max(1, sum(r["tributes"] for r in g))),
    ("gems carried / living turn", lambda g: sum(r["carry"] for r in g) / max(1, sum(r["turns"] - r["dead"] for r in g))),
    ("deaths / game", lambda g: mean(r["deaths"] for r in g)),
    ("gems lost / death", lambda g: sum(r["lost"] for r in g) / max(1, sum(r["deaths"] for r in g))),
    ("% turns dead", lambda g: mean(per(r, "dead") for r in g)),
    ("% turns on spawn", lambda g: mean(per(r, "spawn") for r in g)),
    ("% turns in enemy half", lambda g: mean(per(r, "enemy_half") for r in g)),
    ("% turns enemy within 5", lambda g: mean(per(r, "near_enemy") for r in g)),
    ("% turns ally within 4", lambda g: mean(per(r, "ally_near") for r in g)),
    ("mean dist to nearest ally", lambda g: sum(r["ally_dist_sum"] for r in g) / max(1, sum(r["ally_dist_n"] for r in g))),
    ("attacks on champions / game", lambda g: mean(r["att_champ"] for r in g)),
    ("attacks on camps / game", lambda g: mean(r["att_camp"] for r in g)),
]
for k in (4, 5, 6, 9, 11, 12, 13, 14, 16, 20, 21, 23):
    rows.append((f"action {k} / 100 turns", lambda g, k=k: mean(r["acts"].get(str(k), 0) / max(1, r["turns"]) * 100 for r in g)))
def r1000(g, i):
    vals = [x[i] for r in g for x in r["r1000"] if x[i] is not None]
    return st.median(vals) if vals else float("nan")
rows += [("str @r1000 (median)", lambda g: r1000(g, 1)), ("focus @r1000", lambda g: r1000(g, 2)),
         ("max hp @r1000", lambda g: r1000(g, 3)), ("unspent gold @r1000", lambda g: r1000(g, 4)), ("items held @r1000", lambda g: r1000(g, 5))]
names = list(groups)
print(f"{'':32}" + "".join(f"{n:>14}" for n in names))
print(f"{'team-games':32}" + "".join(f"{len(groups[n]):>14}" for n in names))
for label, fn in rows:
    print(f"{label:32}" + "".join(f"{fn(groups[n]):>14.1f}" for n in names))
NAMES = ["?"] * 13
for n in ("top winners", "ours all"):
    c = Counter(p for r in groups[n] for p in (r["picks"] or []))
    tot = sum(c.values())
    print(n, "picks %:", {k: round(100 * v / tot) for k, v in c.most_common()})
