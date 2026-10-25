"""Self-play for the draft head: play exploring drafts against a fixed opponent, label every
draft with our own gem margin, and train the head on our drafts weighted by how they did.

    python tools/selfplay_drafts.py --opponent hybrid@hybrid-v11 --sweeps 15 --explore 1.0
    python tools/selfplay_drafts.py --collect-only   # rebuild the rows from the sweeps already played

Each sweep plays every map from both sides (tools/run_matches.py) with config.DRAFT_RANDOM set to
--explore and a fresh DRAFT_RANDOM_SALT, so the same map draws different compositions each time;
with --explore below 1 the rest of the picks come from the draft head (if on) or the priority
list. The bot's config is restored afterwards. Rows go to data/drafts/selfplay.npz in the draft
dataset's layout (won = our margin was positive; rating = our gem margin, which the trainer can
weight by), and tools/train_draft_head.py trains on them.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_draft_dataset as bdd  # noqa: E402  (engine stubs, bot modules on the path)
import draft_features  # noqa: E402

ROOT = bdd.rf.ROOT
PY = sys.executable
CONFIG = os.path.join(ROOT, "bots", "hybrid", "src", "config.py")
OUT_ROOT = os.path.join(ROOT, "replays", "selfplay")
DRAFT_OF = {"Ryth": 0, "Vesper Vale": 1, "Vendetta": 2, "Cassi": 3, "Issac": 3, "Stellar": 4, "Prism": 5, "Marcella": 6,
            "Willow": 7, "Ren": 8, "Varg": 9, "Tavi": 10, "Heart of Ruin": 11}


def set_config(explore, salt):
    s = open(CONFIG, encoding="utf-8").read()
    s = re.sub(r"^DRAFT_RANDOM = [0-9.]+", "DRAFT_RANDOM = %s" % explore, s, count=1, flags=re.M)
    s = re.sub(r"^DRAFT_RANDOM_SALT = \d+", "DRAFT_RANDOM_SALT = %d" % salt, s, count=1, flags=re.M)
    open(CONFIG, "w", encoding="utf-8", newline="\n").write(s)


def picks_of(roster):
    picks = []
    for name in roster:
        p = DRAFT_OF.get(name)
        if p is not None and p not in picks:
            picks.append(p)
    return picks


def collect(table):
    X, y, mask, won, margin, meta, maps = [], [], [], [], [], [], []
    games = 0
    for results in sorted(glob.glob(os.path.join(OUT_ROOT, "*", "results.json"))):
        for m in json.load(open(results, encoding="utf-8"))["matches"]:
            if not m.get("finished"):
                continue
            feats = table.get(m["map"].replace("-", "_")) or table.get(m["map"])
            if feats is None:
                continue
            side = "a" if m["side"] == "A" else "b"
            picks = picks_of(m["roster_a"])
            if not picks:
                continue
            games += 1
            mg = float(m["gems_a"] - m["gems_b"])
            for i, pick in enumerate(picks):
                X.append(draft_features.build(feats[side], picks[:i], i)); y.append(pick)
                mask.append([p not in picks[:i] for p in range(draft_features.N_PICKS)])
                won.append(1.0 if mg > 0 else 0.0); margin.append(mg); meta.append((i, len(picks))); maps.append(m["map"])
    return games, dict(X=np.array(X, dtype=np.uint8), y=np.array(y, dtype=np.int8), mask=np.array(mask, dtype=np.bool_),
                       won=np.array(won, dtype=np.float32), engine=np.array(["selfplay"] * len(X)), rating=np.array(margin, dtype=np.float32),
                       meta=np.array(meta, dtype=np.int32), maps=np.array(maps))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--opponent", default="hybrid@hybrid-v11")
    ap.add_argument("--sweeps", type=int, default=15)
    ap.add_argument("--explore", type=float, default=1.0)
    ap.add_argument("--salt-start", type=int, default=None, help="first salt (default: continue after the sweeps on disk)")
    ap.add_argument("--dumps", nargs="+", default=[d for d in bdd.__dict__.get("DEFAULT_DUMPS", []) if os.path.isdir(d)] or
                    [os.path.join(ROOT, "data", "replays", "1.0.1", "ladder25"), os.path.join(ROOT, "data", "replays", "1.0.1", "ladder")])
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "data", "drafts", "selfplay.npz"))
    ap.add_argument("--collect-only", action="store_true")
    args = ap.parse_args()
    os.makedirs(OUT_ROOT, exist_ok=True)
    if not args.collect_only:
        done = [int(os.path.basename(d)) for d in glob.glob(os.path.join(OUT_ROOT, "[0-9]*")) if os.path.basename(d).isdigit()]
        salt = args.salt_start if args.salt_start is not None else (max(done) + 1 if done else 1)
        original = open(CONFIG, encoding="utf-8").read()
        try:
            for k in range(args.sweeps):
                out = os.path.join(OUT_ROOT, str(salt))
                set_config(args.explore, salt)
                t = time.time()
                r = subprocess.run([PY, os.path.join(ROOT, "tools", "run_matches.py"), "hybrid", args.opponent, "--out", out],
                                   cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
                line = next((l for l in r.stdout.splitlines() if l.startswith("hybrid:") and "over" in l), "(no summary)")
                print(f"sweep {k + 1}/{args.sweeps} salt {salt}: {line[:90]}  [{time.time() - t:.0f}s]", flush=True)
                salt += 1
        finally:
            open(CONFIG, "w", encoding="utf-8", newline="\n").write(original)
            print("config restored")
    table = bdd.map_table(args.dumps)
    games, rows = collect(table)
    np.savez_compressed(args.out, **rows)
    from collections import Counter
    print(f"{games} games, {len(rows['X'])} pick rows -> {args.out}; our wins {rows['won'].mean():.1%}; "
          f"mean margin {rows['rating'].mean():+.0f}; picks {dict(sorted(Counter(rows['y'].tolist()).items()))}")


if __name__ == "__main__":
    main()
