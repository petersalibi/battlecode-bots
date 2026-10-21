"""Train the escape head, a bilinear survival model ([state] -> weight per tile feature, dotted
with a candidate tile's features = survival logit), and export it as int8 weights.

    python tools/train_escape_head.py data/replays/1.0.1/escapes.npz --winners

Rows come from tools/replay_escape_dataset.py: the state, the tile the body stepped to and whether
it was alive 20 rounds later. The loss is a binary cross entropy on that tile (outcome, not
imitation: the model learns which steps strong teams' bodies survived, so at inference the bot
steps to the tile with the highest survival score). Rows of winning sides are weighted up.
Quantisation, the integer reference and the export reuse the camp scorer's.
"""
import argparse
import os
import sys
import time

import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from progress import Progress  # noqa: E402
import weights_io  # noqa: E402
from train_mode_selector import INPUT_SCALE, quantise, int_forward  # noqa: E402
import train_camp_scorer as tcs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(path, holdout_fraction, min_rating):
    d = np.load(path)
    S, C, y = d["S"].astype(np.float32), d["C"].astype(np.float32), d["y"].astype(np.float32)
    CA, M, chosen = d["CA"].astype(np.float32), d["M"], d["chosen"].astype(np.int64)
    match_id, rating, won = d["match_id"], d["rating"], d["won"]
    keep = np.ones(len(y), dtype=bool)
    if min_rating > 0:
        keep &= rating >= min_rating
    S, C, y, CA, M, chosen, match_id, rating, won = (a[keep] for a in (S, C, y, CA, M, chosen, match_id, rating, won))
    ids = np.unique(match_id)
    rng = np.random.default_rng(0)
    rng.shuffle(ids)
    test_ids = set(ids[: max(1, int(len(ids) * holdout_fraction))].tolist())
    test = np.array([m in test_ids for m in match_id])
    return S, C, y, CA, M, chosen, rating, won, test


def train(S, C, y, w, test, epochs, lr, batch, seed, arch, hidden):
    torch.manual_seed(seed)
    net = tcs.CampNet(arch, hidden)
    opt = torch.optim.Adam(net.parameters(), lr=lr, weight_decay=1e-4)
    Xt = torch.tensor(np.concatenate([S[~test], C[~test]], axis=1)); yt = torch.tensor(y[~test]); wt = torch.tensor(w[~test])
    Xv = torch.tensor(np.concatenate([S[test], C[test]], axis=1)); yv = torch.tensor(y[test])
    n = len(Xt)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, epochs)
    bar = Progress(epochs, "epochs")
    for epoch in range(epochs):
        net.train()
        perm = torch.randperm(n)
        total = 0.0
        for i in range(0, n, batch):
            idx = perm[i:i + batch]
            logit = net(Xt[idx]).squeeze(-1)
            loss = (F.binary_cross_entropy_with_logits(logit, yt[idx], reduction="none") * wt[idx]).mean() \
                + 1e-4 * (logit ** 2).mean()
            opt.zero_grad(); loss.backward(); opt.step()
            total += loss.item() * len(idx)
        sched.step()
        bar.step(note=f"bce {total / n:.3f}")
        if epoch % 5 == 0 or epoch == epochs - 1:
            net.eval()
            with torch.no_grad():
                pv = torch.sigmoid(net(Xv).squeeze(-1)).numpy()
            auc = _auc(pv, y[test])
            print(f"epoch {epoch:3d} bce {total / n:.4f} held-out auc {auc:.3f} acc {np.mean((pv > 0.5) == (y[test] > 0.5)):.3f}")
    bar.done()
    return net


def _auc(p, y):
    order = np.argsort(p)
    ranks = np.empty(len(p)); ranks[order] = np.arange(1, len(p) + 1)
    pos = y > 0.5
    n_pos, n_neg = pos.sum(), (~pos).sum()
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    return (ranks[pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("data")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "bots", "hybrid", "src", "weights.py"))
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--holdout", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--winners", action="store_true")
    ap.add_argument("--loser-weight", type=float, default=0.5)
    ap.add_argument("--min-rating", type=float, default=0.0)
    ap.add_argument("--arch", choices=("bilinear", "tower"), default="bilinear")
    ap.add_argument("--hidden", type=int, default=16)
    ap.add_argument("--margin", type=float, default=0.0, help="survival-logit gap below which the script's step stands")
    ap.add_argument("--no-export", action="store_true")
    args = ap.parse_args()

    S, C, y, CA, M, chosen, rating, won, test = load(args.data, args.holdout, args.min_rating)
    tcs.N_STATE, tcs.N_CAND = S.shape[1], C.shape[1]
    w = np.ones(len(y), dtype=np.float32)
    if args.winners:
        w = np.where(won > 0, 1.0, args.loser_weight).astype(np.float32)
    print(f"{len(S)} rows, {int(test.sum())} held out; survived {y.mean():.3f}")
    net = train(S, C, y, w, test, args.epochs, args.lr, args.batch, args.seed, args.arch, args.hidden)

    spec, tower_unit = quantise(net, S[~test][:20000].astype(np.float32))
    score_unit = tower_unit * INPUT_SCALE
    # Integer vs float agreement on the held-out rows' candidate rankings.
    net.eval()
    agree = 0; total = 0; picks_chosen = 0
    with torch.no_grad():
        for s, ca, m, ch in zip(S[test][:3000], CA[test][:3000], M[test][:3000], chosen[test][:3000]):
            k = int(m.sum())
            x = np.concatenate([np.repeat(s[None], k, 0), ca[:k]], axis=1).astype(np.float32)
            fl = net(torch.tensor(x)).squeeze(-1).numpy()
            wv = int_forward(spec, s.astype(np.int64))
            it = np.array([int(np.dot(wv, ca[i].astype(np.int64))) for i in range(k)])
            agree += int(fl.argmax() == it.argmax()); total += 1; picks_chosen += int(it.argmax() == ch)
    print(f"quantised: agrees with float on {100 * agree / max(total, 1):.1f}% of held-out rankings; "
          f"picks the tile the body took on {100 * picks_chosen / max(total, 1):.1f}%; one logit = {1 / score_unit:.1f} score units")
    if not args.no_export:
        margin = int(round(args.margin / score_unit))
        note = ("%s survival head, trained %s on %d flee rows (%d epochs)" % (args.arch, time.strftime("%Y-%m-%d %H:%M"), int((~test).sum()), args.epochs))
        weights_io.write_head(args.out, "ESCAPE_HEAD", spec, margin, "survival-logit gap equal to %s logits; below it the script steps" % args.margin,
                              note, extra={"n_state": S.shape[1], "kind": "bilinear"})
        print("wrote", args.out)


if __name__ == "__main__":
    main()
