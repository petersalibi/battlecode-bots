"""Vision denial per side: of the enemy bodies within 4 tiles of a body at its turn start (true
positions), how many could it not see, and how many of those stood in a bush. Also who lands the
first hit when a contact starts.   python unseen.py <base dir>"""
import json, gzip, glob, os, sys
from collections import defaultdict, Counter
D = sys.argv[1]; US = "Teto Territory"
def cheb(a, b): return max(abs(a[0] - b[0]), abs(a[1] - b[1]))
idx = json.load(open(f"{D}/raw/index.json", encoding="utf-8"))
S = {k: Counter() for k in ("us", "band")}
for f in sorted(glob.glob(f"{D}/ladder/*.jsonl.gz")):
    mid = os.path.basename(f).split(".")[0]; v = idx.get(mid)
    if not v: continue
    ours = next((0 if t["side"] == "a" else 1 for t in v["teams"] if t["name"] == US), None)
    if ours is None: continue
    side_of = {ours: "us", 1 - ours: "band"}
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        st = json.loads(next(fh)); bushes = set(map(tuple, st["bushes"]))
        recs = []; pos = defaultdict(dict); team_of = {}
        for l in fh:
            r = json.loads(l)
            if r.get("kind") != "turn": continue
            s = r["state"]["self"] or {}
            ok = s.get("alive", True) and s.get("x") is not None
            team_of[r["champ"]] = r["team"]
            pos[r["champ"]][r["round"]] = (s["x"], s["y"]) if ok else None
            recs.append(r)
    # latest known true position of every champ at a given (round, ordinal): use the same round's
    # record when it exists (enemies that moved earlier this round) else the previous round's
    for r in recs:
        s = r["state"]["self"] or {}
        if not (s.get("alive", True) and s.get("x") is not None): continue
        me = (s["x"], s["y"]); rd = r["round"]; side = side_of[r["team"]]
        seen = {(ch["x"], ch["y"]) for ch in r["state"]["champs"] if ch["team"] != r["team"]}
        for oc, ot in team_of.items():
            if ot == r["team"]: continue
            p = pos[oc].get(rd) if rd in pos[oc] else pos[oc].get(rd - 1)
            if p is None: continue
            if cheb(p, me) <= 4:
                S[side]["near"] += 1
                if p not in seen:
                    S[side]["unseen"] += 1
                    if p in bushes: S[side]["unseen_bush"] += 1
                    if cheb(p, me) <= 2: S[side]["unseen_close"] += 1
        # first hit of a contact: we attacked an enemy champion this turn without having been in contact last turn
        hit = any(x["k"] == 5 and "to" in x and tuple(x["to"]) in seen for x in r["actions"])
        if hit: S[side]["hits"] += 1
        if hit and s.get("x") is not None and me in bushes: S[side]["hits_from_bush"] += 1
for side in ("us", "band"):
    c = S[side]; n = max(1, c["near"])
    print(f"{side:5s} enemy bodies within 4 at our turn start: {c['near']}; unseen {100*c['unseen']/n:.1f}% "
          f"(of which in a bush {100*c['unseen_bush']/max(1,c['unseen']):.0f}%, within 2 tiles {100*c['unseen_close']/max(1,c['unseen']):.0f}%); "
          f"attacks on champions {c['hits']}, from inside a bush {100*c['hits_from_bush']/max(1,c['hits']):.0f}%")
