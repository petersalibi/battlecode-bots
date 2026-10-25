"""Our deaths before round 150 in challenge games: mode history, health, warning time, allies, enemies."""
import json, gzip, glob, os, re, sys, zipfile, statistics as st
from collections import defaultdict, Counter
D = sys.argv[1]; END = 150
US = "Teto Territory"
NAMES = ["RYTH","VESPER_VALE","VENDETTA","CASSI","ISSAC","STELLAR","PRISM","MARCELLA","WILLOW","REN","VARG","TAVI","HEART_OF_RUIN"]
idx = json.load(open(f"{D}/raw/index.json", encoding="utf-8"))
def cheb(a, b): return max(abs(a[0]-b[0]), abs(a[1]-b[1]))
rows = []
for f in sorted(glob.glob(f"{D}/ladder/*.jsonl.gz")):
    mid = os.path.basename(f).split(".")[0]; v = idx.get(mid)
    if not v: continue
    ours = next((0 if t["side"] == "a" else 1 for t in v["teams"] if t["name"] == US), None)
    if ours is None: continue
    side = "a" if ours == 0 else "b"
    modes = defaultdict(list)
    try:
        for l in zipfile.ZipFile(f"{D}/raw/{mid}.zip").read(f"team-{side}.stdout").decode("utf-8", "replace").replace("\0", "").splitlines():
            m = re.match(r"^r(\d+) ([A-Z_]+): (\w+) hp=(\d+)%.*?target=(-?\d+) enemies=(\d+)", l)
            if m: modes[m.group(2)].append((int(m.group(1)), m.group(3), int(m.group(4))))
    except Exception: pass
    hist = defaultdict(list); pos = {}; typ = {}
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        stc = json.loads(next(fh))
        sa = stc["spawnA"]; spawnA = {(x, y) for x in range(sa["minX"], sa["maxX"]+1) for y in range(sa["minY"], sa["maxY"]+1)}
        spawn = {0: spawnA, 1: {tuple(t) for t in stc["spawnB"]}}
        prev_alive = {}
        for l in fh:
            r = json.loads(l)
            if r.get("kind") != "turn": continue
            rd = r["round"]
            if rd > END: break
            t, c, s = r["team"], r["champ"], r["state"]["self"] or {}
            alive = s.get("alive", True) and s.get("x") is not None
            if alive:
                pos[(t, c)] = (rd, (s["x"], s["y"])); typ[(t, c)] = s.get("type")
            if t == ours:
                if alive:
                    me = (s["x"], s["y"])
                    en = [(cheb((ch["x"], ch["y"]), me)) for ch in r["state"]["champs"] if ch["team"] != ours]
                    al = sum(1 for (tt, cc), (r2, q) in pos.items() if tt == ours and cc != c and r2 >= rd - 1 and cheb(q, me) <= 4)
                    hist[c].append((rd, s["hp"] / max(1, s["max"]), sum(1 for d in en if d <= 8), min(en, default=99), al,
                                    min(cheb(me, q) for q in spawn[ours]), [a["k"] for a in r["actions"]]))
                elif prev_alive.get(c):
                    h = hist[c]
                    seen = [x for x in h if x[2] > 0]
                    # how many rounds the enemies were in sight before the death (contiguous run ending at death)
                    run = 0
                    for x in reversed(h):
                        if x[2] > 0: run += 1
                        else: break
                    first_seen = h[-run] if run else h[-1]
                    nm = NAMES[typ[(t, c)]] if typ.get((t, c)) is not None and typ[(t, c)] < 13 else "?"
                    ms = [(r2, m, hp) for r2, m, hp in modes.get(nm, []) if r2 <= rd]
                    modes_in_window = Counter(m for r2, m, hp in ms if r2 >= rd - run - 1)
                    mode_at_sight = next((m for r2, m, hp in reversed(ms) if r2 <= first_seen[0]), None)
                    rows.append(dict(map=v["map"], round=rd, champ=nm, warn=run, hp_at_sight=first_seen[1], enemies_at_sight=first_seen[2],
                                     dist_at_sight=first_seen[3], allies_at_sight=first_seen[4], spawn_dist=first_seen[5],
                                     enemies_max=max((x[2] for x in h[-run:]), default=0) if run else 0,
                                     mode_at_sight=mode_at_sight, mode_last=ms[-1][1] if ms else None, modes=dict(modes_in_window),
                                     hits_on_champ=sum(1 for x in h[-run:] for k in x[6] if k == 5) if run else 0))
                prev_alive[c] = alive
json.dump(rows, open(sys.argv[2], "w"))
print(len(rows), "early deaths")
