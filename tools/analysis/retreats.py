"""Retreat episodes (in contact, moving away from the nearest enemy 2+ turns running), both sides of challenge games."""
import json, gzip, glob, os, sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
US = "Teto Territory"
def cheb(a, b): return max(abs(a[0]-b[0]), abs(a[1]-b[1]))
def one(path):
    d = os.path.dirname(os.path.dirname(path)); mid = os.path.basename(path).split(".")[0]
    v = json.load(open(os.path.join(d, "raw", "index.json"), encoding="utf-8")).get(mid)
    if not v: return []
    ours = next((0 if t["side"] == "a" else 1 for t in v["teams"] if t["name"] == US), None)
    if ours is None: return []
    track = defaultdict(list)   # key -> [(round, pos, hp, maxhp, nearest enemy dist, n enemies<=6, nearest ally dist, spawn dist, alive)]
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        stc = json.loads(next(fh))
        sa = stc["spawnA"]
        sc = {0: ((sa["minX"]+sa["maxX"])//2, (sa["minY"]+sa["maxY"])//2), 1: tuple(stc["spawnB"][len(stc["spawnB"])//2])}
        for l in fh:
            r = json.loads(l)
            if r.get("kind") != "turn": continue
            t, c, s = r["team"], r["champ"], r["state"]["self"] or {}
            ok = s.get("alive", True) and s.get("x") is not None
            if not ok:
                track[(t, c)].append((r["round"], None)); continue
            me = (s["x"], s["y"])
            en = [cheb((ch["x"], ch["y"]), me) for ch in r["state"]["champs"] if ch["team"] != t]
            al = [cheb((ch["x"], ch["y"]), me) for ch in r["state"]["champs"] if ch["team"] == t and ch["i"] != c]
            track[(t, c)].append((r["round"], (me, s["hp"], s["max"], min(en, default=99), sum(1 for x in en if x <= 6), min(al, default=99), cheb(me, sc[t]))))
    out = []
    for (t, c), tr in track.items():
        i = 1
        while i < len(tr) - 2:
            a, b, cc = tr[i - 1][1], tr[i][1], tr[i + 1][1]
            if a and b and cc and a[3] <= 4 and b[3] > a[3] - 0 and b[3] >= a[3] and cc[3] >= b[3] and (b[3] > a[3] or cc[3] > b[3]):
                # retreat starts at a; follow while the distance keeps not shrinking or until out of contact
                j = i; dmg = 0.0; steps = 0; toward_ally = 0; toward_home = 0
                while j < len(tr) and tr[j][1] and tr[j - 1][1] and tr[j][1][3] <= 6 and steps < 12:
                    p0, p1 = tr[j - 1][1], tr[j][1]
                    if p1[1] < p0[1]: dmg += p0[1] - p1[1]
                    if p1[5] < p0[5]: toward_ally += 1
                    if p1[6] < p0[6]: toward_home += 1
                    steps += 1; j += 1
                start_round = tr[i - 1][0]
                died = any(x[1] is None for x in tr[i:i + 16])
                out.append(dict(ours=(t == ours), hp=a[1] / max(1, a[2]), n_en=a[4], ally=a[5], steps=steps, dmg_per_turn=dmg / max(1, steps),
                                toward_ally=toward_ally / max(1, steps), toward_home=toward_home / max(1, steps), died=died, spawn_dist=a[6]))
                i = j + 1
            else:
                i += 1
    return out
if __name__ == "__main__":
    files = []
    for d in sys.argv[2:]: files += glob.glob(os.path.join(d, "ladder", "*.jsonl.gz"))
    rows = []
    with ProcessPoolExecutor(max_workers=int(os.environ.get("BC_JOBS", 16))) as ex:
        for r in ex.map(one, files, chunksize=2): rows.extend(r)
    json.dump(rows, open(sys.argv[1], "w")); print(len(rows), "retreats from", len(files), "games", file=sys.stderr)
