"""Our contact turns (enemy champion within 4) split by the mode our body was in (from our stdout)."""
import json, gzip, glob, os, re, sys, zipfile, bisect
from collections import defaultdict, Counter
D = sys.argv[1]; US = "Teto Territory"
NAMES = ["RYTH","VESPER_VALE","VENDETTA","CASSI","ISSAC","STELLAR","PRISM","MARCELLA","WILLOW","REN","VARG","TAVI","HEART_OF_RUIN"]
def cheb(a, b): return max(abs(a[0]-b[0]), abs(a[1]-b[1]))
idx = json.load(open(f"{D}/raw/index.json", encoding="utf-8"))
T = defaultdict(Counter)
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
    prev = {}
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        next(fh)
        for l in fh:
            r = json.loads(l)
            if r.get("kind") != "turn": continue
            t, c, s = r["team"], r["champ"], r["state"]["self"] or {}
            ok = s.get("alive", True) and s.get("x") is not None
            if not ok:
                prev[(t, c)] = None; continue
            me = (s["x"], s["y"]); hp = s["hp"]
            p = prev.get((t, c)); prev[(t, c)] = hp
            if t != ours: continue
            en = [ch for ch in r["state"]["champs"] if ch["team"] != t and cheb((ch["x"], ch["y"]), me) <= 4]
            if not en: continue
            nm = NAMES[s["type"]] if s.get("type") is not None and s["type"] < 13 else "?"
            rs = rounds.get(nm, [])
            i = bisect.bisect_right(rs, r["round"]) - 1
            mode = modes[nm][i][1] if i >= 0 else "?"
            a = T[mode]; a["turns"] += 1
            if any(x["k"] == 5 and "to" in x and any((ch["x"], ch["y"]) == tuple(x["to"]) for ch in r["state"]["champs"] if ch["team"] != t) for x in r["actions"]): a["attack"] += 1
            if any(x["k"] == 6 for x in r["actions"]): a["cast"] += 1
            if p is not None and p > hp: a["taken"] += p - hp
            a["hp_sum"] += hp / max(1, s["max"])
tot = sum(a["turns"] for a in T.values())
print(f"{'mode':10}{'% of contact':>14}{'attack %':>10}{'cast %':>8}{'dmg taken/turn':>16}{'mean hp':>9}")
for mode, a in sorted(T.items(), key=lambda kv: -kv[1]["turns"]):
    print(f"{mode:10}{100*a['turns']/tot:>13.1f}%{100*a['attack']/a['turns']:>10.1f}{100*a['cast']/a['turns']:>8.1f}{a['taken']/a['turns']:>16.1f}{a['hp_sum']/a['turns']:>9.2f}")
