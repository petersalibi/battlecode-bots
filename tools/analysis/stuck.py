"""Stuck windows in a dumped game: per body, runs of 20+ alive turns off spawn where the body
stayed within 2 tiles. Prints round range, tiles, action kinds, mode (from our stdout), hp, enemies."""
import gzip, json, sys, zipfile, re, bisect, collections
D, mid, side = sys.argv[1], sys.argv[2], sys.argv[3]
ours = 0 if side == "a" else 1
NAMES = ["RYTH","VESPER_VALE","VENDETTA","CASSI","ISSAC","STELLAR","PRISM","MARCELLA","WILLOW","REN","VARG","TAVI","HEART_OF_RUIN"]
out = zipfile.ZipFile(f"{D}/raw/{mid}.zip").read(f"team-{side}.stdout").decode("utf8", "replace").replace("\0", "")
lines = out.splitlines()
print("stdout lines", len(lines), "tracebacks", sum("Traceback" in l for l in lines), "stuck lines", sum("stuck" in l for l in lines))
print("fuel/exhaust lines:", [l[:120] for l in lines if "exhaust" in l.lower() or "suspend" in l.lower()][:5])
modes = collections.defaultdict(list)
for l in lines:
    m = re.match(r"^r(\d+) ([A-Z_]+): (\w+) hp=.*?(src=\S+)?$", l)
    if m: modes[m.group(2)].append((int(m.group(1)), m.group(3), m.group(4)))
rounds = {k: [r for r, _, _ in v] for k, v in modes.items()}
turns = collections.defaultdict(list)
with gzip.open(f"{D}/ladder/{mid}.jsonl.gz", "rt", encoding="utf8") as fh:
    st = json.loads(next(fh))
    sp = st["spawnA"] if ours == 0 else st["spawnB"]
    spawn = set((x, y) for x in range(sp["minX"], sp["maxX"] + 1) for y in range(sp["minY"], sp["maxY"] + 1)) if isinstance(sp, dict) else set(map(tuple, sp))
    for l in fh:
        r = json.loads(l)
        if r.get("kind") == "turn" and r["team"] == ours: turns[r["champ"]].append(r)
    print("map", st["width"], "x", st["height"], "rounds", max(r["round"] for ts in turns.values() for r in ts))
for c, ts in sorted(turns.items()):
    s0 = next((r["state"]["self"] for r in ts if r["state"]["self"]), {})
    nm = NAMES[s0.get("type", 0)]
    i = 0; n = len(ts)
    while i < n:
        s = ts[i]["state"]["self"] or {}
        if not (s.get("alive", True) and s.get("x") is not None) or (s["x"], s["y"]) in spawn:
            i += 1; continue
        tiles = {(s["x"], s["y"])}; j = i
        while j + 1 < n:
            s2 = ts[j + 1]["state"]["self"] or {}
            if not (s2.get("alive", True) and s2.get("x") is not None): break
            t2 = (s2["x"], s2["y"])
            if t2 not in tiles and len(tiles) >= 2: break
            tiles.add(t2); j += 1
        if j - i + 1 >= 20:
            win = ts[i:j + 1]
            kinds = collections.Counter(x["k"] for r in win for x in r["actions"])
            en = collections.Counter(len([ch for ch in r["state"]["champs"] if ch["team"] != ours]) for r in win)
            hp = [round(r["state"]["self"]["hp"] / max(1, r["state"]["self"]["max"]), 2) for r in win[::max(1, len(win) // 4)]]
            rs = rounds.get(nm, []); k = bisect.bisect_right(rs, win[0]["round"]) - 1
            ms = collections.Counter()
            for r in win:
                k = bisect.bisect_right(rs, r["round"]) - 1
                ms[(modes[nm][k][1], modes[nm][k][2]) if k >= 0 else "?"] += 1
            print(f"{nm:12s} r{win[0]['round']}-{win[-1]['round']} ({len(win)} turns) tiles {sorted(tiles)} actions {dict(kinds)} enemies-seen {dict(en)} hp {hp} modes {dict(ms)}")
        i = j + 1
