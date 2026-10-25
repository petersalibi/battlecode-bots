"""Camp scorer training rows from replay dumps: at every retarget a farming body would make, the
state vector, the candidate vectors of the camp groups in reach (as candidates.py lists them)
and the group the body actually attacked next.

    python tools/replay_camp_dataset.py data/replays/1.0.1/ladder --index data/replays/1.0.1/raw/index.json \
        --winners-only -o data/replays/1.0.1/camps.npz
    python tools/replay_camp_dataset.py replays/<game>.jsonl.gz --check replays/<game>/team-a.stdout

Rows come every RETARGET_EVERY rounds of a body that is alive, not hitting a camp this turn and
inferred to be farming (tools/replay_features.py infer_mode). The label is the first camp group the
body attacks within LOOKAHEAD rounds; rows where it attacks nothing, dies first, or attacks a group
outside our candidate list (reachable for them, not for a walker of ours) are dropped. The reward
label is the gems the body collected or tributed over HORIZON rounds minus a death penalty, like
the mode rows. Features come from the bot's own camp_features.py over the offline world of
replay_features.py; `--check` compares them with the bot's camplog of the same game
(config.DATALOG in a debug build), state feature by feature and candidate by group.

Output (npz): S [N, N_STATE] uint8, C [N, K, N_CAND] uint8 (zero padded), M [N, K] bool, y [N]
int8 (index into the K candidates), reward [N] float32, meta [N, 5] int32 (round, slot, team,
n_candidates, label group), match_id, rating, won.
"""
import argparse
import gzip
import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from progress import Progress  # noqa: E402
import replay_features as rf  # noqa: E402  (installs the engine stubs and imports the bot modules)

import camp_features  # noqa: E402
import config  # noqa: E402

K = config.MAX_CAMP_CANDIDATES
LOOKAHEAD = 60
HORIZON = 100
DEATH_PENALTY = 10.0


def attacked_group(turn, st):
    """Group of the camp this turn's attacks or casts hit, or None."""
    for a in turn["actions"]:
        if a["k"] in (rf.K_ATTACK, rf.K_CAST) and "to" in a:
            ci = st.camp_at.get(tuple(a["to"]))
            if ci is not None:
                return st.group_of_camp[ci]
    return None


