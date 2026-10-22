"""Contact head training rows from replay dumps: every turn a body had an enemy champion within
CONTACT_RANGE, the contact feature vector and what the body did (attack / close / hold / retreat).

    python tools/replay_contact_dataset.py data/challenges/v36-band200 [more run dirs...] \
        --sides band -o data/contact/band-1.0.3.npz

Each run dir holds raw/index.json and ladder/*.jsonl.gz (tools/challenges.py collect). `--sides
band` keeps the turns of the team that is not ours (the +200 band: the behaviour to imitate),
`ours` ours, `both` both. Output: X [N, N_CONTACT] uint8, y [N] int8 (contact_features.ATTACK..),
meta [N, 6] (round, slot, team, won, died within 10, hp%), match [N] (run/match index), rating [N].
Features come from the bot's own contact_features.py over the offline world of replay_features.py.
"""
import argparse
import glob
import gzip
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from progress import Progress  # noqa: E402
import replay_features as rf  # noqa: E402

import config  # noqa: E402
import contact_features as cf  # noqa: E402

US = "Teto Territory"
HORIZON = 10
K_MOVE, K_ATTACK = 4, 5


def label(turn, w):
    """What the body did this turn, from the engine's own action steps."""
    enemy_tiles = {(e.pos.x, e.pos.y) for e in w.enemies}
    moved_to = None
    for a in turn["actions"]:
        if a["k"] == K_ATTACK and "to" in a and tuple(a["to"]) in enemy_tiles:
            return cf.ATTACK
        if a["k"] == K_MOVE and "to" in a:
            moved_to = tuple(a["to"])
    if moved_to is None or not w.enemies:
        return cf.HOLD
    before = w.nearest_enemy.dist
    after = min(max(abs(moved_to[0] - e.pos.x), abs(moved_to[1] - e.pos.y)) for e in w.enemies)
    if after > before:
        return cf.RETREAT
    if after < before:
        return cf.CLOSE
    return cf.HOLD


def process(path, keep_team, won, rating):
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
    alive_rounds = {}
    for t in turns:
        s = t["state"]["self"]
        alive = bool(s) and s.get("x") is not None and s.get("alive", True) and (s.get("resp") or 0) == 0
        alive_rounds.setdefault(t["champ"], []).append((t["round"], alive))

    def died_within(champ, round_number, horizon):
        for r, ok in alive_rounds.get(champ, []):
            if round_number < r <= round_number + horizon and not ok:
                return True
        return False

    trackers = {}
    X, y, meta = [], [], []
    for t in turns:
        key = (t["team"], t["champ"])
        tr = trackers.setdefault(key, rf.Tracker())
        s = t["state"]
        me = s["self"]
        alive = bool(me) and me.get("x") is not None and me.get("alive", True) and (me.get("resp") or 0) == 0
        if not alive:
            tr.prev_hp = None
            continue
        if t["team"] not in keep_team:
            tr.prev_hp = float(me["hp"])
            continue
        st.group_fields = st.group_fields_by_team[t["team"]]
        w = rf.OfflineWorld(st, t, tr, sizes, tr.decider.mode)
        tr.prev_hp, tr.prev_gems = w.hp, w.gems
        if w.nearest_enemy is None or w.nearest_enemy.dist > config.CONTACT_RANGE or w.at_spawn:
            continue
        x = cf.build(w, tr.decider)
        X.append(np.array(x, dtype=np.uint8))
        y.append(label(t, w))
        meta.append((t["round"], w.slot, t["team"], 1 if won.get(t["team"]) else 0,
                     1 if died_within(t["champ"], t["round"], HORIZON) else 0, int(w.hp_ratio * 100)))
    if not X:
        return None
    return dict(X=np.stack(X), y=np.array(y, dtype=np.int8), meta=np.array(meta, dtype=np.int32),
                rating=np.full(len(X), rating, dtype=np.float32))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("runs", nargs="+", help="run directories with raw/index.json and ladder/")
    ap.add_argument("--sides", choices=("band", "ours", "both"), default="band")
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()
    jobs = []
    for run in args.runs:
        idx = json.load(open(os.path.join(run, "raw", "index.json"), encoding="utf-8"))
        for f in sorted(glob.glob(os.path.join(run, "ladder", "*.jsonl.gz"))):
            mid = os.path.basename(f).split(".")[0]
            v = idx.get(mid)
            if not v:
                continue
            ours = next((0 if t["side"] == "a" else 1 for t in v["teams"] if t["name"] == US), None)
            if ours is None:
                continue
            keep = {1 - ours} if args.sides == "band" else {ours} if args.sides == "ours" else {0, 1}
            won = {(0 if t["side"] == "a" else 1): t.get("result") == "win" for t in v["teams"]}
            rating = max((t.get("rating") or 0) for t in v["teams"] if t["name"] != US) if any(t.get("rating") for t in v["teams"]) else 0.0
            jobs.append((f, keep, won, rating))
    parts, match = [], []
    bar = Progress(len(jobs), "contact rows")
    for i, (f, keep, won, rating) in enumerate(jobs):
        out = process(f, keep, won, rating)
        bar.step(1, os.path.basename(f)[:12])
        if out is None:
            continue
        parts.append(out)
        match.append(np.full(len(out["y"]), i, dtype=np.int32))
    bar.done()
    if not parts:
        sys.exit("no rows")
    data = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    data["match"] = np.concatenate(match)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    np.savez_compressed(args.out, **data)
    counts = np.bincount(data["y"], minlength=cf.N_ACTIONS)
    print(f"{len(data['y'])} rows from {len(parts)} games -> {args.out}; "
          + ", ".join(f"{cf.ACTION_NAMES[i]} {100 * counts[i] / len(data['y']):.0f}%" for i in range(cf.N_ACTIONS)))


if __name__ == "__main__":
    main()
