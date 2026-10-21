"""Fight head training rows from replay dumps: every turn a body attacked a champion, the state
vector, one candidate vector per visible enemy, which enemy it attacked, and how it went (the
enemy died within HORIZON rounds, or we did).

    python tools/replay_fight_dataset.py data/replays/1.0.1/ladder --index data/replays/1.0.1/raw/index.json \
        -o data/replays/1.0.1/fights.npz

Same layout as the camp rows, so tools/train_camp_scorer.py trains it with `--head FIGHT_HEAD`:
S [N, N_STATE], C [N, K, N_CAND] (zero padded), M [N, K], y [N] (index of the enemy attacked),
reward [N], meta [N, 5], match_id, rating, won. Features come from the bot's own fight_features.py
over the offline world of replay_features.py.
"""
import argparse
import gzip
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from progress import Progress  # noqa: E402
import replay_features as rf  # noqa: E402

import config  # noqa: E402
import fight_features  # noqa: E402

K = 6
HORIZON = 10
KILL_REWARD = 10.0
DEATH_PENALTY = 10.0


def hit_target(turn, enemies):
    """The enemy an attack or cast this turn was aimed at (nearest to the target tile, within 1)."""
    for a in turn["actions"]:
        if a["k"] in (rf.K_ATTACK, rf.K_CAST) and "to" in a:
            tx, ty = a["to"]
            best, best_d = None, 2
            for i, e in enumerate(enemies):
                d = max(abs(e.pos.x - tx), abs(e.pos.y - ty))
                if d < best_d:
                    best, best_d = i, d
            if best is not None:
                return best
    return None


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

    trackers, team_modes = {}, [{}, {}]
    S, C, M, y, reward, meta = [], [], [], [], [], []
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
        if label != config.FIGHT or len(w.enemies) < 2:
            continue
        enemies = sorted(w.enemies, key=lambda e: e.dist)[:K]
        target = hit_target(t, enemies)
        if target is None:
            continue
        _ranged, kit = rf.KITS.get(w.kind, (False, []))
        w.abilities = [type("A", (), {"slot": i, "is_dash": k == "dash"})() for i, (k, _m) in enumerate(kit)]
        state, crows = fight_features.build(w, d, enemies)
        c = np.zeros((K, fight_features.N_CAND), dtype=np.uint8)
        m = np.zeros(K, dtype=np.bool_)
        for i, row in enumerate(crows):
            c[i] = row
            m[i] = True
        enemy_champ = enemies[target].handle
        killed = died_within(enemy_champ, t["round"], HORIZON)
        died = died_within(t["champ"], t["round"], HORIZON)
        S.append(np.array(state, dtype=np.uint8)); C.append(c); M.append(m); y.append(target)
        reward.append(KILL_REWARD * killed - DEATH_PENALTY * died)
        meta.append((t["round"], w.slot, t["team"], len(enemies), -1))
    if not S:
        return None
    return dict(S=np.stack(S), C=np.stack(C), M=np.stack(M), y=np.array(y, dtype=np.int8),
                reward=np.array(reward, dtype=np.float32), meta=np.array(meta, dtype=np.int32))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dumps", nargs="+")
    ap.add_argument("-o", "--out", default=os.path.join(rf.ROOT, "data", "replay_fights.npz"))
    ap.add_argument("--index")
    ap.add_argument("--jobs", type=int, default=12)
    args = ap.parse_args()
    index = json.load(open(args.index, encoding="utf-8")) if args.index else {}
    dumps = []
    for d in args.dumps:
        dumps += [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".jsonl.gz")] if os.path.isdir(d) else [d]
    bar = Progress(len(dumps), "fight rows")
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
    S, C, M, y, reward, meta = (cat(k) for k in ("S", "C", "M", "y", "reward", "meta"))
    match_id = np.concatenate([np.full(len(p["S"]), i, dtype=np.int32) for i, p in enumerate(parts)])
    rating = np.concatenate([np.array([side_info[i][int(team)][0] for team in p["meta"][:, 2]], dtype=np.float32) for i, p in enumerate(parts)])
    won = np.concatenate([np.array([side_info[i][int(team)][1] for team in p["meta"][:, 2]], dtype=np.float32) for i, p in enumerate(parts)])
    np.savez_compressed(args.out, S=S, C=C, M=M, y=y, reward=reward, meta=meta, match_id=match_id, rating=rating, won=won)
    print(f"{len(S)} fight rows from {len(parts)} games -> {args.out}; attacked the nearest in {100 * (y == 0).mean():.0f}%; "
          f"kills {100 * (reward > 0).mean():.0f}%, deaths {100 * (reward < 0).mean():.0f}%; winners {100 * won.mean():.0f}%")


if __name__ == "__main__":
    main()
