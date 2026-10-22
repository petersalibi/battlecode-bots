"""Cornered turns (a melee enemy adjacent at the start of our turn, our body melee): outcome over
the next 5 turns by the mode we chose (flee vs fight), hp band, and distance to the nearest
escape (own spawn, bush, ally in sight). Outcome = died within 5 turns, hp lost, contact broken."""
import json, gzip, glob, os, re, sys, zipfile, bisect
from collections import defaultdict, Counter
D = sys.argv[1]; US = "Teto Territory"
NAMES = ["RYTH","VESPER_VALE","VENDETTA","CASSI","ISSAC","STELLAR","PRISM","MARCELLA","WILLOW","REN","VARG","TAVI","HEART_OF_RUIN"]
RANGED = {1,3,5,6,8,11,12}
def cheb(a, b): return max(abs(a[0]-b[0]), abs(a[1]-b[1]))
idx = json.load(open(f"{D}/raw/index.json", encoding="utf-8"))
cells = defaultdict(lambda: [0, 0, 0.0, 0])   # (mode, hpband, escbucket) -> [n, died, hp lost frac, contact broken]
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
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        st = json.loads(next(fh))
        sp = st["spawnA"] if ours == 0 else st["spawnB"]
        if isinstance(sp, dict):
            spawn = [(x, y) for x in range(sp["minX"], sp["maxX"] + 1) for y in range(sp["minY"], sp["maxY"] + 1)]
        else:
            spawn = [tuple(t) for t in sp]
        bushes = [tuple(b) for b in st["bushes"]]
        turns = defaultdict(list)
        for l in fh:
            r = json.loads(l)
            if r.get("kind") == "turn" and r["team"] == ours: turns[r["champ"]].append(r)
    for c, ts in turns.items():
        byround = {r["round"]: r for r in ts}
        for r in ts:
            s = r["state"]["self"] or {}
            if not (s.get("alive", True) and s.get("x") is not None) or s["type"] in RANGED: continue
            me = (s["x"], s["y"]); hp = s["hp"]; mx = max(1, s["max"])
            champs = r["state"]["champs"]
            en = [ch for ch in champs if ch["team"] != ours]
            adj_melee = [ch for ch in en if cheb((ch["x"], ch["y"]), me) <= 1 and ch["type"] not in RANGED]
            if not adj_melee: continue
            nm = NAMES[s["type"]]; rs = rounds.get(nm, []); i = bisect.bisect_right(rs, r["round"]) - 1
            mode = modes[nm][i][1] if i >= 0 else "?"
            if mode not in ("flee", "fight"): continue
            allies = [ch for ch in champs if ch["team"] == ours and (ch["x"], ch["y"]) != me and ch.get("hp", 0) > 0]
            esc = min([cheb(me, t) for t in spawn] + [cheb(me, b) for b in bushes] + [cheb(me, (a["x"], a["y"])) for a in allies] + [99])
            eb = "0-1" if esc <= 1 else "2-3" if esc <= 3 else "4-6" if esc <= 6 else "7+"
            hb = "<30%" if hp / mx < 0.3 else "30-45%" if hp / mx < 0.45 else "45-70%" if hp / mx < 0.7 else "70%+"
            # outcome over next 5 of our turns
            died = False; hp_end = hp; broken = None
            for k in range(1, 6):
                nr = byround.get(r["round"] + k)
                if nr is None: continue
                ns = nr["state"]["self"] or {}
                if not (ns.get("alive", True) and ns.get("x") is not None): died = True; hp_end = 0; break
                hp_end = ns["hp"]
                broken = not any(cheb((ch["x"], ch["y"]), (ns["x"], ns["y"])) <= 4 for ch in nr["state"]["champs"] if ch["team"] != ours)
            cell = cells[(mode, hb, eb)]
            cell[0] += 1; cell[1] += died; cell[2] += (hp - hp_end) / mx; cell[3] += bool(broken)
print(f"{'mode':5s} {'hp':7s} {'escape':6s} {'n':>6s} {'died5':>6s} {'hp lost':>8s} {'broke':>6s}")
for key in sorted(cells, key=lambda k: (k[1], k[2], k[0])):
    n, d, lost, br = cells[key]
    if n < 20: continue
    print(f"{key[0]:5s} {key[1]:7s} {key[2]:6s} {n:6d} {100*d/n:5.0f}% {100*lost/n:7.0f}% {100*br/n:5.0f}%")
