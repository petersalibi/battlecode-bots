"""Escape head training rows from replay dumps: every turn a body fled with an enemy close, the
state vector, the candidate tiles (stand still plus the walkable neighbours), the tile it stepped
to, and whether it was still alive SURVIVE rounds later.

    python tools/replay_escape_dataset.py data/replays/1.0.1/ladder --index data/replays/1.0.1/raw/index.json \
        -o data/replays/1.0.1/escapes.npz

A row is a turn whose inferred mode (tools/replay_features.py) is flee, with an enemy within
FLEE_ENEMY_RANGE, where the body stood still or stepped to a neighbouring tile (dashes and
teleports are skipped: the head scores steps). Features come from the bot's own
escape_features.py over the offline world. The label is survival: 1 when the body was alive on
every one of its turns in the next SURVIVE rounds, 0 when it died.

Output (npz): S [N, N_STATE] uint8, C [N, N_CAND] uint8 (the chosen tile), CA [N, K, N_CAND]
uint8 (every candidate, zero padded), M [N, K] bool, chosen [N] int8 (index into K), y [N] int8
(survived), meta [N, 4] int32 (round, slot, team, n_candidates), match_id, rating, won.
"""
import argparse
import gzip
import json
import os
import sys
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from progress import Progress  # noqa: E402
import replay_features as rf  # noqa: E402

import config  # noqa: E402
import escape_features  # noqa: E402

K = 9
SURVIVE = 20


class _Board:
    __slots__ = ("tiles",)

    def __init__(self, tiles):
        self.tiles = tiles

    def is_set(self, tile):
        return (tile.x, tile.y) in self.tiles


class _Field:
    __slots__ = ("values", "width")

    def __init__(self, values, width):
        self.values, self.width = values, width

    def get(self, tile):
        if self.values is None or tile.x < 0 or tile.y < 0:
            return rf.UNREACHABLE
        i = tile.y * self.width + tile.x
        return self.values[i] if i < len(self.values) else rf.UNREACHABLE


def adapt(w, st, me):
    """Give an OfflineWorld the attributes escape_features reads from the bot's World."""
    if getattr(st, "_escape_static", None) is None:
        blocked = st.blocked | set(st.camp_at)
        st._escape_static = (_Board(blocked), _Board(st.bushes), [_Board(t or set()) for t in st.spawn_tiles],
                             _Field(st.altar_field, st.width), [_Field(f, st.width) for f in st.spawn_fields])
    blocked, bushes, spawns, altar, spawn_fields = st._escape_static
    w.static_blocked, w.bushes = blocked, bushes
    w.spawn_ally = spawns[w.team]
    w.spawn_field = spawn_fields[w.team]
    w.altar_field_or_none = lambda: altar
    w.make_tile = rf.Vec
    w.cooldowns = list(me.get("cd") or [0, 0])
    w.can_cast = True
    _ranged, kit = rf.KITS.get(w.kind, (False, []))
    w.abilities = [types.SimpleNamespace(slot=i, is_dash=(k == "dash")) for i, (k, _m) in enumerate(kit)]

    def walkable_neighbours(tile):
        n = 0
        for dx, dy in escape_features.NEIGHBOURS:
            x, y = tile.x + dx, tile.y + dy
            if 0 <= x < st.width and 0 <= y < st.height and (x, y) not in blocked.tiles:
                n += 1
        return n
    w.walkable_neighbours = walkable_neighbours
    return w


