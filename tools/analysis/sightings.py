"""First-sighting episodes in the opening: an enemy comes within 8 of a body that saw none for 3 rounds.
What happens over the next rounds, per side.  python sightings.py <base> <raw> <ladder> <keep: teto|opp|band> <out.json> [band names]"""
import json, gzip, os, sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
BASE, RAW, LAD, WHO, OUT = sys.argv[1:6]
BAND = set(json.load(open(sys.argv[6]))) if len(sys.argv) > 6 else set()
US = "Teto Territory"; END = 250
def cheb(a, b): return max(abs(a[0]-b[0]), abs(a[1]-b[1]))
def one(item):
    mid, v = item
    f = f"{BASE}/{LAD}/{mid}.jsonl.gz"
    if not os.path.exists(f): return []
    teams = {0 if t["side"] == "a" else 1: t for t in v["teams"]}
    if WHO == "teto": keep = [k for k, t in teams.items() if t["name"] == US]
    elif WHO == "opp": keep = [k for k, t in teams.items() if t["name"] != US] if any(t["name"] == US for t in teams.values()) else []
    else: keep = [k for k, t in teams.items() if t["name"] in BAND]
    if not keep: return []
    pos = defaultdict(dict)       # round -> (team, champ) -> (pos, hp ratio)
    alive_r = defaultdict(dict)
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        stc = json.loads(next(fh))
        sa = stc["spawnA"]
        sc = {0: ((sa["minX"]+sa["maxX"])/2, (sa["minY"]+sa["maxY"])/2),
              1: (sum(x for x, _ in stc["spawnB"])/len(stc["spawnB"]), sum(y for _, y in stc["spawnB"])/len(stc["spawnB"]))}
        for l in fh:
            r = json.loads(l)
            if r.get("kind") != "turn": continue
            rd = r["round"]
            if rd > END + 25: break
            s = r["state"]["self"] or {}
            key = (r["team"], r["champ"])
            ok = s.get("alive", True) and s.get("x") is not None
            alive_r[rd][key] = ok
            if ok: pos[rd][key] = ((s["x"], s["y"]), s["hp"] / max(1, s["max"]))
    out = []
    last_seen = {}
    for rd in sorted(pos):
        if rd > END: break
        for key, (p, hp) in pos[rd].items():
            t = key[0]
            if t not in keep: continue
            en = [(k, q) for k, (q, h) in pos[rd].items() if k[0] != t and cheb(p, q) <= 8]
            if en and rd - last_seen.get(key, -99) > 3:
                allies = sum(1 for k, (q, h) in pos[rd].items() if k[0] == t and k != key and cheb(p, q) <= 4)
                d0 = min(cheb(p, q) for _, q in en)
                # 5 rounds later
                later = pos.get(rd + 5, {}).get(key)
                d5 = None; allies5 = None; moved_home = None
                if later:
                    q5 = later[0]
                    ens5 = [q for k, (q, h) in pos[rd + 5].items() if k[0] != t]
                    d5 = min((cheb(q5, q) for q in ens5), default=None)
                    allies5 = sum(1 for k, (q, h) in pos[rd + 5].items() if k[0] == t and k != key and cheb(q5, q) <= 4)
                    home = (round(sc[t][0]), round(sc[t][1]))
                    moved_home = cheb(p, home) - cheb(q5, home)
                died = any(not alive_r.get(rd + i, {}).get(key, True) for i in range(1, 21))
                kills = sum(1 for k, _ in en if any(not alive_r.get(rd + i, {}).get(k, True) for i in range(1, 21)))
                ally_died = any(not alive_r.get(rd + i, {}).get(k, True) for k in pos[rd] if k[0] == t and k != key for i in range(1, 21)
                                if k in pos[rd] and cheb(pos[rd][k][0], p) <= 8)
                out.append(dict(round=rd, hp=hp, enemies=len(en), allies=allies, d0=d0, d5=d5, allies5=allies5, moved_home=moved_home,
                                died=died, kills=kills, ally_died=ally_died,
                                spawn_dist=cheb(p, (round(sc[t][0]), round(sc[t][1])))))
            if en: last_seen[key] = rd
    return out
if __name__ == "__main__":
    idx = json.load(open(f"{BASE}/{RAW}/index.json", encoding="utf-8"))
    items = [(m, v) for m, v in idx.items() if v.get("status", "finished") == "finished"]
    rows = []
    with ProcessPoolExecutor(max_workers=int(os.environ.get("BC_JOBS", 8))) as ex:
        for r in ex.map(one, items, chunksize=2): rows.extend(r)
    json.dump(rows, open(OUT, "w")); print(len(rows), "sightings", file=sys.stderr)