def process(path, check_keys=None):
    rows = [json.loads(l) for l in gzip.open(path, "rt", encoding="utf-8")]
    static_rec = rows[0]
    end = rows[-1] if rows[-1].get("kind") == "end" else {}
    turns = [r for r in rows if r.get("kind") == "turn"]
    if not turns:
        return None
    old_rules = static_rec["groups"][0]["gems"] > 10 if static_rec["groups"] else False
    st = rf.Static(static_rec, old_rules)
    st.finish(end.get("spawnB") or [])
    n_per_team = static_rec["champsPerTeam"]
    sizes = (n_per_team, n_per_team)

    # Per body: the sequence of (round, group attacked or None, gems, alive) for labels and rewards.
    seq = {}
    for t in turns:
        key = (t["team"], t["champ"])
        s = t["state"]["self"]
        alive = bool(s) and s.get("x") is not None and s.get("alive", True) and (s.get("resp") or 0) == 0
        seq.setdefault(key, []).append((t["round"], attacked_group(t, st) if alive else None, (s or {}).get("gems") or 0, alive))

    trackers = {}
    team_modes = [{}, {}]
    team_targets = [{}, {}]       # champ -> group it is farming (last camp hit within 30 rounds)
    last_row_round = {}
    S, C, M, y, reward, meta, cand_groups = [], [], [], [], [], [], []
    for pos, t in enumerate(turns):
        key = (t["team"], t["champ"])
        tr = trackers.setdefault(key, rf.Tracker())
        s = t["state"]
        alive = bool(s["self"]) and s["self"].get("x") is not None and s["self"].get("alive", True) and (s["self"].get("resp") or 0) == 0
        if not alive:
            if tr.alive:
                tr.alive = False
                tr.decider.record_death(t["round"])
            tr.prev_hp = None
            team_modes[t["team"]][t["champ"]] = "dead"
            continue
        tr.alive = True
        st.group_fields = st.group_fields_by_team[t["team"]]
        w = rf.OfflineWorld(st, t, tr, sizes, tr.decider.mode)
        w.set_claims(team_modes[t["team"]], t["champ"], team_targets[t["team"]])
        turn = rf.OfflineTurn(w)
        d = tr.decider
        hit = attacked_group(t, st)
        if hit is not None:
            tr.last_camp_group, tr.last_camp_round = hit, t["round"]
            d.target_group = st.groups[hit]
        elif t["round"] - tr.last_camp_round > 30:
            d.target_group = None
        team_targets[t["team"]][t["champ"]] = d.target_group.index if d.target_group is not None else None
        label = rf.infer_mode(w, t, st, d.mode)
        d._set_mode(label, "replay")
        team_modes[t["team"]][t["champ"]] = label
        tr.prev_hp, tr.prev_gems = w.hp, w.gems

        slot = w.slot
        if check_keys is not None:
            wanted = (t["round"], slot) in check_keys and t["team"] == 0
        else:
            wanted = (hit is None and label == config.FARM
                      and t["round"] - last_row_round.get(key, -1000) >= config.RETARGET_EVERY)
        if not wanted:
            continue
        cands = turn.camps(fresh=True)
        if len(cands) < 2 and check_keys is None:
            continue
        for cand in cands:
            d.score_camp(cand, w)
        allowed = [cand.score > config.MIN_CAMP_SCORE for cand in cands]
        # Label: the first group attacked in the lookahead window, before any death.
        target, died_first = None, False
        items = seq[key]
        j = next(i for i, it in enumerate(items) if it[0] == t["round"])
        for r, g, _gems, ok in items[j + 1:]:
            if r > t["round"] + LOOKAHEAD:
                break
            if not ok:
                died_first = True
                break
            if g is not None:
                target = g
                break
        index = next((i for i, cand in enumerate(cands) if cand.group.index == target), None)
        if check_keys is None and (target is None or died_first or index is None or not allowed[index]):
            last_row_round[key] = t["round"]
            continue
        last_row_round[key] = t["round"]
        state, rows_c = camp_features.build(w, turn, d, cands)
        c = np.zeros((K, camp_features.N_CAND), dtype=np.uint8)
        m = np.zeros(K, dtype=np.bool_)
        for i, row in enumerate(rows_c[:K]):
            c[i] = row
            m[i] = allowed[i]
        # Reward over HORIZON rounds: tributes and collections minus a death penalty.
        tributed = collected = 0
        died = False
        prev = items[j][2]
        for r, g, gems, ok in items[j + 1:]:
            if r > t["round"] + HORIZON:
                break
            if not ok:
                died = True
                break
            dg = gems - prev
            if dg > 0:
                collected += dg
            elif dg < 0 and gems == 0:
                tributed += prev
            prev = gems
        S.append(np.array(state, dtype=np.uint8)); C.append(c); M.append(m)
        y.append(index if index is not None else -1)
        reward.append((0 if died else tributed) + 0.5 * collected - (DEATH_PENALTY if died else 0))
        script_pick = max((i for i in range(len(cands)) if allowed[i]), key=lambda i: cands[i].score, default=-1)
        meta.append((t["round"], slot, t["team"], len(cands), target if target is not None else -1, script_pick))
        cand_groups.append([cand.group.index for cand in cands[:K]])
    if not S:
        return None
    return dict(S=np.stack(S), C=np.stack(C), M=np.stack(M), y=np.array(y, dtype=np.int8),
                reward=np.array(reward, dtype=np.float32), meta=np.array(meta, dtype=np.int32),
                cand_groups=cand_groups)


LOG_RE = re.compile(r"^camplog r=(\d+) s=(\d+) pick=(-?\d+) src=\w+ g=([\d,]*) S=([0-9a-f]+) C=([0-9a-f|]*)$")


def read_log(path):
    logged = {}
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.replace("\0", "").strip()
        m = LOG_RE.match(line)
        if m:
            groups = [int(g) for g in m.group(4).split(",") if g]
            state = np.frombuffer(bytes.fromhex(m.group(5)), dtype=np.uint8)
            cands = [np.frombuffer(bytes.fromhex(h), dtype=np.uint8) for h in m.group(6).split("|") if h]
            logged[(int(m.group(1)), int(m.group(2)))] = (int(m.group(3)), groups, state, cands)
    return logged


