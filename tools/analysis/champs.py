"""Per champion and side: gems gathered, deaths, damage dealt and taken in contact, per body-game."""
import json, gzip, glob, os, sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
US = "Teto Territory"
NAMES = ["RYTH","VESPER_VALE","VENDETTA","CASSI","ISSAC","STELLAR","PRISM","MARCELLA","WILLOW","REN","VARG","TAVI","HEART_OF_RUIN"]
def cheb(a, b): return max(abs(a[0]-b[0]), abs(a[1]-b[1]))
def one(path):
    d = os.path.dirname(os.path.dirname(path)); mid = os.path.basename(path).split(".")[0]
    v = json.load(open(os.path.join(d, "raw", "index.json"), encoding="utf-8")).get(mid)
    if not v: return []
    ours = next((0 if t["side"] == "a" else 1 for t in v["teams"] if t["name"] == US), None)
    res = {(0 if t["side"] == "a" else 1): t["result"] for t in v["teams"]}
    st = defaultdict(lambda: dict(gath=0, deaths=0, taken=0.0, dealt=0.0, contact=0, type=None))
    prev = {}
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        next(fh)
        for l in fh:
            r = json.loads(l)
            if r.get("kind") != "turn": continue
            t, c, s = r["team"], r["champ"], r["state"]["self"] or {}
            a = st[(t, c)]
            ok = s.get("alive", True) and s.get("x") is not None
            p = prev.get((t, c))
            if not ok:
                if p and p[0]: a["deaths"] += 1
                prev[(t, c)] = (False, 0, 0); continue
            a["type"] = s.get("type")
            g = s.get("gems") or 0; hp = s["hp"]
            if p and p[0] and g > p[1]: a["gath"] += g - p[1]
            me = (s["x"], s["y"])
            if any(ch["team"] != t and cheb((ch["x"], ch["y"]), me) <= 4 for ch in r["state"]["champs"]):
                a["contact"] += 1
                if p and p[0] and p[2] > hp:
                    a["taken"] += p[2] - hp
                    # credit the damage to the nearest enemy (rough attribution)
                    near = min((ch for ch in r["state"]["champs"] if ch["team"] != t), key=lambda ch: cheb((ch["x"], ch["y"]), me))
                    st[(1 - t, near["i"])]["dealt"] += p[2] - hp
            prev[(t, c)] = (True, g, hp)
    out = []
    for (t, c), a in st.items():
        if a["type"] is None: continue
        side = "us" if t == ours else "them"
        out.append(dict(side=side, champ=NAMES[a["type"]] if a["type"] < 13 else "?", win=res[t] == "win", **{k: a[k] for k in ("gath", "deaths", "taken", "dealt", "contact")}))
    return out
if __name__ == "__main__":
    files = []
    for d in sys.argv[2:]: files += glob.glob(os.path.join(d, "ladder", "*.jsonl.gz"))
    rows = []
    with ProcessPoolExecutor(max_workers=int(os.environ.get("BC_JOBS", 16))) as ex:
        for r in ex.map(one, files, chunksize=2): rows.extend(r)
    json.dump(rows, open(sys.argv[1], "w")); print(len(rows), "body-games", file=sys.stderr)
