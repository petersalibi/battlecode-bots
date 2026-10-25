"""Turn harness runs with datalog on into a training set for the mode selector.

    python tools/build_dataset.py replays/batch/<run> [more runs ...] -o data/modes.npz

Each run folder comes from `tools/run_matches.py <bot> <opponent> --debug --log-limit 16000000`
with `config.DATALOG = True` in the bot. Only the first bot's side of each match is read (the
side letter in the file name). For every logged body-turn the row holds the 66 features, the mode
mask, the script's scores and choice, and an outcome label computed from the statistics JSON:

    reward = gems this body tributed in the next HORIZON rounds
           + 0.5 * gems it collected in that window
           - DEATH_PENALTY if it died in that window

so training can weight the script's choices by how well they turned out (see
tools/train_mode_selector.py). Champions are matched between the log (kind index) and the
statistics (name) through CHAMPION_NAMES in the bot's config.
"""
import argparse
import glob
import json
import os
import re
import sys
import zipfile

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bots", "hybrid", "src"))
import config  # noqa: E402  (plain constants, no SDK)

HORIZON = 60
DEATH_PENALTY = 10.0
LINE_RE = re.compile(rb"^nnlog r=(\d+) s=(\d+) k=(\d+) m=(\d+) src=(\w+) mask=([01]{6}) sc=([-\d,]+) "
                     rb"gems=(\d+) bank=(\d+) hp=(\d+) f=([0-9a-f]+)$", re.M)
STAT_NAMES = {name.upper().replace(" ", "_"): name for name in
              ("Ryth", "Vesper Vale", "Vendetta", "Cassi", "Issac", "Stellar", "Prism", "Marcella", "Willow",
               "Ren", "Varg", "Tavi", "Heart of Ruin")}


def timeline(d, team, name, metric, n_rounds):
    """Per-round cumulative value of a champion metric from the run-length timelines."""
    out = np.zeros(n_rounds + 2, dtype=np.float32)
    for t in d["championTimelines"]:
        if t["metric"] != metric or t["owner"]["team"] != team or t["owner"]["name"] != name:
            continue
        r = 0
        for run in t["runs"]:
            value = float(run["value"])
            out[r:r + run["duration"]] = value
            r += run["duration"]
        out[r:] = out[r - 1] if r > 0 else 0
        break
    return out


def deaths_by_round(d, team, name, n_rounds):
    out = np.zeros(n_rounds + 2, dtype=np.bool_)
    for e in d["events"]:
        if e["kind"] == "death" and e["owner"].get("team") == team and e["owner"].get("name") == name:
            for x in e["entries"]:
                out[min(x["timestamp"]["round"], n_rounds + 1)] = True
    return out


def read_match(json_path):
    name = os.path.basename(json_path)[:-5]
    side = name[-1]
    team = "teamA" if side == "A" else "teamB"
    d = json.load(open(json_path, encoding="utf-8"))
    n_rounds = d["match"]["roundsCompleted"]
    try:
        out = zipfile.ZipFile(json_path[:-5] + ".repl26a").read(f"team-{side.lower()}.stdout").replace(b"\x00", b"")
    except (OSError, KeyError):
        return None
    rows = LINE_RE.findall(out)
    if not rows:
        return None
    X, mask, scores, y, reward, meta = [], [], [], [], [], []
    cache = {}
    for r, s, k, m, src, mbits, sc, gems, bank, hp, hexf in rows:
        r, k, m = int(r), int(k), int(m)
        kind_name = config.CHAMPION_NAMES[k] if k < len(config.CHAMPION_NAMES) else None
        stat_name = STAT_NAMES.get(kind_name)
        if stat_name not in cache:
            cache[stat_name] = (timeline(d, team, stat_name, "submittedGems", n_rounds),
                                timeline(d, team, stat_name, "collectedGems", n_rounds),
                                deaths_by_round(d, team, stat_name, n_rounds)) if stat_name else None
        tl = cache[stat_name]
        if tl is None:
            continue
        sub, col, died = tl
        end = min(r + HORIZON, n_rounds + 1)
        value = (sub[end] - sub[r]) + 0.5 * (col[end] - col[r]) - (DEATH_PENALTY if died[r + 1:end + 1].any() else 0.0)
        X.append(np.frombuffer(bytes.fromhex(hexf.decode()), dtype=np.uint8))
        mask.append([c == 49 for c in mbits])
        scores.append([int(v) / 100.0 for v in sc.decode().split(",")])
        y.append(m)
        reward.append(value)
        meta.append((r, int(s), k, int(gems), int(bank), int(hp), src == b"nn"))
    return (np.stack(X), np.array(mask, dtype=np.bool_), np.array(scores, dtype=np.float32),
            np.array(y, dtype=np.int8), np.array(reward, dtype=np.float32), np.array(meta, dtype=np.int32), name)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("runs", nargs="+", help="run_matches.py output folders")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "data", "modes.npz"))
    args = ap.parse_args()
    parts = []
    for run in args.runs:
        for js in sorted(glob.glob(os.path.join(run, "*-[AB].json"))):
            part = read_match(js)
            if part is not None:
                parts.append(part)
                print(f"{part[-1]}: {len(part[0])} rows")
    if not parts:
        sys.exit("no nnlog lines found: run the harness with --debug and config.DATALOG = True")
    X = np.concatenate([p[0] for p in parts]); mask = np.concatenate([p[1] for p in parts])
    scores = np.concatenate([p[2] for p in parts]); y = np.concatenate([p[3] for p in parts])
    reward = np.concatenate([p[4] for p in parts]); meta = np.concatenate([p[5] for p in parts])
    match_id = np.concatenate([np.full(len(p[0]), i, dtype=np.int32) for i, p in enumerate(parts)])
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    np.savez_compressed(args.out, X=X, mask=mask, scores=scores, y=y, reward=reward, meta=meta, match_id=match_id,
                        matches=np.array([p[-1] for p in parts]))
    counts = np.bincount(y, minlength=config.N_MODES)
    print(f"\n{len(X)} rows from {len(parts)} match sides, {X.shape[1]} features -> {args.out}")
    print("mode counts:", {config.MODE_NAMES[i]: int(c) for i, c in enumerate(counts)})
    print("reward: mean %.2f, std %.2f, deaths in window %.1f%%" % (
        reward.mean(), reward.std(), 100 * (reward <= -DEATH_PENALTY + 1e-6).mean()))


if __name__ == "__main__":
    main()
