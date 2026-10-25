"""Classify the ten-turn stalls of our bodies in harness games: what surrounded each body.

python tools/analysis/stalls.py <dir>

Layout: <dir>/raw/<map>-<A|B>.zip (match zips copied from replays/batch/<sweep>/) and
<dir>/ladder/<map>-<A|B>.jsonl.gz (tools/replay_dump.py <dir>/raw -o <dir>/ladder). Reads the
"stuck N turns" lines from our stdout and, at that round, the body's true position against the
target group's living camps, free neighbours, holes and allies. Written 2026-10-22; the classes it
found are in docs/NEXT_STEPS.md section 4a.
"""
import gzip, json, sys, os, glob, re, zipfile, collections
S = sys.argv[1]
NAMES = ["RYTH","VESPER_VALE","VENDETTA","CASSI","ISSAC","STELLAR","PRISM","MARCELLA","WILLOW","REN","VARG","TAVI","HEART_OF_RUIN"]
RANGED = {"VESPER_VALE", "REN", "HEART_OF_RUIN"}
def cheb(a, b): return max(abs(a[0]-b[0]), abs(a[1]-b[1]))
classes = collections.Counter(); examples = collections.defaultdict(list); per_map = collections.defaultdict(collections.Counter)
for zp in sorted(glob.glob(os.path.join(S, "raw", "*.zip"))):
    name = os.path.basename(zp)[:-4]; side = name[-1]; team = 0 if side == "A" else 1
    s = zipfile.ZipFile(zp).read("team-%s.stdout" % side.lower()).decode("utf8", "replace").replace("\x00", "")
    stalls = [(int(r), k, m, int(x), int(y), int(t)) for r, k, m, x, y, t in re.findall(r"r(\d+) (\w+): stuck \d+ turns in (\w+) at (\d+),(\d+) gems=\d+ target=(-?\d+)", s)]
    want = {(r, k): (x, y, t, m) for r, k, m, x, y, t in stalls}
    with gzip.open(os.path.join(S, "ladder", name + ".jsonl.gz"), "rt", encoding="utf8") as f:
        st = json.loads(next(f)); n = st["champsPerTeam"]
        walls = set(map(tuple, st["walls"])); holes = set(map(tuple, st["holes"])); altars = set(map(tuple, st["altars"]))
        camp_pos = {c["index"]: (c["x"], c["y"]) for c in st["camps"]}
        group_camps = {g["index"]: g["camps"] for g in st["groups"]}
        for line in f:
            r = json.loads(line)
            if r.get("kind") != "turn" or r["team"] != team: continue
            me = r["state"]["self"]
            if not me["alive"]: continue
            kind = NAMES[me["type"]]
            key = (r["round"], kind)
            if key not in want: continue
            x, y, tgt, mode = want[key]
            pos = (me["x"], me["y"])
            hp = r.get("campHp") or {}
            alive_tiles = {tuple(int(v) for v in k.split(",")) for k, v in hp.items() if v > 0}
            tgt_tiles = [camp_pos[i] for i in group_camps.get(tgt, [])]
            tgt_alive = [t for t in tgt_tiles if t in alive_tiles]
            rng = 3 if kind in RANGED else 1
            d_alive = min((cheb(pos, t) for t in tgt_alive), default=99)
            d_any = min((cheb(pos, t) for t in tgt_tiles), default=99)
            occupied = {(c["x"], c["y"]) for c in r["state"]["champs"] if c.get("x") is not None and c.get("hp", 0) > 0 and c["i"] != r["champ"]}
            free = 0; adj_hole = False
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if not (dx or dy): continue
                    t = (pos[0]+dx, pos[1]+dy)
                    if not (0 <= t[0] < st["width"] and 0 <= t[1] < st["height"]): continue
                    if t in holes: adj_hole = True
                    if t in walls or t in holes or t in altars or t in alive_tiles or t in occupied: continue
                    free += 1
            adj_allies = sum(1 for c in r["state"]["champs"] if c.get("x") is not None and c["team"] == team and c["i"] != r["champ"] and cheb((c["x"], c["y"]), pos) <= 1)
            gstate = r["state"]["groups"][tgt] if 0 <= tgt < len(r["state"]["groups"]) else None
            if tgt < 0: cls = "no target"
            elif not tgt_alive: cls = "target group has no living camp"
            elif d_alive <= rng: cls = "living target camp in attack range, no attack"
            elif free == 0: cls = "camp out of reach, every neighbour blocked"
            elif adj_hole: cls = "camp out of reach, hole beside us, free tiles exist"
            else: cls = "camp out of reach, free tiles exist"
            classes[cls] += 1; per_map[name[:-2]][cls] += 1
            if len(examples[cls]) < 5:
                examples[cls].append("%s r%d %s at %s tgt g%d alive_d=%s any_d=%s free=%d allies_adj=%d state=%s nalive=%d/%d" % (name, r["round"], kind, pos, tgt, d_alive, d_any, free, adj_allies, gstate, len(tgt_alive), len(tgt_tiles)))
total = sum(classes.values())
print("classified", total)
for c, v in classes.most_common():
    print("%3d (%2d%%) %s" % (v, 100*v/total, c))
    for e in examples[c]: print("      ", e)
for m, c in per_map.items(): print(m, dict(c))
