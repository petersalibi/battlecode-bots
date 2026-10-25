"""Train the draft value head: the map plus a full composition -> our expected gem margin, from
the self-play sweeps (replays/selfplay/<salt>/results.json), and export it as DRAFT_VALUE.

    python tools/train_draft_value.py --opponent-tag v11

One row per game: the draft head's input layout with every pick set and the pick index at the
team size, labelled with gems_a - gems_b (our side is always bot A in those sweeps). The bot
drafts greedily with it: for each remaining pick it evaluates every allowed champion added to
the composition so far and takes the highest predicted margin (draft._value_pick). Held-out
quality is reported as the correlation between predicted and actual margin and as the mean
actual margin of the games whose composition the model ranks in its top quarter.
"""
import argparse
import glob
import json
import os
import sys
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from progress import Progress  # noqa: E402
import weights_io  # noqa: E402
from train_mode_selector import INPUT_SCALE, quantise, int_forward  # noqa: E402
import build_draft_dataset as bdd  # noqa: E402
import selfplay_drafts as sp  # noqa: E402
import draft_features  # noqa: E402

ROOT = bdd.rf.ROOT


class ValueNet(nn.Module):
    def __init__(self, n_in, hidden=32):
        super().__init__()
        self.layers = nn.ModuleList([nn.Linear(n_in, hidden), nn.Linear(hidden, 1)])

    def forward(self, x):
        x = x * INPUT_SCALE
        return self.layers[1](F.relu(self.layers[0](x)))


def rows(table):
    X, y, maps = [], [], []
    for results in sorted(glob.glob(os.path.join(sp.OUT_ROOT, "*", "results.json"))):
        for m in json.load(open(results, encoding="utf-8"))["matches"]:
            if not m.get("finished"):
                continue
            feats = table.get(m["map"].replace("-", "_")) or table.get(m["map"])
            picks = sp.picks_of(m["roster_a"])
            if feats is None or not picks:
                continue
            side = "a" if m["side"] == "A" else "b"
            X.append(draft_features.build(feats[side], picks, len(picks)))
            y.append(float(m["gems_a"] - m["gems_b"]))
            maps.append(m["map"])
    return np.array(X, dtype=np.uint8), np.array(y, dtype=np.float32), np.array(maps)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "bots", "hybrid", "src", "weights.py"))
    ap.add_argument("--dumps", nargs="+", default=[os.path.join(ROOT, "data", "replays", "1.0.1", "ladder25"),
                                                  os.path.join(ROOT, "data", "replays", "1.0.1", "ladder")])
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--hidden", type=int, default=32)
    ap.add_argument("--holdout", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--scale", type=float, default=1000.0, help="margins are divided by this for training")
    ap.add_argument("--no-export", action="store_true")
    args = ap.parse_args()
    table = bdd.map_table(args.dumps)
    X, y, maps = rows(table)
    rng = np.random.default_rng(args.seed)
    test = rng.random(len(y)) < args.holdout
    print(f"{len(y)} games; margin mean {y.mean():+.0f}, wins {np.mean(y > 0):.1%}; {int(test.sum())} held out")
    torch.manual_seed(args.seed)
    net = ValueNet(X.shape[1], args.hidden)
    opt = torch.optim.Adam(net.parameters(), lr=args.lr, weight_decay=1e-4)
    Xt, yt = torch.tensor(X[~test].astype(np.float32)), torch.tensor(y[~test] / args.scale)
    Xv, yv = torch.tensor(X[test].astype(np.float32)), y[test]
    bar = Progress(args.epochs, "epochs")
    for epoch in range(args.epochs):
        net.train()
        perm = torch.randperm(len(Xt))
        for i in range(0, len(Xt), 128):
            idx = perm[i:i + 128]
            loss = F.mse_loss(net(Xt[idx]).squeeze(-1), yt[idx])
            opt.zero_grad(); loss.backward(); opt.step()
        bar.step()
        if epoch % 50 == 0 or epoch == args.epochs - 1:
            net.eval()
            with torch.no_grad():
                pv = net(Xv).squeeze(-1).numpy() * args.scale
            corr = float(np.corrcoef(pv, yv)[0, 1]) if len(yv) > 2 else float("nan")
            top = pv >= np.quantile(pv, 0.75)
            print(f"epoch {epoch:3d} held-out corr {corr:.3f}; mean margin of its top quarter {yv[top].mean():+.0f} vs all {yv.mean():+.0f}")
    bar.done()
    spec, score_unit = quantise(net, X[~test][:5000].astype(np.float32))
    with torch.no_grad():
        fl = net(Xv).squeeze(-1).numpy()
    it = np.array([int_forward(spec, x.astype(np.int64))[0] * score_unit for x in X[test]])
    print(f"quantised: held-out corr with float {np.corrcoef(fl, it)[0, 1]:.4f}; one unit of margin/{args.scale:.0f} = {1 / score_unit:.1f} score units")
    # Greedy drafts per map from side a, for a sanity read.
    names = ("RYTH", "VESPER", "VENDETTA", "CASSI+ISSAC", "STELLAR", "PRISM", "MARCELLA", "WILLOW", "REN", "VARG", "TAVI", "HEART")
    allowed = [0, 1, 2, 3, 6, 7, 8, 9, 10, 11]
    sizes = {}
    for results in sorted(glob.glob(os.path.join(sp.OUT_ROOT, "*", "results.json")))[:1]:
        for m in json.load(open(results, encoding="utf-8"))["matches"]:
            sizes[m["map"]] = len(sp.picks_of(m["roster_a"]))
    for mp in sorted(table):
        key = next((k for k in sizes if k.replace("-", "_") == mp or k == mp), None)
        size = sizes.get(key, 4)
        picks = []
        for k in range(size):
            best, best_v = None, None
            for p in allowed:
                if p in picks:
                    continue
                x = np.array(draft_features.build(table[mp]["a"], picks + [p], size), dtype=np.int64)
                v = int_forward(spec, x)[0]
                if best is None or v > best_v:
                    best, best_v = p, v
            picks.append(best)
        print(f"  {mp:24} {' '.join(names[p] for p in picks)}")
    if not args.no_export:
        note = "draft value head trained %s on %d self-play games vs the fixed opponent; held-out corr %.3f" % (
            time.strftime("%Y-%m-%d %H:%M"), int((~test).sum()), float(np.corrcoef(pv, yv)[0, 1]))
        weights_io.write_head(args.out, "DRAFT_VALUE", spec, 0, "unused", note)
        print("wrote", args.out)


if __name__ == "__main__":
    main()
