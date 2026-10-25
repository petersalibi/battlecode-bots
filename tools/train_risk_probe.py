"""Train the death-risk head as a linear probe on the shipped mode selector's penultimate
activations, and append it as a seventh output lane, leaving the six mode lanes bit-identical.

    python tools/train_risk_probe.py data/replays/1.0.1/modes25_all.npz --threshold 0.5

The label is died20 from tools/replay_features.py (the body died within 20 rounds of the turn),
on every side of every game (losers supply the deaths). The probe sees the selector's 16 uint8
hidden activations, reconstructed with the integer arithmetic of nn.py from the exported spec,
so training and play see exactly the same inputs. The probe's integer row has its own scale
(the lane is only ever compared with the risk threshold, never with the mode lanes), chosen so
its weights fill the int8 range. The export rewrites MODE_SELECTOR_SPEC with n_out 7 and adds
MODE_SELECTOR_RISK (the threshold in the lane's score units) and MODE_SELECTOR_RISK_STAY (the
lower threshold that keeps a risk-fleeing body fleeing).
"""
import argparse
import math
import os
import sys
import time

import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weights_io  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bots", "hybrid", "src"))
import weights  # noqa: E402


def layers_of(spec):
    out = []
    for l in spec["layers"]:
        raw = np.frombuffer(l["weights"], dtype=np.uint8).astype(np.int64)
        out.append({"weights": np.where(raw > 127, raw - 256, raw).reshape(l["n_out"], l["n_in"]),
                    "bias": np.array(l["bias"], dtype=np.int64), "mult": l["mult"], "shift": l["shift"],
                    "n_in": l["n_in"], "n_out": l["n_out"]})
    return out


def hidden(layers, X):
    """uint8 activations after the hidden layers (the last layer's input), nn.py arithmetic."""
    x = X.astype(np.int64)
    for layer in layers[:-1]:
        acc = x @ layer["weights"].T + layer["bias"]
        x = np.clip((np.maximum(acc, 0) * layer["mult"]) >> layer["shift"], 0, 255)
    return x