def compare(path, log_path):
    logged = read_log(log_path)
    out = process(path, check_keys=set(logged))
    if out is None:
        print("check: no offline rows"); return
    S, C, meta = out["S"], out["C"], out["meta"]
    n_state, n_cand = camp_features.N_STATE, camp_features.N_CAND
    s_diff = np.zeros(n_state); c_diff = np.zeros(n_cand); n = nc = 0
    missing = 0
    examples = {}
    # Offline candidate group order is reconstructed from the turn's own fields; match by group.
    rows_by_key = {(int(row[0]), int(row[1])): i for i, row in enumerate(meta) if row[2] == 0}
    offline_groups = out["cand_groups"]
    for key, (pick, groups, state, cands) in logged.items():
        i = rows_by_key.get(key)
        if i is None:
            missing += 1
            continue
        n += 1
        s_diff += np.abs(S[i].astype(int) - state.astype(int))
        for fi in np.argsort(-np.abs(S[i].astype(int) - state.astype(int)))[:2]:
            if abs(int(S[i][fi]) - int(state[fi])) > 8 and len(examples.setdefault(("S", int(fi)), [])) < 3:
                examples[("S", int(fi))].append((key, int(S[i][fi]), int(state[fi])))
        off = {g: C[i][j] for j, g in enumerate(offline_groups[i])}
        for g, row in zip(groups, cands):
            if g in off:
                nc += 1
                d = np.abs(off[g].astype(int) - row.astype(int))
                c_diff += d
                for fi in np.argsort(-d)[:2]:
                    if d[fi] > 8 and len(examples.setdefault(("C", int(fi)), [])) < 3:
                        examples[("C", int(fi))].append((key, g, int(off[g][fi]), int(row[fi])))
    if n == 0:
        print("check: no matching rows"); return
    s_diff /= n; c_diff /= max(nc, 1)
    print(f"check: {n} of {len(logged)} logged retargets matched ({missing} missing offline); {nc} candidates matched by group")
    print("state mean abs error per feature:", [round(float(v), 1) for v in s_diff])
    print("candidate mean abs error per feature:", [round(float(v), 1) for v in c_diff])
    print(f"state features within 2: {100 * (s_diff <= 2).mean():.0f}%, candidate features within 2: {100 * (c_diff <= 2).mean():.0f}%")
    for (kind, fi), ex in sorted(examples.items()):
        print(f"  {kind}{fi}: " + "; ".join(str(e) for e in ex))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dumps", nargs="+")
    ap.add_argument("-o", "--out", default=os.path.join(rf.ROOT, "data", "replay_camps.npz"))
    ap.add_argument("--check", help="our bot's stdout (camplog lines) of the same game for a feature comparison")
    ap.add_argument("--index", help="fetch_replays.py index.json: adds each side's ladder rating and result")
    ap.add_argument("--jobs", type=int, default=int(os.environ.get("BC_JOBS", 8)), help="parallel workers (default: the BC_JOBS environment variable, else 8)")
    ap.add_argument("--winners-only", action="store_true")
    args = ap.parse_args()
    if args.check:
        compare(args.dumps[0], args.check)
        return
    index = json.load(open(args.index, encoding="utf-8")) if args.index else {}
    dumps = []
    for d in args.dumps:
        dumps += [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".jsonl.gz")] if os.path.isdir(d) else [d]
    bar = Progress(len(dumps), "camp rows")
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
    S = np.concatenate([p["S"] for p in parts]); C = np.concatenate([p["C"] for p in parts]); M = np.concatenate([p["M"] for p in parts])
    y = np.concatenate([p["y"] for p in parts]); reward = np.concatenate([p["reward"] for p in parts]); meta = np.concatenate([p["meta"] for p in parts])
    match_id = np.concatenate([np.full(len(p["S"]), i, dtype=np.int32) for i, p in enumerate(parts)])
    rating = np.concatenate([np.array([side_info[i][int(team)][0] for team in p["meta"][:, 2]], dtype=np.float32) for i, p in enumerate(parts)])
    won = np.concatenate([np.array([side_info[i][int(team)][1] for team in p["meta"][:, 2]], dtype=np.float32) for i, p in enumerate(parts)])
    if args.winners_only:
        keep = won > 0
        print(f"keeping the {int(keep.sum())} rows of winning sides out of {len(keep)}")
        S, C, M, y, reward, meta, match_id, rating, won = (a[keep] for a in (S, C, M, y, reward, meta, match_id, rating, won))
    np.savez_compressed(args.out, S=S, C=C, M=M, y=y, reward=reward, meta=meta, match_id=match_id, rating=rating, won=won)
    n_cands = meta[:, 3]
    print(f"{len(S)} rows from {len(parts)} games -> {args.out}; candidates per row mean {n_cands.mean():.1f}; "
          f"label is the nearest candidate in {100 * (y == 0).mean():.0f}% of rows; mean reward {reward.mean():.2f}")


if __name__ == "__main__":
    main()
