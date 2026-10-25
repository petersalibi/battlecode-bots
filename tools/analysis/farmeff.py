"""Farming efficiency per side and champion: what living turns are spent on, and gems per camp hit."""
import json, gzip, glob, os, sys
from collections import defaultdict, Counter
from concurrent.futures import ProcessPoolExecutor
US = "Teto Territory"
NAMES = ["RYTH","VESPER_VALE","VENDETTA","CASSI","ISSAC","STELLAR","PRISM","MARCELLA","WILLOW","REN","VARG","TAVI","HEART_OF_RUIN"]
def cheb(a, b): return max(abs(a[0]-b[0]), abs(a[1]-b[1]))
def one(path):
    d = os.path.dirname(os.path.dirname(path)); mid = os.path.basename(path).split(".")[0]
    v = json.load(open(os.path.join(d, "raw", "index.json"), encoding="utf-8")).get(mid)
    if not v: return []
    ours = next((0 if t["side"] == "a" else 1 for t in v["teams"] if t["name"] == US), None)
    st = defaultdict(lambda: dict(act=Counter(), gath=0, camp_hits=0, captures=0, type=None))
    prev = {}
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        stc = json.loads(next(fh))
        camp_tiles = {(c["x"], c["y"]) for c in stc["camps"]}
        sa = stc["spawnA"]
        spawn = {0: {(x, y) for x in range(sa["minX"], sa["maxX"] + 1) for y in range(sa["minY"], sa["maxY"] + 1)}, 1: {tuple(t) for t in stc["spawnB"]}}
        for l in fh:
            r = json.loads(l)
            if r.get("kind") != "turn": continue
            t, c, s = r["team"], r["champ"], r["state"]["self"] or {}
            a = st[(t, c)]
            ok = s.get("alive", True) and s.get("x") is not None
            p = prev.get((t, c))
            if not ok:
                a["act"]["dead"] += 1; prev[(t, c)] = None; continue
            a["type"] = s.get("type")
            me = (s["x"], s["y"]); g = s.get("gems") or 0
            ks = r["actions"]
            hit_camp = any(x["k"] == 5 and "to" in x and tuple(x["to"]) in camp_tiles for x in ks)
            cast = any(x["k"] == 6 for x in ks)
            hit_champ = any(x["k"] == 5 and "to" in x and tuple(x["to"]) not in camp_tiles for x in ks)
            contact = any(ch["team"] != t and cheb((ch["x"], ch["y"]), me) <= 4 for ch in r["state"]["champs"])
            moved = p is not None and p[0] != me
            chan = any(x["k"] in (11, 12) for x in ks)
            if me in spawn[t]: k = "on spawn"
            elif contact: k = "contact"
            elif hit_camp: k = "hit camp"
            elif chan: k = "channel"
            elif moved: k = "walk"
            else: k = "idle"
            a["act"][k] += 1
            if hit_camp: a["camp_hits"] += 1
            if p is not None and g > p[1]:
                a["gath"] += g - p[1]; a["captures"] += 1
            prev[(t, c)] = (me, g)
    out = []
    for (t, c), a in st.items():
        if a["type"] is None: continue
        out.append(dict(side="us" if t == ours else "them", champ=NAMES[a["type"]] if a["type"] < 13 else "?", act=dict(a["act"]),
                        gath=a["gath"], camp_hits=a["camp_hits"], captures=a["captures"]))
    return out
if __name__ == "__main__":
    files = []
    for d in sys.argv[2:]: files += glob.glob(os.path.join(d, "ladder", "*.jsonl.gz"))
    rows = []
    with ProcessPoolExecutor(max_workers=int(os.environ.get("BC_JOBS", 16))) as ex:
        for r in ex.map(one, files, chunksize=2): rows.extend(r)
    json.dump(rows, open(sys.argv[1], "w")); print(len(rows), "body-games", file=sys.stderr)
