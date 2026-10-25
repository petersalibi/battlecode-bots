"""Train the mode selector (66 -> 32 -> 16 -> 6) and export it as int8 weights for the bot.

    python tools/train_mode_selector.py data/modes.npz --epochs 30 -o bots/hybrid/src/weights.py

Objective: outcome-weighted behavioural cloning. The network learns the scripted bot's mode
choices, but each example is weighted by how well that choice turned out (the reward label from
tools/build_dataset.py, standardised and passed through exp(beta * advantage), clipped), so modes
that led to tributes are reinforced and modes that preceded deaths are discounted. With beta = 0
this is plain imitation, which should reproduce the script.

The float model is trained on raw 0-255 inputs with the mode mask applied to the logits. It is
then quantised after training: int8 weights per layer (symmetric), uint8 activations with scales
from a calibration pass, and an integer multiplier/shift per hidden layer, exactly the arithmetic
of bots/hybrid/src/nn.py. The export is checked against a numpy integer reference, and
tools/parity_test.py checks the bot's own engine against the same reference.
"""
import argparse
import math
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bots", "hybrid", "src"))
import config  # noqa: E402

HIDDEN = (32, 16)
N_MODES = config.N_MODES
MASK_NEG = -1e4
_LEGAL = slice(None)          # rows kept by load(); applied to the extra arrays main() reads
INPUT_SCALE = 1.0 / 255.0     # the model sees inputs in [0, 1]; the export folds this into the first layer
CENTER_PENALTY = 1e-3         # keeps the mean logit near zero so int8 scores keep their resolution


class ModeNet(nn.Module):
    def __init__(self, n_in, hidden=HIDDEN, n_out=N_MODES):
        super().__init__()
        dims = (n_in, *hidden, n_out)
        self.layers = nn.ModuleList(nn.Linear(dims[i], dims[i + 1]) for i in range(len(dims) - 1))

    def forward(self, x):
        x = x * INPUT_SCALE
        for layer in self.layers[:-1]:
            x = F.relu(layer(x))
        return self.layers[-1](x)


def load(path, holdout_fraction, min_rating=0.0):
    d = np.load(path)
    X = d["X"].astype(np.float32)
    mask, y, reward, match_id = d["mask"], d["y"].astype(np.int64), d["reward"], d["match_id"]
    strong = np.ones(len(y), dtype=bool)
    if min_rating > 0 and "rating" in d:
        strong = d["rating"] >= min_rating
        print(f"keeping {int(strong.sum())} of {len(strong)} rows from sides rated {min_rating:.0f}+")
    # Hold out whole matches so the test score is not inflated by neighbouring turns.
    ids = np.unique(match_id)
    rng = np.random.default_rng(0)
    rng.shuffle(ids)
    test_ids = set(ids[: max(1, int(len(ids) * holdout_fraction))].tolist())
    test = np.array([m in test_ids for m in match_id])
    # Replay data: a label the bot's own mask forbids (an inferred mode with no candidate, for
    # example capture with no camp in reach) cannot be learnt and only drags the legal logits down.
    legal = mask[np.arange(len(y)), y]
    if not legal.all():
        print(f"dropping {int((~legal).sum())} rows whose label is a masked mode")
    legal &= strong
    if not legal.all():
        X, mask, y, reward, test = X[legal], mask[legal], y[legal], reward[legal], test[legal]
        global _LEGAL
        _LEGAL = legal
    return X, mask, y, reward, test


def advantage_weights(reward, y, beta, clip_hi):
    """exp(beta * standardised advantage), with the baseline taken per chosen mode so a mode that
    is usually chosen in bad spots (flee) is not punished for the spot."""
    adv = np.zeros_like(reward)
    for mode in range(N_MODES):
        sel = y == mode
        if sel.any():
            r = reward[sel]
            adv[sel] = (r - r.mean()) / (r.std() + 1e-6)
    w = np.exp(beta * adv)
    return np.clip(w, 0.2, clip_hi).astype(np.float32)


