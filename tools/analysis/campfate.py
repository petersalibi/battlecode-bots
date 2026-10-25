"""For each camp group engagement (a team starts hitting a living group), what happened: cleared?
captured by whom? Plus hits spent, gems and kind. Both sides of challenge games."""
import json, gzip, glob, os, sys
from collections import defaultdict, Counter
from concurrent.futures import ProcessPoolExecutor
US = "Teto Territory"
KIND = {0: "wolf", 1: "elk", 2: "gem", 3: "gold", 4: "monolith", 5: "golem"}
def one(path):
    d = os.path.dirname(os.path.dirname(path)); mid = os.path.basename(path).split(".")[0]
    v = json.load(open(os.path.join(d, "raw", "index.json"), encoding="utf-8")).get(mid)
    if not v: return []
    ours = next((0 if t["side"] == "a" else 1 for t in v["teams"] if t["name"] == US), None)
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        stc = json.loads(next(fh))
        group_of = {(c["x"], c["y"]): c["group"] for c in stc["camps"]}
        tiles = defaultdict(list)
        for c in stc["camps"]: tiles[c["group"]].append((c["x"], c["y"]))
        kinds = {g: "+".join(sorted({KIND.get(c["type"], "?") for c in stc["camps"] if c["group"] == g})) for g in tiles}
        gems = {g["index"]: g["gems"] for g in stc["groups"]}
        eng = {}         # (team, group) -> dict(start, hits)
        out = []
        prev_gems = {}
        cleared_at = {}  # group -> round all camps reached 0
        for l in fh:
            r = json.loads(l)
            if r.get("kind") != "turn": continue
            rd, t, c = r["round"], r["team"], r["champ"]
            s = r["state"]["self"] or {}
            hp = r["campHp"]
            for g, ts in tiles.items():
                dead = all(hp.get(f"{x},{y}", 0) <= 0 for x, y in ts)
                if dead and g not in cleared_at: cleared_at[g] = rd
                if not dead and g in cleared_at:
                    # respawned: close any open engagements without a capture
                    for tt in (0, 1):
                        e = eng.pop((tt, g), None)
                        if e: out.append(dict(side="us" if tt == ours else "them", kind=kinds[g], gems=gems.get(g, 0), hits=e["hits"], cleared=e.get("cleared", False), outcome="respawned"))
                    del cleared_at[g]
            for x in r["actions"]:
                if x["k"] == 5 and "to" in x and tuple(x["to"]) in group_of:
                    g = group_of[tuple(x["to"])]
                    e = eng.setdefault((t, g), dict(start=rd, hits=0))
                    e["hits"] += 1
            # capture: gems jump while standing on a cleared group's tiles
            ok = s.get("alive", True) and s.get("x") is not None
            gm = s.get("gems") or 0
            pg = prev_gems.get((t, c))
            if ok and pg is not None and gm > pg:
                me = (s["x"], s["y"])
                for g, ts in tiles.items():
                    if g in cleared_at and any(max(abs(me[0]-a), abs(me[1]-b)) <= 1 for a, b in ts):
                        for tt in (0, 1):
                            e = eng.pop((tt, g), None)
                            if e: out.append(dict(side="us" if tt == ours else "them", kind=kinds[g], gems=gems.get(g, 0), hits=e["hits"], cleared=True,
                                                  outcome="captured" if tt == t else "stolen"))
                        break
            prev_gems[(t, c)] = gm if ok else None
        for (tt, g), e in eng.items():
            out.append(dict(side="us" if tt == ours else "them", kind=kinds[g], gems=gems.get(g, 0), hits=e["hits"], cleared=g in cleared_at, outcome="open at end"))
    return out
if __name__ == "__main__":
    files = []
    for d in sys.argv[2:]: files += glob.glob(os.path.join(d, "ladder", "*.jsonl.gz"))
    rows = []
    with ProcessPoolExecutor(max_workers=int(os.environ.get("BC_JOBS", 16))) as ex:
        for r in ex.map(one, files, chunksize=2): rows.extend(r)
    json.dump(rows, open(sys.argv[1], "w")); print(len(rows), "engagements", file=sys.stderr)
