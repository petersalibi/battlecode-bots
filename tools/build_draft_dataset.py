"""Draft head training rows: for every pick of every collected draft, the map's features (from a
replay dump's static record, per map and side), the picks so far, the pick index, and the pick.

    python tools/build_draft_dataset.py data/drafts/drafts.json --dumps data/replays/1.0.1/ladder25 \
        data/replays/1.0.1/ladder -o data/drafts/drafts.npz
    python tools/build_draft_dataset.py --check <our stdout with draftlog lines> --dumps <dump of that game>

Map features come from the bot's own draft_features.py over a MapInfo built here from the
dump (one dump per map and side is enough; the first found is used). Drafts on maps without a
dump are skipped and listed. `--check` compares the map part logged by our draft
(`draftlog` lines in a game's stdout) with the offline MapInfo of the same game's dump.

Output (npz): X [N, N_FEATURES] uint8, y [N] int8 (ChampionDraft 0-11), mask [N, 12] bool (legal
picks: not yet picked), won [N] float32, engine [N] (strings), rating [N] float32, meta [N, 2]
(pick index, team size), maps [N] (strings).
"""
import argparse
import gzip
import json
import os
import re
import sys
from collections import deque

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import replay_features as rf  # noqa: E402  (engine stubs, bot modules on the path)

import draft_features  # noqa: E402

NEIGHBOURS = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]


def bfs(width, height, blocked, roots, limit=None):
    dist = {}
    q = deque()
    for r in roots:
        if r not in blocked:
            dist[r] = 0
            q.append(r)
    while q:
        x, y = q.popleft()
        d = dist[(x, y)]
        if limit is not None and d >= limit:
            continue
        for dx, dy in NEIGHBOURS:
            n = (x + dx, y + dy)
            if 0 <= n[0] < width and 0 <= n[1] < height and n not in blocked and n not in dist:
                dist[n] = d + 1
                q.append(n)
    return dist


def thin_holes(width, height, holes, walk):
    out = set()
    for (x, y) in holes:
        for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
            if (x + dx, y + dy) in walk and (x - dx, y - dy) in walk:
                out.add((x, y))
                break
    return out


def map_info(static, side):
    """MapInfo from a dump's static record, from the given side's spawn."""
    W, H = static["width"], static["height"]
    walls = {tuple(t) for t in static["walls"]}
    holes = {tuple(t) for t in static["holes"]}
    bushes = {tuple(t) for t in static["bushes"]}
    altars = {tuple(t) for t in static["altars"]}
    camps = {(c["x"], c["y"]): c for c in static["camps"]}
    a = static["spawnA"]
    spawn_a = {(x, y) for x in range(a["minX"], a["maxX"] + 1) for y in range(a["minY"], a["maxY"] + 1)}
    spawn_b = {tuple(t) for t in static["spawnB"]}
    ours, theirs = (spawn_a, spawn_b) if side == "a" else (spawn_b, spawn_a)
    info = draft_features.MapInfo()
    info.width, info.height = W, H
    info.team_size = static["champsPerTeam"]
    info.n_groups = len(static["groups"])
    for c in static["camps"]:
        if 0 <= c["type"] < draft_features.N_KINDS:
            info.kind_counts[c["type"]] += 1
    info.holes, info.walls, info.bushes = len(holes), len(walls), len(bushes)
    info.total_gems = sum(g["gems"] for g in static["groups"])
    info.total_gold = sum(g["gold"] for g in static["groups"])
    blocked = walls | holes | altars | set(camps) | theirs
    walk = {(x, y) for x in range(W) for y in range(H)} - blocked
    field = bfs(W, H, blocked, ours, 61)

    firsts = {}
    for c in static["camps"]:
        firsts.setdefault(c["group"], (c["x"], c["y"]))

    def camps_within(dist, limit):
        # Groups whose first camp has a cardinal neighbour within `limit`, as the draft counts them.
        n = 0
        for (x, y) in firsts.values():
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                if dist.get((x + dx, y + dy), 10 ** 9) <= limit:
                    n += 1
                    break
        return n
    info.camps_within_30 = camps_within(field, 30)
    info.camps_within_60 = camps_within(field, 60)
    thin = thin_holes(W, H, holes, walk)
    field_dash = bfs(W, H, blocked - thin, ours, 61) if thin else field
    info.camps_within_60_dash = camps_within(field_dash, 60)
    # Spawn to spawn and spawn to the nearest altar, walking (enemy spawn and altars passable as goals).
    full = bfs(W, H, walls | holes | altars | set(camps), ours)
    info.spawn_turns = min((full.get(t, 10 ** 9) for n in theirs for t in [(n[0] + dx, n[1] + dy) for dx, dy in NEIGHBOURS + [(0, 0)]]), default=0)
    info.spawn_turns = 0 if info.spawn_turns >= 10 ** 9 else info.spawn_turns
    info.altar_turns = min((full.get((ax + dx, ay + dy), 10 ** 9) for (ax, ay) in altars for dx, dy in NEIGHBOURS), default=0)
    info.altar_turns = 0 if info.altar_turns >= 10 ** 9 else info.altar_turns
    return info