def train(X, mask, y, w, test, epochs, lr, batch, seed, logit_l2=0.0, smoothing=0.0):
    torch.manual_seed(seed)
    net = ModeNet(X.shape[1])
    opt = torch.optim.Adam(net.parameters(), lr=lr, weight_decay=1e-4)
    Xt, Mt, yt, wt = (torch.tensor(X[~test]), torch.tensor(mask[~test]), torch.tensor(y[~test]), torch.tensor(w[~test]))
    Xv, Mv, yv = torch.tensor(X[test]), torch.tensor(mask[test]), torch.tensor(y[test])
    n = len(Xt)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, epochs)
    bar = Progress(epochs, "epochs")
    for epoch in range(epochs):
        net.train()
        perm = torch.randperm(n)
        total = 0.0
        for i in range(0, n, batch):
            idx = perm[i:i + batch]
            raw = net(Xt[idx])
            logits = raw.masked_fill(~Mt[idx], MASK_NEG)
            # Softmax ignores the logits' offset and scale, but int8 export does not: penalise the
            # mean logit and (optionally) their magnitude, and smooth the labels so noisy ones do
            # not drive the margins to infinity.
            # Penalties act on the legal modes only: a masked mode gets no cross-entropy gradient,
            # so a penalty over all modes would be satisfied by pushing masked logits anywhere.
            m = Mt[idx]
            ce = F.cross_entropy(logits, yt[idx], reduction="none", label_smoothing=smoothing)
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
            print(f"epoch {epoch:3d} ce {total / n:.4f} train acc {accuracy(net, Xt, Mt, yt):.3f} "
                  f"test acc {accuracy(net, Xv, Mv, yv):.3f} {logit_stats(net, Xv[:5000])}")
    bar.done()
    return net


@torch.no_grad()
def logit_stats(net, X):
    net.eval()
    if len(X) == 0:
        return ""
    raw = net(X)
    return f"logits: row mean {raw.mean(1).abs().mean():.1f} row std {raw.std(1).mean():.1f} max {raw.abs().max():.0f}"


@torch.no_grad()
def accuracy(net, X, M, y):
    net.eval()
    if len(X) == 0:
        return float("nan")
    pred = net(X).masked_fill(~M, MASK_NEG).argmax(1)
    return (pred == y).float().mean().item()


# ---- quantisation ----------------------------------------------------------------------------

def quantise(net, X_calib):
    """Int8 weights, uint8 activations, integer requantisation; returns the spec for nn.from_spec
    and the float-equivalent score scale of the last layer."""
    net.eval()
    layers = [l for l in net.layers]
    spec_layers = []
    x = torch.tensor(X_calib) * INPUT_SCALE   # the model's own input scaling
    in_scale = INPUT_SCALE              # float value represented by one raw input unit
    with torch.no_grad():
        for index, layer in enumerate(layers):
            W = layer.weight.numpy().astype(np.float64)
            b = layer.bias.numpy().astype(np.float64)
            w_scale = max(np.abs(W).max() / 127.0, 1e-8)
            Wq = np.clip(np.round(W / w_scale), -127, 127).astype(np.int64)
            # Accumulator unit = w_scale * in_scale float units; the bias shares it.
            acc_unit = w_scale * in_scale
            bq = np.clip(np.round(b / acc_unit), -(1 << 23), (1 << 23) - 1).astype(np.int64)
            last = index == len(layers) - 1
            h = layer(x)
            if last:
                spec_layers.append({"n_in": W.shape[1], "n_out": W.shape[0], "weights": Wq, "bias": bq,
                                    "mult": 1, "shift": 0})
                score_unit = acc_unit     # float logit per integer score unit
                break
            h = F.relu(h)
            # uint8 activation scale from the calibration data (99.9th percentile keeps outliers
            # from crushing the resolution).
            hi = float(np.percentile(h.numpy(), 99.9)) if h.numel() else 1.0
            act_scale = max(hi / 255.0, 1e-6)
            ratio = acc_unit / act_scale  # activations = acc * ratio
            shift = 16
            mult = int(round(ratio * (1 << shift)))
            while mult > (1 << 30) and shift > 0:
                shift -= 1
                mult = int(round(ratio * (1 << shift)))
            spec_layers.append({"n_in": W.shape[1], "n_out": W.shape[0], "weights": Wq, "bias": bq,
                                "mult": mult, "shift": shift})
            # Feed the next layer what the integer network will produce, in float units.
            x = torch.tensor(np.clip(np.round(h.numpy() / act_scale), 0, 255).astype(np.float32) * act_scale)
            in_scale = act_scale
    return spec_layers, score_unit


def int_forward(spec_layers, x):
    """Numpy reference for the integer network (same arithmetic as nn.py)."""
    x = np.asarray(x, dtype=np.int64)
    for index, layer in enumerate(spec_layers):
        acc = layer["weights"] @ x + layer["bias"]
        if index == len(spec_layers) - 1:
            return acc
        x = np.clip((np.maximum(acc, 0) * layer["mult"]) >> layer["shift"], 0, 255)
    return x


