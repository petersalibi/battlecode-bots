"""Calibrate the fight power balance: for each of our bodies in contact, the local balance (us + allies
within 6 vs enemies within 6, per-champion kit power) and what happened in the next 15 rounds."""
import json, gzip, glob, os, sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
US = "Teto Territory"
NAMES = ["RYTH","VESPER_VALE","VENDETTA","CASSI","ISSAC","STELLAR","PRISM","MARCELLA","WILLOW","REN","VARG","TAVI","HEART_OF_RUIN"]
KIT = {"CASSI": (1.0, 0.25, 0.0), "ISSAC": (1.125, 0.0, 0.0), "RYTH": (1.125, 0.125, 0.0), "VESPER_VALE": (1.125, 0.0, 0.0),
       "PRISM": (1.0, 0.31, 0.0), "STELLAR": (1.0, 0.43, 0.0), "VENDETTA": (1.125, 0.0, 0.0), "MARCELLA": (1.125, 0.25, 0.0),
       "TAVI": (1.125, 0.18, 0.0), "WILLOW": (1.125, 0.0, 0.0), "REN": (1.0, 0.28, 0.0), "VARG": (1.25, 0.0, 2.5), "HEART_OF_RUIN": (1.0, 0.22, 1.5)}
def cheb(a, b): return max(abs(a[0]-b[0]), abs(a[1]-b[1]))
def power(ch, mode):
    hp, st, fo = ch["hp"], ch.get("str") or 0, ch.get("focus") or 0
    if mode == "raw": return hp * st
    if mode == "flat": return hp * (st + 0.25 * fo)
    nm = NAMES[ch["type"]] if ch.get("type") is not None and ch["type"] < 13 else ""
    s, f, sh = KIT.get(nm, (1.0, 0.0, 0.0))
    return (hp + fo * sh) * (st * s + fo * f)
def one(path):
    d = os.path.dirname(os.path.dirname(path)); mid = os.path.basename(path).split(".")[0]
    idx = json.load(open(os.path.join(d, "raw", "index.json"), encoding="utf-8")); v = idx.get(mid)
    if not v: return []
    ours = next((0 if t["side"] == "a" else 1 for t in v["teams"] if t["name"] == US), None)
    if ours is None: return []
    alive = defaultdict(dict); samples = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        next(fh)
        for l in fh:
            r = json.loads(l)
            if r.get("kind") != "turn": continue
            rd = r["round"]; s = r["state"]["self"] or {}
            ok = s.get("alive", True) and s.get("x") is not None
            alive[rd][(r["team"], r["champ"])] = ok
            if r["team"] != ours or not ok or rd % 3: continue          # every third round per body is enough
            me = (s["x"], s["y"])
            chs = [c for c in r["state"]["champs"] if c.get("hp", 0) > 0 and c.get("str") is not None]
            en = [c for c in chs if c["team"] != ours and cheb((c["x"], c["y"]), me) <= 6]
            if not en or not any(cheb((c["x"], c["y"]), me) <= 4 for c in en): continue
            al = [c for c in chs if c["team"] == ours and cheb((c["x"], c["y"]), me) <= 6]
            bal = {m: sum(power(c, m) for c in al) / max(1.0, sum(power(c, m) for c in en)) for m in ("raw", "flat", "kit")}
            samples.append(dict(round=rd, champ=r["champ"], en=[(1 - ours, c["i"]) for c in en], al=[(ours, c["i"]) for c in al], bal=bal, n_en=len(en), n_al=len(al)))
    out = []
    for s in samples:
        rd = s["round"]
        def died(k): return any(alive.get(rd + i, {}).get(k, True) is False for i in range(1, 16))
        out.append(dict(bal=s["bal"], n_en=s["n_en"], n_al=s["n_al"], me_died=died((ours, s["champ"])),
                        our_deaths=sum(died(k) for k in s["al"]), their_deaths=sum(died(k) for k in s["en"])))
    return out
if __name__ == "__main__":
    files = []
    for d in sys.argv[2:]: files += glob.glob(os.path.join(d, "ladder", "*.jsonl.gz"))
    rows = []
    with ProcessPoolExecutor(max_workers=int(os.environ.get("BC_JOBS", 8))) as ex:
        for r in ex.map(one, files, chunksize=2): rows.extend(r)
    json.dump(rows, open(sys.argv[1], "w")); print(len(rows), "contact samples from", len(files), "games", file=sys.stderr)
