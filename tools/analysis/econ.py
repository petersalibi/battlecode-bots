"""Per team per game: gems gathered, lost on death, banked; turn shares (dead, at spawn, still, moving)."""
import json, gzip, os, sys
from concurrent.futures import ProcessPoolExecutor

BASE = sys.argv[1]

def one(item):
    mid, v = item
    f = f"{BASE}/ladder/{mid}.jsonl.gz"
    if not os.path.exists(f):
        return None
    ours = next((t["side"] for t in v["teams"] if "Teto" in t.get("name", "")), None)
    if ours is None:
        return None
    our_team = 0 if ours == "a" else 1
    res = next(t["result"] for t in v["teams"] if t["side"] == ours)
    prev = {}
    agg = {t: dict(gathered=0, lost=0, banked=0, deaths=0, turns=0, dead=0, spawn=0, still=0, carry_sum=0) for t in (0, 1)}
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        static = json.loads(next(fh))
        sa = static["spawnA"]
        spawn = {0: {(x, y) for x in range(sa["minX"], sa["maxX"] + 1) for y in range(sa["minY"], sa["maxY"] + 1)},
                 1: {tuple(t) for t in static["spawnB"]}}
        last_round = 0
        for l in fh:
            r = json.loads(l)
            if r.get("kind") != "turn":
                continue
            t, c, s = r["team"], r["champ"], r["state"]["self"]
            last_round = r["round"]
            a = agg[t]; a["turns"] += 1
            alive = s.get("alive", True) and "x" in s
            p = prev.get((t, c))
            g = s.get("gems", 0)
            if not alive:
                a["dead"] += 1
                if p and p[0]:
                    a["deaths"] += 1; a["lost"] += p[3]
                prev[(t, c)] = (False, None, None, 0)
                continue
            pos = (s["x"], s["y"])
            if pos in spawn[t]:
                a["spawn"] += 1
            elif p and p[0] and p[1] == pos:
                a["still"] += 1
            a["carry_sum"] += g
            if p and p[0]:
                d = g - p[3]
                if d > 0: a["gathered"] += d
                elif d < 0: a["banked"] += -d
            prev[(t, c)] = (True, pos, None, g)
    return dict(map=v["map"], result=res, rounds=last_round, us=agg[our_team], them=agg[1 - our_team])

if __name__ == "__main__":
    idx = json.load(open(f"{BASE}/raw/index.json", encoding="utf-8"))
    items = list(idx.items())[: int(sys.argv[2])]
    out = []
    with ProcessPoolExecutor(max_workers=int(os.environ.get("BC_JOBS", 8))) as ex:
        for i, r in enumerate(ex.map(one, items, chunksize=2)):
            if r: out.append(r)
            if i % 20 == 0: print(f"\r{i}/{len(items)}", end="", file=sys.stderr, flush=True)
    json.dump(out, open(sys.argv[3], "w"))
    print(f"\n{len(out)} games", file=sys.stderr)
