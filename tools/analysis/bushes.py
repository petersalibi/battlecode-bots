"""Bush use per side in a challenge/ladder run: share of living turns in a bush, hides (stepping into a
bush with an enemy within 5) and their outcomes, and what the nearest enemy does after a hide
(closes on the bush, holds, or leaves): does the other side chase through bushes?
python bushes.py <base dir with raw/ and ladder/>
"""
import json, gzip, glob, os, sys
from collections import defaultdict, Counter
D = sys.argv[1]; US = "Teto Territory"
def cheb(a, b): return max(abs(a[0] - b[0]), abs(a[1] - b[1]))
idx = json.load(open(f"{D}/raw/index.json", encoding="utf-8"))
S = {k: Counter() for k in ("us", "band")}
after = {k: Counter() for k in ("us", "band")}       # chaser response to the other side's hide
for f in sorted(glob.glob(f"{D}/ladder/*.jsonl.gz")):
    mid = os.path.basename(f).split(".")[0]; v = idx.get(mid)
    if not v: continue
    ours = next((0 if t["side"] == "a" else 1 for t in v["teams"] if t["name"] == US), None)
    if ours is None: continue
    side_of = {ours: "us", 1 - ours: "band"}
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        st = json.loads(next(fh))
        bushes = set(map(tuple, st["bushes"]))
        if not bushes: continue
        turns = defaultdict(list)                      # champ -> [turn records]
        pos = defaultdict(dict)                        # champ -> round -> (x, y) or None
        team_of = {}
        for l in fh:
            r = json.loads(l)
            if r.get("kind") != "turn": continue
            s = r["state"]["self"] or {}
            ok = s.get("alive", True) and s.get("x") is not None
            turns[r["champ"]].append(r); team_of[r["champ"]] = r["team"]
            pos[r["champ"]][r["round"]] = (s["x"], s["y"]) if ok else None
    maxr = max(max(p) for p in pos.values() if p)
    for c, ts in turns.items():
        side = side_of[team_of[c]]
        prev = None
        for r in ts:
            s = r["state"]["self"] or {}
            ok = s.get("alive", True) and s.get("x") is not None
            if not ok:
                prev = None; continue
            me = (s["x"], s["y"]); rd = r["round"]
            en = [(ch["x"], ch["y"]) for ch in r["state"]["champs"] if ch["team"] != team_of[c]]
            near = [e for e in en if cheb(e, me) <= 4]
            S[side]["alive"] += 1
            if me in bushes:
                S[side]["in_bush"] += 1
                if near: S[side]["contact_in_bush"] += 1
            if near: S[side]["contact"] += 1
            # a hide: stepped into a bush this turn with an enemy within 5 last turn
            if prev is not None and prev[0] not in bushes and me in bushes and prev[1]:
                S[side]["hides"] += 1
                hp0 = s["hp"]; mx = max(1, s["max"])
                died = False; hp_end = hp0; broke = None
                for k in range(1, 7):
                    p = pos[c].get(rd + k)
                    if p is None and (rd + k) in pos[c]: died = True; break
                nr = {x["round"]: x for x in ts}
                last = None
                for k in range(1, 7):
                    x = nr.get(rd + k)
                    if x is None or not x["state"]["self"] or x["state"]["self"].get("x") is None: continue
                    last = x
                if died: S[side]["hide_died6"] += 1
                elif last is not None:
                    ls = last["state"]["self"]
                    S[side]["hide_hp_lost"] += (hp0 - ls["hp"]) / mx
                    lme = (ls["x"], ls["y"])
                    if not any(cheb((ch["x"], ch["y"]), lme) <= 4 for ch in last["state"]["champs"] if ch["team"] != team_of[c]):
                        S[side]["hide_broke6"] += 1
                # the nearest enemy's response over the next 3 rounds (true positions)
                chaser, cd = None, 99
                for oc, ot in team_of.items():
                    if ot == team_of[c]: continue
                    p = pos[oc].get(rd)
                    if p is not None and cheb(p, me) < cd: chaser, cd = oc, cheb(p, me)
                if chaser is not None and cd <= 3 and hp0 <= 0.4 * mx:
                    other = side_of[team_of[chaser]]
                    after[other]["weak_hides"] += 1
                    if died: after[other]["weak_killed6"] += 1
                    if any(pos[chaser].get(rd + k) in bushes for k in (1, 2, 3)): after[other]["weak_entered_bush"] += 1
                if chaser is not None and cd <= 5:
                    other = side_of[team_of[chaser]]
                    p3 = pos[chaser].get(rd + 3)
                    if p3 is None:
                        after[other]["gone"] += 1
                    else:
                        d3 = cheb(p3, me)
                        if d3 <= 1: after[other]["reached_bush"] += 1
                        elif d3 < cd: after[other]["closed"] += 1
                        elif d3 == cd: after[other]["held"] += 1
                        else: after[other]["left"] += 1
                    # did the chaser step onto a bush tile within 3 rounds (chasing through)?
                    if any(pos[chaser].get(rd + k) in bushes for k in (1, 2, 3)):
                        after[other]["entered_bush"] += 1
            prev = (me, bool(near) or any(cheb(e, me) <= 5 for e in en))
for side in ("us", "band"):
    c = S[side]; a = after[side]
    hides = max(1, c["hides"]); resp = max(1, a["reached_bush"] + a["closed"] + a["held"] + a["left"] + a["gone"])
    print(f"{side:5s} living turns {c['alive']}: in a bush {100*c['in_bush']/max(1,c['alive']):.1f}%, "
          f"of contact turns {100*c['contact_in_bush']/max(1,c['contact']):.1f}% in a bush; "
          f"hides {c['hides']} ({c['hides']/70:.1f}/game): died within 6 {100*c['hide_died6']/hides:.0f}%, "
          f"contact broken by 6 {100*c['hide_broke6']/hides:.0f}%, hp lost {100*c['hide_hp_lost']/hides:.0f}%")
    wh = max(1, a["weak_hides"])
    print(f"      weak hiders (hp<=40%, chaser within 3): {a['weak_hides']} ({a['weak_hides']/70:.1f}/game), killed within 6 {100*a['weak_killed6']/wh:.0f}%, chaser stepped onto a bush {100*a['weak_entered_bush']/wh:.0f}%")
    print(f"      as the chaser, after the other side hides ({resp}): reached the bush {100*a['reached_bush']/resp:.0f}%, "
          f"closed {100*a['closed']/resp:.0f}%, held {100*a['held']/resp:.0f}%, left {100*a['left']/resp:.0f}%, "
          f"gone {100*a['gone']/resp:.0f}%; stepped onto a bush within 3 rounds {100*a['entered_bush']/resp:.0f}%")
