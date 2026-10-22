"""Every champion death in challenge games, both sides: attackers, helpers, casts, mode, half.

python fights.py <challenge dir> <out.json>
"""
import json, gzip, glob, os, re, sys, zipfile
from collections import defaultdict, Counter
from concurrent.futures import ProcessPoolExecutor

D = sys.argv[1]
US = "Teto Territory"
NAMES = ["RYTH","VESPER_VALE","VENDETTA","CASSI","ISSAC","STELLAR","PRISM","MARCELLA","WILLOW","REN","VARG","TAVI","HEART_OF_RUIN"]


def cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def one(path):
    mid = os.path.basename(path).split(".")[0]
    idx = json.load(open(f"{D}/raw/index.json", encoding="utf-8"))
    v = idx.get(mid)
    if not v: return []
    ours = next((0 if t["side"] == "a" else 1 for t in v["teams"] if t["name"] == US), None)
    if ours is None: return []
    res = next(t["result"] for t in v["teams"] if t["name"] == US)
    opp = next(t["name"] for t in v["teams"] if t["name"] != US)
    side = "a" if ours == 0 else "b"
    modes = defaultdict(list)
    try:
        log = zipfile.ZipFile(f"{D}/raw/{mid}.zip").read(f"team-{side}.stdout").decode("utf-8", "replace").replace("\0", "").splitlines()
        for l in log:
            m = re.match(r"^r(\d+) ([A-Z_]+): (\w+) hp=", l)
            if m: modes[m.group(2)].append((int(m.group(1)), m.group(3)))
    except Exception:
        pass
    # timeline: per round, each champion's position/alive/type, and its actions
    pos = {}; alive = {}; typ = {}; events = []   # events: (round, team, champ, kind, target tile)
    deaths = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        st = json.loads(next(fh))
        sa = st["spawnA"]; ca = ((sa["minX"] + sa["maxX"]) / 2, (sa["minY"] + sa["maxY"]) / 2)
        cb = (sum(x for x, _ in st["spawnB"]) / len(st["spawnB"]), sum(y for _, y in st["spawnB"]) / len(st["spawnB"]))
        centre = {0: ca, 1: cb}
        hist = defaultdict(list)    # (team, champ) -> [(round, pos)]
        for l in fh:
            r = json.loads(l)
            if r.get("kind") != "turn": continue
            key = (r["team"], r["champ"]); s = r["state"]["self"] or {}; rd = r["round"]
            is_alive = s.get("alive", True) and s.get("x") is not None
            if key in alive and alive[key] and not is_alive:
                deaths.append((rd, key, pos[key]))
            alive[key] = is_alive
            if is_alive:
                pos[key] = (s["x"], s["y"]); typ[key] = s.get("type")
                hist[key].append((rd, pos[key]))
            for a in r["actions"]:
                if a["k"] in (5, 6):
                    events.append((rd, r["team"], r["champ"], a["k"], tuple(a["to"]) if "to" in a else None, pos.get(key)))
    import bisect
    rounds_of = {k: [x[0] for x in h] for k, h in hist.items()}
    def near(h, rd, p, _cache={}):
        rs = [x[0] for x in h] if id(h) not in _cache else _cache[id(h)]
        _cache[id(h)] = rs
        i = bisect.bisect_left(rs, rd - 8); j = bisect.bisect_right(rs, rd)
        return any(cheb(q, p) <= 6 for _, q in h[i:j])
    out = []
    for rd, key, p in deaths:
        team = key[0]
        win = [e for e in events if rd - 8 <= e[0] <= rd]
        # victim tiles over the window
        tiles = {q for (r2, q) in hist[key] if rd - 8 <= r2 <= rd}
        attackers = {e[2] for e in win if e[1] != team and e[3] == 5 and e[4] in tiles}
        enemy_casts = sum(1 for e in win if e[1] != team and e[3] == 6 and e[5] and cheb(e[5], p) <= 6)
        own_casts = sum(1 for e in win if e[1] == team and e[3] == 6 and e[5] and cheb(e[5], p) <= 6)
        helpers = {k for k, h in hist.items() if k[0] == team and k != key and near(h, rd, p)}
        enemies_near = {k for k, h in hist.items() if k[0] != team and near(h, rd, p)}
        dm = (p[0] - centre[team][0]) ** 2 + (p[1] - centre[team][1]) ** 2
        de = (p[0] - centre[1 - team][0]) ** 2 + (p[1] - centre[1 - team][1]) ** 2
        nm = NAMES[typ[key]] if typ.get(key) is not None and typ[key] < 13 else "?"
        mode = None
        if team == ours:
            ms = [m for r2, m in modes.get(nm, []) if r2 <= rd]
            mode = ms[-1] if ms else None
        out.append(dict(map=v["map"], opp=opp, result=res, ours=team == ours, round=rd, champ=nm, attackers=len(attackers),
                        helpers=len(helpers), enemies=len(enemies_near), enemy_casts=enemy_casts, own_casts=own_casts,
                        home_half=dm < de, mode=mode))
    return out


if __name__ == "__main__":
    files = sorted(glob.glob(f"{D}/ladder/*.jsonl.gz"))
    rows = []
    with ProcessPoolExecutor(max_workers=int(os.environ.get("BC_JOBS", 8))) as ex:
        for r in ex.map(one, files): rows.extend(r)
    json.dump(rows, open(sys.argv[2], "w"))
    print(len(rows), "deaths from", len(files), "games")
