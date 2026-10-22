"""Train the contact head (what to do with an enemy within reach: attack / close / hold / retreat)
by imitation of the band's own turns, and export it as packed int8 weights.

    python tools/train_contact_head.py data/contact/band-1.0.3.npz [--epochs 30] [--margin 0.5]

Holds out 15% of games, prints held-out accuracy against the majority class and the per-class
confusion, quantises with the selector's int8 scheme (tools/train_mode_selector.quantise) and
writes CONTACT_HEAD_SPEC / CONTACT_HEAD_MARGIN into bots/hybrid/src/weights.py. Run
tools/parity_test.py afterwards.
"""
import argparse
import os
import sys
import time

import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weights_io  # noqa: E402
from train_mode_selector import ModeNet, quantise, int_forward, INPUT_SCALE  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class cf:   # the head's action set (bots/hybrid/src/contact_features.py imports the SDK, so it is not loaded here)
    N_ACTIONS = 4
    ACTION_NAMES = ("attack", "close", "hold", "retreat")

HIDDEN = (16,)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("data")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "bots", "hybrid", "src", "weights.py"))
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--batch", type=int, default=512)
    ap.add_argument("--holdout", type=float, default=0.15)
    ap.add_argument("--margin", type=float, default=0.5, help="logit gap below which the script decides")
    ap.add_argument("--logit-l2", type=float, default=1e-3)
    ap.add_argument("--winners-weight", type=float, default=1.0, help="weight of rows from sides that won (others 1.0)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-export", action="store_true")
    args = ap.parse_args()
    d = np.load(args.data)
    X, y, meta, match = d["X"].astype(np.float32), d["y"].astype(np.int64), d["meta"], d["match"]
    rng = np.random.RandomState(args.seed)
    games = np.unique(match)
    held = set(rng.choice(games, size=max(1, int(len(games) * args.holdout)), replace=False).tolist())
    test = np.array([m in held for m in match])
    train = ~test
    wts = np.where(meta[:, 3] == 1, args.winners_weight, 1.0).astype(np.float32)
    torch.manual_seed(args.seed)
    net = ModeNet(X.shape[1], hidden=HIDDEN, n_out=cf.N_ACTIONS)
    opt = torch.optim.Adam(net.parameters(), lr=args.lr, weight_decay=1e-4)
    Xt, yt, wt = torch.tensor(X[train]), torch.tensor(y[train]), torch.tensor(wts[train])
    n = len(Xt)
    for epoch in range(args.epochs):
        perm = torch.randperm(n)
        total = 0.0
        net.train()
        for i in range(0, n, args.batch):
            idx = perm[i:i + args.batch]
            logits = net(Xt[idx])
            loss = (F.cross_entropy(logits, yt[idx], reduction="none") * wt[idx]).mean()
            loss = loss + args.logit_l2 * (logits ** 2).mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
            total += float(loss) * len(idx)
        if epoch % 5 == 4 or epoch == args.epochs - 1:
            net.eval()
            with torch.no_grad():
                pred = net(torch.tensor(X[test])).argmax(1).numpy()
            print(f"epoch {epoch + 1}: loss {total / n:.4f}, held-out accuracy {100 * (pred == y[test]).mean():.1f}%")
    net.eval()
    with torch.no_grad():
        logits = net(torch.tensor(X[test])).numpy()
    pred = logits.argmax(1)
    majority = np.bincount(y[train], minlength=cf.N_ACTIONS).argmax()
    print(f"held-out: {len(pred)} rows from {len(held)} games; accuracy {100 * (pred == y[test]).mean():.1f}% "
          f"vs majority ({cf.ACTION_NAMES[majority]}) {100 * (y[test] == majority).mean():.1f}%")
    conf = np.zeros((cf.N_ACTIONS, cf.N_ACTIONS), dtype=int)
    for a, b in zip(y[test], pred):
        conf[a, b] += 1
    print("confusion (rows = band's action, columns = head):")
    for i in range(cf.N_ACTIONS):
        print(f"  {cf.ACTION_NAMES[i]:8s} " + " ".join(f"{conf[i, j]:7d}" for j in range(cf.N_ACTIONS))
              + f"   recall {100 * conf[i, i] / max(1, conf[i].sum()):.0f}%")
    srt = np.sort(logits, axis=1)
    gap = srt[:, -1] - srt[:, -2]
    sure = gap >= args.margin
    print(f"margin {args.margin}: head speaks on {100 * sure.mean():.0f}% of held-out rows, accuracy there "
          f"{100 * (pred[sure] == y[test][sure]).mean() if sure.any() else 0:.1f}%")
    spec, unit = quantise(net, X[train][:4096])
    ref = np.array([int_forward(spec, x).argmax() for x in X[test][:2000]])
    print(f"int8 export: agreement with float argmax on 2000 held-out rows {100 * (ref == pred[:2000]).mean():.1f}%")
    if args.no_export:
        return
    note = (f"trained {time.strftime('%Y-%m-%d %H:%M')} on {int(train.sum())} band contact rows ({args.epochs} epochs); "
            f"held-out accuracy {100 * (pred == y[test]).mean():.1f}% vs majority {100 * (y[test] == majority).mean():.1f}%")
    margin = int(round(args.margin / unit))
    weights_io.write_head(args.out, "CONTACT_HEAD", spec, margin,
                          f"integer score gap equal to {args.margin} logits; below it the script decides", note)
    print(f"wrote CONTACT_HEAD to {args.out} (margin {margin})")


if __name__ == "__main__":
    main()
