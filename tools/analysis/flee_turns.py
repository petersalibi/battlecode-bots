"""Our flee-mode contact turns: nearest enemy distance at turn start, whether we attacked/moved,
hp band, the chaser's class, and the fate: died within 5 turns?"""
import json, gzip, glob, os, re, sys, zipfile, bisect
from collections import defaultdict, Counter
D = sys.argv[1]; US = "Teto Territory"
NAMES = ["RYTH","VESPER_VALE","VENDETTA","CASSI","ISSAC","STELLAR","PRISM","MARCELLA","WILLOW","REN","VARG","TAVI","HEART_OF_RUIN"]
RANGED = {1,3,5,6,8,11,12}
def cheb(a, b): return max(abs(a[0]-b[0]), abs(a[1]-b[1]))
idx = json.load(open(f"{D}/raw/index.json", encoding="utf-8"))
dist_h = Counter(); act = Counter(); hpb = Counter(); chaser = Counter(); tot = 0
melee_adj = Counter()   # for melee bodies with an enemy adjacent at turn start: what we did
died5 = Counter()
for f in sorted(glob.glob(f"{D}/ladder/*.jsonl.gz")):
    mid = os.path.basename(f).split(".")[0]; v = idx.get(mid)
    if not v: continue
    ours = next((0 if t["side"] == "a" else 1 for t in v["teams"] if t["name"] == US), None)
    if ours is None: continue
    side = "a" if ours == 0 else "b"
    modes = defaultdict(list)
    try:
        for l in zipfile.ZipFile(f"{D}/raw/{mid}.zip").read(f"team-{side}.stdout").decode("utf-8", "replace").replace("\0", "").splitlines():
            m = re.match(r"^r(\d+) ([A-Z_]+): (\w+) hp=", l)
            if m: modes[m.group(2)].append((int(m.group(1)), m.group(3)))
    except Exception: continue
    rounds = {k: [r for r, _ in v_] for k, v_ in modes.items()}
    turns = []
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        next(fh)
        for l in fh:
            r = json.loads(l)
            if r.get("kind") != "turn" or r["team"] != ours: continue
            turns.append(r)
    # alive-by-round per champ for the fate check
    alive = defaultdict(dict)
    for r in turns:
        s = r["state"]["self"] or {}
        alive[r["champ"]][r["round"]] = bool(s.get("alive", True) and s.get("x") is not None)
    for r in turns:
        s = r["state"]["self"] or {}
        if not (s.get("alive", True) and s.get("x") is not None): continue
        me = (s["x"], s["y"]); hp = s["hp"]; mx = max(1, s.get("max", s.get("maxHp", hp)))
        en = [ch for ch in r["state"]["champs"] if ch["team"] != ours and cheb((ch["x"], ch["y"]), me) <= 4]
        if not en: continue
        nm = NAMES[s["type"]] if s.get("type") is not None and s["type"] < 13 else "?"
        rs = rounds.get(nm, []); i = bisect.bisect_right(rs, r["round"]) - 1
        mode = modes[nm][i][1] if i >= 0 else "?"
        if mode != "flee": continue
        tot += 1
        d = min(cheb((ch["x"], ch["y"]), me) for ch in en); dist_h[d] += 1
        near = min(en, key=lambda ch: cheb((ch["x"], ch["y"]), me))
        chaser["ranged" if near.get("type") in RANGED else "melee"] += 1
        hpb[int(hp / mx * 10) / 10] += 1
        ks = r["actions"]
        hit = any(x["k"] == 5 and "to" in x and any((ch["x"], ch["y"]) == tuple(x["to"]) for ch in en) for x in ks)
        moved = any(x["k"] in (1, 2) for x in ks)   # k codes: guess move=1/2; check below
        kinds = Counter(x["k"] for x in ks)
        act[tuple(sorted(kinds))] += 1
        if s["type"] not in RANGED and d <= 1:
            melee_adj["hit" if hit else "no hit"] += 1
        rd = r["round"]; a = alive[r["champ"]]
        dead = any(a.get(rr) is False for rr in range(rd + 1, rd + 6))
        died5["died<=5" if dead else "survived"] += 1
print("flee contact turns", tot)
print("nearest enemy dist at turn start", dict(sorted(dist_h.items())))
print("chaser class", dict(chaser))
print("hp band", dict(sorted(hpb.items())))
print("action kind sets (k codes)", act.most_common(8))
print("melee with enemy adjacent:", dict(melee_adj))
print("fate", dict(died5))