def auc(p, y):
    order = np.argsort(p)
    ranks = np.empty(len(p)); ranks[order] = np.arange(1, len(p) + 1)
    pos = y > 0.5
    n_pos, n_neg = pos.sum(), (~pos).sum()
    return (ranks[pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg) if n_pos and n_neg else float("nan")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("data")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "bots", "hybrid", "src", "weights.py"))
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--lr", type=float, default=1e-2)
    ap.add_argument("--holdout", type=float, default=0.15)
    ap.add_argument("--threshold", type=float, default=0.5, help="death probability above which the bot stops farming and fighting")
    ap.add_argument("--stay", type=float, default=0.3, help="probability below which a risk-fleeing body may resume")
    ap.add_argument("--flag-rate", type=float, help="set the threshold so this fraction of held-out turns is flagged (overrides --threshold)")
    ap.add_argument("--stay-rate", type=float, default=0.10, help="with --flag-rate: the fraction of turns above the resume threshold")
    ap.add_argument("--no-export", action="store_true")
    args = ap.parse_args()

    spec = weights.MODE_SELECTOR_SPEC
    layers = layers_of(spec)
    if layers[-1]["n_out"] != 6:
        print(f"the selector already has {layers[-1]['n_out']} outputs: re-export the six-lane selector first")
        sys.exit(1)
    d = np.load(args.data)
    X, y, match_id = d["X"], d["died20"].astype(np.float32), d["match_id"]
    ids = np.unique(match_id); rng = np.random.default_rng(0); rng.shuffle(ids)
    test = np.isin(match_id, ids[: max(1, int(len(ids) * args.holdout))])
    H = hidden(layers, X)
    print(f"{len(X)} rows, {int(test.sum())} held out; died within 20 rounds: {y.mean():.3%}; hidden activations mean {H.mean():.1f}")
    Ht, yt = torch.tensor(H[~test] / 255.0, dtype=torch.float32), torch.tensor(y[~test])
    Hv, yv = torch.tensor(H[test] / 255.0, dtype=torch.float32), y[test]
    w = torch.zeros(H.shape[1], requires_grad=True); b = torch.zeros(1, requires_grad=True)
    opt = torch.optim.Adam([w, b], lr=args.lr)
    pos_weight = torch.tensor((1 - yt.mean()) / yt.mean())
    n = len(Ht)
    for epoch in range(args.epochs):
        perm = torch.randperm(n)
        for i in range(0, n, 4096):
            idx = perm[i:i + 4096]
            logit = Ht[idx] @ w + b
            loss = F.binary_cross_entropy_with_logits(logit, yt[idx], pos_weight=pos_weight)
            opt.zero_grad(); loss.backward(); opt.step()
        with torch.no_grad():
            pv = torch.sigmoid(Hv @ w + b).numpy()
        if epoch % 5 == 0 or epoch == args.epochs - 1:
            print(f"epoch {epoch:3d} held-out auc {auc(pv, yv):.3f}; flagged at p>={args.threshold}: {np.mean(pv >= args.threshold):.1%} of turns, "
                  f"catching {np.mean(pv[yv > 0.5] >= args.threshold):.1%} of deaths")
    # The probe is trained with a positive-class weight, so its p=0.5 is nowhere near a true 50%
    # death rate: with --flag-rate the thresholds are set by how many turns they flag instead.
    if args.flag_rate is not None:
        args.threshold = float(np.quantile(pv, 1 - args.flag_rate))
        args.stay = float(np.quantile(pv, 1 - args.stay_rate))
        print(f"thresholds from flag rates: p>={args.threshold:.3f} flags {args.flag_rate:.1%} of turns and catches "
              f"{np.mean(pv[yv > 0.5] >= args.threshold):.1%} of deaths; stay p>={args.stay:.3f} ({args.stay_rate:.1%} of turns)")
    # Integer lane: weights per raw uint8 input, scaled to fill int8; threshold in the same units.
    wf = (w.detach().numpy() / 255.0).astype(np.float64)
    bf = float(b.detach().numpy()[0])
    scale = 127.0 / max(np.abs(wf).max(), 1e-9)
    wq = np.clip(np.round(wf * scale), -127, 127).astype(np.int64)
    bq = int(round(bf * scale))
    logit_t = math.log(args.threshold / (1 - args.threshold)); logit_s = math.log(args.stay / (1 - args.stay))
    T, S = int(round(logit_t * scale)), int(round(logit_s * scale))
    # Check the integer lane against the float probe on held-out rows.
    int_logit = (H[test].astype(np.int64) @ wq + bq) / scale
    with torch.no_grad():
        fl = (Hv @ w + b).numpy()
    print(f"integer lane: scale {scale:.1f} per logit, agrees with float on {np.mean((int_logit >= logit_t) == (fl >= logit_t)):.1%} of threshold decisions; "
          f"mean abs logit error {np.abs(int_logit - fl).mean():.3f}")
    if args.no_export:
        return
    last = layers[-1]
    new_last = {"n_in": last["n_in"], "n_out": 7, "mult": 1, "shift": 0,
                "weights": np.concatenate([last["weights"], wq[None, :]], axis=0),
                "bias": np.concatenate([last["bias"], [bq]])}
    spec_layers = [{"n_in": l["n_in"], "n_out": l["n_out"], "mult": l["mult"], "shift": l["shift"], "weights": l["weights"], "bias": l["bias"]}
                   for l in layers[:-1]] + [new_last]
    margin = weights.MODE_SELECTOR_MARGIN
    note = ("risk lane added %s: linear probe on the hidden activations, %d rows, held-out auc %.3f, flags %.1f%% of turns at p>=%.2f"
            % (time.strftime("%Y-%m-%d %H:%M"), int((~test).sum()), auc(pv, yv), 100 * np.mean(pv >= args.threshold), args.threshold))
    weights_io.write_head(args.out, "MODE_SELECTOR", spec_layers, margin,
                          "integer score gap equal to 0.5 logits; below it the script decides", note)
    weights_io.write_line(args.out, "MODE_SELECTOR_RISK", T, "risk lane score at p=%.2f: above it farm, capture and fight are masked" % args.threshold)
    weights_io.write_line(args.out, "MODE_SELECTOR_RISK_STAY", S, "risk lane score at p=%.2f: a risk-fleeing body resumes below it" % args.stay)
    print("wrote", args.out)


if __name__ == "__main__":
    main()
