"""Check that the bot's packed-integer inference (bots/hybrid/src/nn.py) matches a plain numpy
integer reference bit for bit, for every head in weights.py, on random inputs and on real
feature rows when a dataset is given.

    python tools/parity_test.py [data/modes.npz]

Exits non-zero on any mismatch. Run before shipping new weights.
"""
import importlib
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bots", "hybrid", "src"))
import weights  # noqa: E402  (imports nn, no SDK)


def reference(spec, x):
    x = np.asarray(x, dtype=np.int64)
    layers = spec["layers"]
    for index, layer in enumerate(layers):
        n_in, n_out = layer["n_in"], layer["n_out"]
        raw = np.frombuffer(layer["weights"], dtype=np.uint8).astype(np.int64)
        W = np.where(raw > 127, raw - 256, raw).reshape(n_out, n_in)
        acc = W @ x + np.asarray(layer["bias"], dtype=np.int64)
        if index == len(layers) - 1:
            return acc
        x = np.clip((np.maximum(acc, 0) * layer["mult"]) >> layer["shift"], 0, 255)
    return x


def check(name, net, spec, inputs):
    bad = 0
    for x in inputs:
        got = net.forward([int(v) for v in x])
        want = reference(spec, x)
        if list(got) != [int(v) for v in want]:
            bad += 1
            if bad <= 3:
                print(f"{name}: mismatch\n  nn.py  {list(got)}\n  numpy  {[int(v) for v in want]}")
    print(f"{name}: {len(inputs) - bad}/{len(inputs)} inputs match")
    return bad == 0


def main():
    ok = True
    rng = np.random.default_rng(1)
    rows = None
    if len(sys.argv) > 1:
        rows = np.load(sys.argv[1])["X"]
    heads = [("MODE_SELECTOR", weights.MODE_SELECTOR, weights.MODE_SELECTOR_SPEC),
             ("CAMP_SCORER", weights.CAMP_SCORER, weights.CAMP_SCORER_SPEC)]
    for name, net, spec in heads:
        if net is None:
            print(f"{name}: no weights")
            continue
        n_in = net.n_in
        inputs = [rng.integers(0, 256, n_in) for _ in range(500)]
        inputs += [np.zeros(n_in, dtype=np.int64), np.full(n_in, 255, dtype=np.int64)]
        if rows is not None and rows.shape[1] == n_in:
            inputs += list(rows[rng.choice(len(rows), min(2000, len(rows)), replace=False)])
        ok &= check(name, net, spec, inputs)
    print("PARITY OK" if ok else "PARITY FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    importlib.invalidate_caches()
    main()
