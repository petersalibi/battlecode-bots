"""Train the camp scorer, a factored candidate scorer ([state | candidate] -> 32 -> 16 -> 1), and
export it as int8 weights next to the mode selector in bots/hybrid/src/weights.py.

    python tools/train_camp_scorer.py data/replays/1.0.1/camps.npz --winners --logit-l2 1e-3

Rows come from tools/replay_camp_dataset.py: a state vector, up to K candidate vectors with a
mask, the index of the group the body went to, and a reward. The loss is a listwise cross
entropy over the masked candidates (softmax over the K scores), weighted by the reward's
advantage like the mode selector, with the same centre and magnitude penalties on the legal
scores so the int8 export keeps its resolution. Quantisation, the integer reference and the
export reuse tools/train_mode_selector.py.
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

HIDDEN = (32, 16)
N_STATE = N_CAND = None       # set from the dataset's shapes (camp_features.py imports the SDK world)


class CampNet(nn.Module):
    """Bilinear scorer: the layers run on the state alone and output one weight per candidate
    feature; a candidate's score is that weight vector dotted with its features. "bilinear" is a
    single linear layer (state -> weights); "tower" puts a hidden layer in between. The bot's
    nn.Bilinear does the same in integers, so the per-candidate cost is one dot product."""
    def __init__(self, arch="tower", hidden=16):
        super().__init__()
        dims = (N_STATE, N_CAND) if arch == "bilinear" else (N_STATE, hidden, N_CAND)
        self.layers = nn.ModuleList(nn.Linear(dims[i], dims[i + 1]) for i in range(len(dims) - 1))

    def weights_for(self, S):
        x = S * INPUT_SCALE
        for layer in self.layers[:-1]:
            x = F.relu(layer(x))
        return self.layers[-1](x)

    def forward(self, x):
        """Concatenated [state | candidate] rows -> [M, 1] scores (calibration and parity)."""
        w = self.weights_for(x[:, :N_STATE])
        return (w * (x[:, N_STATE:] * INPUT_SCALE)).sum(1, keepdim=True)

    def scores(self, S, C):
        """S [B, N_STATE], C [B, K, N_CAND] -> [B, K] raw scores."""
        w = self.weights_for(S)
        return torch.einsum("bn,bkn->bk", w, C * INPUT_SCALE)


def load(path, holdout_fraction, min_rating):
    d = np.load(path)
    S, C, M, y = d["S"].astype(np.float32), d["C"].astype(np.float32), d["M"], d["y"].astype(np.int64)
    reward, match_id, rating, won = d["reward"], d["match_id"], d["rating"], d["won"]
    keep = (y >= 0) & M[np.arange(len(y)), np.clip(y, 0, M.shape[1] - 1)]
    if min_rating > 0:
        keep &= rating >= min_rating
    print(f"keeping {int(keep.sum())} of {len(keep)} rows (legal label{', rated %.0f+' % min_rating if min_rating else ''})")
    S, C, M, y, reward, match_id, rating, won = (a[keep] for a in (S, C, M, y, reward, match_id, rating, won))
    ids = np.unique(match_id)
    rng = np.random.default_rng(0)
    rng.shuffle(ids)
    test_ids = set(ids[: max(1, int(len(ids) * holdout_fraction))].tolist())
    test = np.array([m in test_ids for m in match_id])
    return S, C, M, y, reward, rating, won, test


def example_weights(reward, beta, clip_hi, won, rating, winners, loser_weight):
    adv = (reward - reward.mean()) / (reward.std() + 1e-6)
    w = np.clip(np.exp(beta * adv), 0.2, clip_hi)
    if winners:
        side = np.where(won > 0, 1.0, loser_weight)
        if (rating > 0).any():
            side = side * np.clip(rating / max(rating.max(), 1.0), 0.5, 1.0)
        w = w * side
    return w.astype(np.float32)


def accuracy(net, S, C, M, y):
    net.eval()
    with torch.no_grad():
        if len(S) == 0:
            return float("nan")
        pred = net.scores(S, C).masked_fill(~M, MASK_NEG).argmax(1)
        return (pred == y).float().mean().item()


def train(S, C, M, y, w, test, epochs, lr, batch, seed, logit_l2, arch, hidden):
    torch.manual_seed(seed)
    net = CampNet(arch, hidden)
    opt = torch.optim.Adam(net.parameters(), lr=lr, weight_decay=1e-4)
    St, Ct, Mt, yt, wt = (torch.tensor(S[~test]), torch.tensor(C[~test]), torch.tensor(M[~test]), torch.tensor(y[~test]), torch.tensor(w[~test]))
    Sv, Cv, Mv, yv = torch.tensor(S[test]), torch.tensor(C[test]), torch.tensor(M[test]), torch.tensor(y[test])
    n = len(St)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, epochs)
    bar = Progress(epochs, "epochs")
    for epoch in range(epochs):
        net.train()
        perm = torch.randperm(n)
        total = 0.0
        for i in range(0, n, batch):
            idx = perm[i:i + batch]
            raw = net.scores(St[idx], Ct[idx])
            m = Mt[idx]
            logits = raw.masked_fill(~m, MASK_NEG)
            ce = F.cross_entropy(logits, yt[idx], reduction="none")
            legal = raw.masked_fill(~m, 0.0)
            count = m.sum(1).clamp(min=1)
            center = ((legal.sum(1) / count) ** 2).mean()
            magnitude = ((legal ** 2).sum(1) / count).mean()
            ce_loss = (ce * wt[idx]).mean()
            loss = ce_loss + CENTER_PENALTY * center + logit_l2 * magnitude
            opt.zero_grad()
            loss.backward()
            opt.step()
            total += ce_loss.item() * len(idx)
        sched.step()
        bar.step(note=f"ce {total / n:.3f}")
        if epoch % 5 == 0 or epoch == epochs - 1:
            print(f"epoch {epoch:3d} ce {total / n:.4f} train acc {accuracy(net, St, Ct, Mt, yt):.3f} "
                  f"test acc {accuracy(net, Sv, Cv, Mv, yv):.3f}")
    bar.done()
    return net


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("data")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "bots", "hybrid", "src", "weights.py"))
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--beta", type=float, default=0.5)
    ap.add_argument("--clip", type=float, default=4.0)
    ap.add_argument("--holdout", type=float, default=0.15)
    ap.add_argument("--margin", type=float, default=0.3, help="logit gap below which the script's camp choice stands")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--logit-l2", type=float, default=1e-3)
    ap.add_argument("--winners", action="store_true")
    ap.add_argument("--loser-weight", type=float, default=0.3)
    ap.add_argument("--min-rating", type=float, default=0.0)
    ap.add_argument("--no-export", action="store_true")
    ap.add_argument("--arch", choices=("bilinear", "tower"), default="tower")
    ap.add_argument("--hidden", type=int, default=16, help="tower: hidden width of the state tower")
    args = ap.parse_args()

    S, C, M, y, reward, rating, won, test = load(args.data, args.holdout, args.min_rating)
    global N_STATE, N_CAND
    N_STATE, N_CAND = S.shape[1], C.shape[2]
    w = example_weights(reward, args.beta, args.clip, won, rating, args.winners, args.loser_weight)
    nearest = float((y == 0).mean())
    print(f"{len(S)} rows, {int(test.sum())} held out; weights mean {w.mean():.2f}; "
          f"'nearest candidate' baseline accuracy {nearest:.3f}")
    net = train(S, C, M, y, w, test, args.epochs, args.lr, args.batch, args.seed, args.logit_l2, args.arch, args.hidden)

    # The integer tower runs on the state alone: calibrate on state rows. The final score is the
    # tower's integer output dotted with the raw 0-255 candidate, so one float logit is
    # 1 / (tower unit * INPUT_SCALE) score units.
    spec, tower_unit = quantise(net, S[~test][:20000].astype(np.float32))
    score_unit = tower_unit * INPUT_SCALE
    Sv, Cv, Mv, yv = S[test], C[test], M[test], y[test]
    net.eval()
    with torch.no_grad():
        float_pred = net.scores(torch.tensor(Sv), torch.tensor(Cv)).masked_fill(~torch.tensor(Mv), MASK_NEG).argmax(1).numpy()
    int_pred = []
    for s_, c, m in zip(Sv, Cv, Mv):
        wv = int_forward(spec, s_)
        scores = np.array([int(np.dot(wv, ci.astype(np.int64))) if ok else -(1 << 60) for ci, ok in zip(c, m)])
        int_pred.append(int(scores.argmax()))
    int_pred = np.array(int_pred)
    print(f"quantised: agrees with float on {100 * (int_pred == float_pred).mean():.1f}% of held-out rows, "
          f"picks the label on {100 * (int_pred == yv).mean():.1f}% (baseline {100 * (yv == 0).mean():.1f}%); "
          f"one logit = {1 / score_unit:.1f} score units")
    if not args.no_export:
        margin = int(round(args.margin / score_unit))
        note = ("%s, trained %s on %d rows (%d epochs, beta %.2f); held-out top-1 %.1f%% vs nearest-candidate %.1f%%"
                % (args.arch, time.strftime("%Y-%m-%d %H:%M"), int((~test).sum()), args.epochs, args.beta,
                   100 * (int_pred == yv).mean(), 100 * (yv == 0).mean()))
        weights_io.write_head(args.out, "CAMP_SCORER", spec, margin,
                              "integer score gap equal to %s logits; below it the script's camp choice stands" % args.margin,
                              note, extra={"n_state": N_STATE, "kind": "bilinear"})
        print("wrote", args.out)


if __name__ == "__main__":
    main()