def export(spec_layers, score_unit, margin_logits, path, note):
    margin = int(round(margin_logits / score_unit))
    weights_io.write_head(path, "MODE_SELECTOR", spec_layers, margin,
                          "integer score gap equal to %s logits; below it the script decides" % margin_logits, note)


def main():
    global _LEGAL
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("data")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "bots", "hybrid", "src", "weights.py"))
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--batch", type=int, default=512)
    ap.add_argument("--beta", type=float, default=0.5, help="advantage weighting strength (0 = plain imitation)")
    ap.add_argument("--clip", type=float, default=4.0, help="maximum example weight")
    ap.add_argument("--holdout", type=float, default=0.15, help="fraction of matches held out")
    ap.add_argument("--margin", type=float, default=0.5, help="logit gap below which the script decides")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--logit-l2", type=float, default=0.0, help="penalty on squared logits (bounds their scale for int8 export)")
    ap.add_argument("--smoothing", type=float, default=0.0, help="label smoothing")
    ap.add_argument("--no-export", action="store_true")
    ap.add_argument("--winners", action="store_true", help="replay data: weight the winning/higher-rated side up")
    ap.add_argument("--loser-weight", type=float, default=0.3)
    ap.add_argument("--min-rating", type=float, default=0.0, help="replay data: keep only rows of sides rated at least this")
    ap.add_argument("--extra", nargs="*", default=[], help="more npz files to train on, as path or path:weight (self-play rows next to the ladder base)")
    args = ap.parse_args()

    X, mask, y, reward, test = load(args.data, args.holdout, args.min_rating)
    w = advantage_weights(reward, y, args.beta, args.clip)
    d = np.load(args.data)
    if args.winners and "won" in d:
        # Replay data: learn mostly from the side that won (and from the higher-rated side).
        won = d["won"][_LEGAL]; rating = d["rating"][_LEGAL]
        side = np.where(won > 0, 1.0, args.loser_weight).astype(np.float32)
        if (rating > 0).any():
            side *= np.clip(rating / max(rating.max(), 1.0), 0.5, 1.0)
        w = (w * side).astype(np.float32)
    for extra in args.extra:
        # Self-play rows: their own advantage weights (per mode, within the file) times a multiplier,
        # legal labels only, a fresh match-level holdout.
        path, mult = extra, ""
        if ":" in extra and extra.rsplit(":", 1)[1].replace(".", "", 1).isdigit():
            path, mult = extra.rsplit(":", 1)
        _LEGAL = slice(None)
        Xe, me, ye, re_, te = load(path, args.holdout, 0.0)
        we = advantage_weights(re_, ye, args.beta, args.clip) * float(mult or 1.0)
        print(f"extra {os.path.basename(path)}: {len(Xe)} rows at weight x{float(mult or 1.0):.2f}")
        X = np.concatenate([X, Xe]); mask = np.concatenate([mask, me]); y = np.concatenate([y, ye])
        w = np.concatenate([w, we.astype(np.float32)]); test = np.concatenate([test, te])
    print(f"{len(X)} rows, {X.shape[1]} features, {int(test.sum())} held out; weights mean {w.mean():.2f}")
    net = train(X, mask, y, w, test, args.epochs, args.lr, args.batch, args.seed, args.logit_l2, args.smoothing)

    spec, score_unit = quantise(net, X[~test][:20000])
    # Integer network vs float network agreement on the held-out matches.
    Xv, Mv, yv = X[test], mask[test], y[test]
    with torch.no_grad():
        float_pred = net(torch.tensor(Xv)).masked_fill(~torch.tensor(Mv), MASK_NEG).argmax(1).numpy()
    int_pred = np.array([np.where(m, int_forward(spec, x), -(1 << 40)).argmax() for x, m in zip(Xv, Mv)])
    print(f"quantised: agrees with float on {100 * (int_pred == float_pred).mean():.1f}% of held-out turns, "
          f"matches the script on {100 * (int_pred == yv).mean():.1f}%; one logit = {1 / score_unit:.1f} score units")
    if not args.no_export:
        note = ("Trained %s on %d rows (beta %.2f, %d epochs); held-out agreement with the script %.1f%%."
                % (time.strftime("%Y-%m-%d %H:%M"), int((~test).sum()), args.beta, args.epochs, 100 * (int_pred == yv).mean()))
        export(spec, score_unit, args.margin, args.out, note)
        print("wrote", args.out)


if __name__ == "__main__":
    main()