def map_table(dump_dirs):
    """(map name -> {side: map features}) from one dump per map; needs the dumps' index.json."""
    table = {}
    for d in dump_dirs:
        raw = os.path.join(os.path.dirname(d.rstrip("/\\")), "raw")
        idx_path = os.path.join(raw, "index.json")
        if not os.path.exists(idx_path):
            idx_path = os.path.join(d, "..", "raw25", "index.json")
        index = json.load(open(idx_path, encoding="utf-8")) if os.path.exists(idx_path) else {}
        for f in sorted(os.listdir(d)):
            if not f.endswith(".jsonl.gz"):
                continue
            mid = f.split(".")[0]
            name = index.get(mid, {}).get("map")
            if name is None or name in table:
                continue
            static = json.loads(gzip.open(os.path.join(d, f), "rt", encoding="utf-8").readline())
            table[name] = {side: draft_features.map_part(map_info(static, side)) for side in ("a", "b")}
    return table


LOG_RE = re.compile(r"^draftlog pick=(\d+) map=([0-9a-f]+)$")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("drafts", nargs="?", help="drafts.json from tools/fetch_drafts.py")
    ap.add_argument("--dumps", nargs="+", required=True, help="dump folders (with ../raw/index.json) to take map statics from")
    ap.add_argument("-o", "--out", default=os.path.join(rf.ROOT, "data", "drafts", "drafts.npz"))
    ap.add_argument("--check", help="our stdout with draftlog lines; --dumps then names that game's dump folder")
    ap.add_argument("--side", default="a", help="with --check: which side we were")
    args = ap.parse_args()
    if args.check:
        d = args.dumps[0]
        f = next(x for x in sorted(os.listdir(d)) if x.endswith(".jsonl.gz")) if os.path.isdir(d) else os.path.basename(d)
        static = json.loads(gzip.open(os.path.join(d, f) if os.path.isdir(d) else d, "rt", encoding="utf-8").readline())
        offline = draft_features.map_part(map_info(static, args.side))
        logged = None
        for line in open(args.check, encoding="utf-8", errors="replace"):
            m = LOG_RE.match(line.replace("\0", "").strip())
            if m:
                logged = list(bytes.fromhex(m.group(2)))
                break
        if logged is None:
            print("no draftlog line"); return
        diff = [abs(a - b) for a, b in zip(offline, logged)]
        print("offline:", offline); print("logged: ", logged)
        print("max abs diff", max(diff), "| features within 2:", sum(1 for x in diff if x <= 2), "of", len(diff))
        return
    rows = json.load(open(args.drafts, encoding="utf-8"))
    table = map_table(args.dumps)
    print(f"map features for {len(table)} maps")
    X, y, mask, won, engine, rating, meta, maps = [], [], [], [], [], [], [], []
    missing = set()
    for key, r in rows.items():
        feats = table.get(r["map"])
        if feats is None:
            missing.add(r["map"]); continue
        mp = feats[r["side"]]
        picks = r["picks"]
        for i, pick in enumerate(picks):
            if not 0 <= pick < draft_features.N_PICKS:
                continue
            x = draft_features.build(mp, picks[:i], i)
            m = [p not in picks[:i] for p in range(draft_features.N_PICKS)]
            X.append(x); y.append(pick); mask.append(m); won.append(1.0 if r["result"] == "win" else 0.0)
            engine.append(r.get("engine") or ""); rating.append(float(r.get("rating") or 0.0))
            meta.append((i, len(picks))); maps.append(r["map"])
    if missing:
        print("no dump for maps:", sorted(missing))
    np.savez_compressed(args.out, X=np.array(X, dtype=np.uint8), y=np.array(y, dtype=np.int8), mask=np.array(mask, dtype=np.bool_),
                        won=np.array(won, dtype=np.float32), engine=np.array(engine), rating=np.array(rating, dtype=np.float32),
                        meta=np.array(meta, dtype=np.int32), maps=np.array(maps))
    from collections import Counter
    print(f"{len(X)} pick rows -> {args.out}; engines {dict(Counter(engine))}; picks {dict(sorted(Counter(y).items()))}")


if __name__ == "__main__":
    main()