def process(path):
    rows = [json.loads(l) for l in gzip.open(path, "rt", encoding="utf-8")]
    static_rec = rows[0]
    end = rows[-1] if rows[-1].get("kind") == "end" else {}
    turns = [r for r in rows if r.get("kind") == "turn"]
    if not turns:
        return None
    old_rules = static_rec["groups"][0]["gems"] > 10 if static_rec["groups"] else False
    st = rf.Static(static_rec, old_rules)
    st.finish(end.get("spawnB") or [])
    n = static_rec["champsPerTeam"]
    sizes = (n, n)
    # Per body: rounds alive, for the survival label.
    alive_rounds = {}
    for t in turns:
        s = t["state"]["self"]
        alive = bool(s) and s.get("x") is not None and s.get("alive", True) and (s.get("resp") or 0) == 0
        alive_rounds.setdefault((t["team"], t["champ"]), []).append((t["round"], alive))
    trackers, team_modes = {}, [{}, {}]
    S, C, CA, M, chosen, y, meta = [], [], [], [], [], [], []
    for t in turns:
        key = (t["team"], t["champ"])
        tr = trackers.setdefault(key, rf.Tracker())
        s = t["state"]
        me = s["self"]
        alive = bool(me) and me.get("x") is not None and me.get("alive", True) and (me.get("resp") or 0) == 0
        if not alive:
            tr.prev_hp = None
            team_modes[t["team"]][t["champ"]] = "dead"
            continue
        st.group_fields = st.group_fields_by_team[t["team"]]
        w = rf.OfflineWorld(st, t, tr, sizes, tr.decider.mode)
        w.set_claims(team_modes[t["team"]], t["champ"])
        d = tr.decider
        label = rf.infer_mode(w, t, st, d.mode)
        d._set_mode(label, "replay")
        team_modes[t["team"]][t["champ"]] = label
        tr.prev_hp, tr.prev_gems = w.hp, w.gems
        if w.nearest_enemy is None or w.nearest_enemy.dist > config.FLEE_ENEMY_RANGE:
            continue
        kinds = [a["k"] for a in t["actions"]]
        if rf.K_CAST in kinds:
            continue                      # a dash or other cast moved us: not a plain step
        moves = [a for a in t["actions"] if a["k"] == rf.K_MOVE and "to" in a]
        # Flee steps, plus turns where the body stood still without attacking (the "stay"
        # candidate needs outcomes too, or the head cannot score it).
        stood = not moves and rf.K_ATTACK not in kinds
        if label != config.FLEE and not stood:
            continue
        dest = rf.Vec(*moves[0]["to"]) if moves else w.pos
        if dest.chebyshev_distance(w.pos) > 1:
            continue
        adapt(w, st, me)
        cands = escape_features.candidates(w)
        index = next((i for i, (tile, stay) in enumerate(cands) if tile.x == dest.x and tile.y == dest.y), None)
        if index is None:
            continue
        state, crows = escape_features.build(w, d, cands)
        seq = alive_rounds[key]
        j = next(i for i, (r, ok) in enumerate(seq) if r == t["round"])
        survived = 1
        for r, ok in seq[j + 1:]:
            if r > t["round"] + SURVIVE:
                break
            if not ok:
                survived = 0
                break
        ca = np.zeros((K, escape_features.N_CAND), dtype=np.uint8)
        m = np.zeros(K, dtype=np.bool_)
        for i, row in enumerate(crows[:K]):
            ca[i] = row
            m[i] = True
        S.append(np.array(state, dtype=np.uint8)); C.append(np.array(crows[index], dtype=np.uint8))
        CA.append(ca); M.append(m); chosen.append(index); y.append(survived)
        meta.append((t["round"], w.slot, t["team"], len(cands)))
    if not S:
        return None
    return dict(S=np.stack(S), C=np.stack(C), CA=np.stack(CA), M=np.stack(M), chosen=np.array(chosen, dtype=np.int8),
                y=np.array(y, dtype=np.int8), meta=np.array(meta, dtype=np.int32))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dumps", nargs="+")
    ap.add_argument("-o", "--out", default=os.path.join(rf.ROOT, "data", "replay_escapes.npz"))
    ap.add_argument("--index")
    ap.add_argument("--jobs", type=int, default=int(os.environ.get("BC_JOBS", 8)), help="parallel workers (default: the BC_JOBS environment variable, else 8)")
    args = ap.parse_args()
    index = json.load(open(args.index, encoding="utf-8")) if args.index else {}
    dumps = []
    for d in args.dumps:
        dumps += [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".jsonl.gz")] if os.path.isdir(d) else [d]
    bar = Progress(len(dumps), "escape rows")
    results = []
    if args.jobs > 1 and len(dumps) > 1:
        from multiprocessing import Pool
        with Pool(args.jobs) as pool:
            for out in pool.imap(process, dumps):
                results.append(out); bar.step()
    else:
        for p in dumps:
            results.append(process(p)); bar.step()
    bar.done()
    parts, side_info = [], []
    for p, out in zip(dumps, results):
        if out is None:
            continue
        parts.append(out)
        entry = index.get(os.path.basename(p).split(".")[0], {})
        by_side = {t.get("side"): t for t in entry.get("teams", [])}
        side_info.append([(float(by_side.get(s, {}).get("rating") or 0.0), 1.0 if by_side.get(s, {}).get("result") == "win" else 0.0)
                          for s in ("a", "b")])
    if not parts:
        print("no rows"); return
    cat = lambda k: np.concatenate([p[k] for p in parts])
    S, C, CA, M, chosen, y, meta = (cat(k) for k in ("S", "C", "CA", "M", "chosen", "y", "meta"))
    match_id = np.concatenate([np.full(len(p["S"]), i, dtype=np.int32) for i, p in enumerate(parts)])
    rating = np.concatenate([np.array([side_info[i][int(team)][0] for team in p["meta"][:, 2]], dtype=np.float32) for i, p in enumerate(parts)])
    won = np.concatenate([np.array([side_info[i][int(team)][1] for team in p["meta"][:, 2]], dtype=np.float32) for i, p in enumerate(parts)])
    np.savez_compressed(args.out, S=S, C=C, CA=CA, M=M, chosen=chosen, y=y, meta=meta, match_id=match_id, rating=rating, won=won)
    print(f"{len(S)} flee rows from {len(parts)} games -> {args.out}; survived {100 * y.mean():.0f}%; "
          f"stood still {100 * (chosen == 0).mean():.0f}%; winners {100 * won.mean():.0f}%")


if __name__ == "__main__":
    main()
