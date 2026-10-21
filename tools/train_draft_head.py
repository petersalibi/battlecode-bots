"""Train the draft head (N_FEATURES -> 32 -> 12) on top teams' picks and export it as int8
weights next to the other heads.

    python tools/train_draft_head.py data/drafts/drafts.npz --engine 1.0.2 --winners

Rows come from tools/build_draft_dataset.py. Cross entropy over the legal picks (not yet picked
by that team), with wins weighted up (--winners) and the current engine's games weighted up
(--engine; older engines keep --old-weight). Quantisation, the integer reference and the export
reuse the mode selector's. The exported head goes in weights.py as DRAFT_HEAD; the draft masks
champions outside config.DRAFT_ALLOWED before taking the argmax.
"""
import argparse
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
from train_mode_selector import INPUT_SCALE, CENTER_PENALTY, MASK_NEG, quantise, int_forward  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
N_PICKS = 12


class DraftNet(nn.Module):
    def __init__(self, n_in, hidden=32):
        super().__init__()
        self.layers = nn.ModuleList([nn.Linear(n_in, hidden), nn.Linear(hidden, N_PICKS)])

    def forward(self, x):
        x = x * INPUT_SCALE
        x = F.relu(self.layers[0](x))
        return self.layers[1](x)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("data")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "bots", "hybrid", "src", "weights.py"))
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--batch", type=int, default=128)
    ap.add_argument("--holdout", type=float, default=0.15)
    ap.add_argument("--hidden", type=int, default=32)
    ap.add_argument("--winners", action="store_true")
    ap.add_argument("--loser-weight", type=float, default=0.3)
    ap.add_argument("--engine", help="weight this engine version's rows at 1, others at --old-weight")
    ap.add_argument("--old-weight", type=float, default=0.3)
    ap.add_argument("--min-rating", type=float, default=0.0)
    ap.add_argument("--logit-l2", type=float, default=1e-3)
    ap.add_argument("--margin", type=float, default=0.0, help="logit gap below which the priority list picks")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-export", action="store_true")
    args = ap.parse_args()

    d = np.load(args.data)
    X, y, mask, won, engine, rating, meta, maps = (d[k] for k in ("X", "y", "mask", "won", "engine", "rating", "meta", "maps"))
    X = X.astype(np.float32); y = y.astype(np.int64)
    keep = np.ones(len(y), dtype=bool)
    if args.min_rating > 0:
        keep &= rating >= args.min_rating
    X, y, mask, won, engine, rating, meta, maps = (a[keep] for a in (X, y, mask, won, engine, rating, meta, maps))
    w = np.ones(len(y), dtype=np.float32)
    if args.winners:
        w *= np.where(won > 0, 1.0, args.loser_weight)
    if args.engine:
        w *= np.where(engine == args.engine, 1.0, args.old_weight)
    # Hold out whole maps' worth of rows? No: hold out by draft (consecutive rows share a draft),
    # using a hash of (map, first pick row index) is overkill; a random 15% of rows is fine here
    # because every row is an independent pick decision given its own inputs.
    rng = np.random.default_rng(args.seed)
    test = rng.random(len(y)) < args.holdout
    torch.manual_seed(args.seed)
    net = DraftNet(X.shape[1], args.hidden)
    opt = torch.optim.Adam(net.parameters(), lr=args.lr, weight_decay=1e-4)
    Xt, Mt, yt, wt = (torch.tensor(X[~test]), torch.tensor(mask[~test]), torch.tensor(y[~test]), torch.tensor(w[~test]))
    Xv, Mv, yv = torch.tensor(X[test]), torch.tensor(mask[test]), torch.tensor(y[test])
    n = len(Xt)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, args.epochs)
    bar = Progress(args.epochs, "epochs")
    print(f"{len(X)} pick rows, {int(test.sum())} held out; engines {dict(zip(*np.unique(engine, return_counts=True)))}")
    for epoch in range(args.epochs):
        net.train()
        perm = torch.randperm(n)
        for i in range(0, n, args.batch):
            idx = perm[i:i + args.batch]
            raw = net(Xt[idx]); m = Mt[idx]
            ce = F.cross_entropy(raw.masked_fill(~m, MASK_NEG), yt[idx], reduction="none")
            legal = raw.masked_fill(~m, 0.0); count = m.sum(1).clamp(min=1)
            loss = (ce * wt[idx]).mean() + CENTER_PENALTY * ((legal.sum(1) / count) ** 2).mean() + args.logit_l2 * ((legal ** 2).sum(1) / count).mean()
            opt.zero_grad(); loss.backward(); opt.step()
        sched.step()
        bar.step()
        if epoch % 10 == 0 or epoch == args.epochs - 1:
            net.eval()
            with torch.no_grad():
                pv = net(Xv).masked_fill(~Mv, MASK_NEG).argmax(1)
                pt = net(Xt).masked_fill(~Mt, MASK_NEG).argmax(1)
            print(f"epoch {epoch:3d} train top-1 {(pt == yt).float().mean():.3f} held-out top-1 {(pv == yv).float().mean():.3f}")
    bar.done()
    spec, score_unit = quantise(net, X[~test][:5000])
    with torch.no_grad():
        float_pred = net(Xv).masked_fill(~Mv, MASK_NEG).argmax(1).numpy()
    int_pred = np.array([np.where(m, int_forward(spec, x), -(1 << 40)).argmax() for x, m in zip(X[test], mask[test])])
    print(f"quantised: agrees with float on {100 * (int_pred == float_pred).mean():.1f}% of held-out picks, "
          f"matches the pick on {100 * (int_pred == yv.numpy()).mean():.1f}%; one logit = {1 / score_unit:.1f} score units")
    # What it would draft on each map from side a, for a sanity read.
    try:
        names = ("RYTH", "VESPER", "VENDETTA", "CASSI+ISSAC", "STELLAR", "PRISM", "MARCELLA", "WILLOW", "REN", "VARG", "TAVI", "HEART")
        shown = set()
        for i in np.argsort(maps):
            mp = str(maps[i])
            if mp in shown or int(meta[i][0]) != 0:
                continue
            shown.add(mp)
            picks = []
            base = X[i].copy()
            size = int(meta[i][1])
            for k in range(size):
                x = base.copy(); x[22] = min(255, k * 64)
                for p in picks:
                    x[23 + p] = 255
                scores = int_forward(spec, x.astype(np.int64))
                for p in picks:
                    scores[p] = -(1 << 40)
                picks.append(int(np.argmax(scores)))
            print(f"  {mp:24} {' '.join(names[p] for p in picks)}")
    except Exception as exc:
        print("sanity read failed:", exc)
    if not args.no_export:
        margin = int(round(args.margin / score_unit))
        note = ("trained %s on %d pick rows%s; held-out top-1 %.1f%%" % (time.strftime("%Y-%m-%d %H:%M"), int((~test).sum()),
                " (engine %s weighted up)" % args.engine if args.engine else "", 100 * (int_pred == yv.numpy()).mean()))
        weights_io.write_head(args.out, "DRAFT_HEAD", spec, margin, "logit gap below which the priority list picks", note)
        print("wrote", args.out)


if __name__ == "__main__":
    main()
