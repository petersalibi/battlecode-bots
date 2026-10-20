"""Packed int8 inference engine. Pure Python; weights are packed at import, which runs at build time.

Each layer stores one big integer per input holding that input's weight to every output in its own
32-bit lane (offset by +128 to stay positive). A layer is then one C-level sum(map(mul)).
Checked against a reference dot product in bots/fuelbench/src/benches.py.
"""
from array import array
from itertools import compress
from operator import mul

LANE_OFFSET = 1 << 24
LANE_BITS = 32


class PackedLayer:
    """int8 weights, uint8 inputs, 32-bit accumulators, integer requantisation."""

    def __init__(self, weights, bias, mult=1, shift=10, last=False):
        self.n_out = len(weights)
        self.n_in = len(weights[0])
        self.cols = [sum((weights[j][i] + 128) << (LANE_BITS * j) for j in range(self.n_out))
                     for i in range(self.n_in)]
        self.bias = sum((bias[j] + LANE_OFFSET) << (LANE_BITS * j) for j in range(self.n_out))
        self.mult, self.shift, self.last = mult, shift, last
        self.nbytes = 4 * self.n_out

    def forward(self, x):
        """x: list of ints 0-255. Returns uint8 activations, or raw integer scores if last."""
        if len(x) != self.n_in:
            raise ValueError(f"expected {self.n_in} inputs, got {len(x)}")
        # Zero activations and their columns are dropped before the multiply.
        acc = sum(map(mul, compress(x, x), compress(self.cols, x)), self.bias)
        lanes = array("I", acc.to_bytes(self.nbytes, "little"))
        off = 128 * sum(x) + LANE_OFFSET
        if self.last:
            return [v - off for v in lanes]
        m, s = self.mult, self.shift
        return [0 if v <= off else min(255, ((v - off) * m) >> s) for v in lanes]


class Network:
    def __init__(self, layers):
        self.layers = layers
        self.n_in = layers[0].n_in
        self.n_out = layers[-1].n_out

    def forward(self, x):
        for layer in self.layers:
            x = layer.forward(x)
        return x


def from_spec(spec):
    """Build a Network from the export format in weights.py.

    spec = {"layers": [{"n_in": .., "n_out": .., "weights": bytes (row-major int8 stored unsigned),
                        "bias": [int, ...], "mult": int, "shift": int}, ...]}
    """
    if spec is None:
        return None
    layers = []
    last_index = len(spec["layers"]) - 1
    for index, layer in enumerate(spec["layers"]):
        n_in, n_out = layer["n_in"], layer["n_out"]
        raw = layer["weights"]
        rows = [[(b - 256 if b > 127 else b) for b in raw[r * n_in:(r + 1) * n_in]] for r in range(n_out)]
        layers.append(PackedLayer(rows, list(layer["bias"]), layer.get("mult", 1), layer.get("shift", 10),
                                  last=index == last_index))
    return Network(layers)


def masked_argmax(scores, mask):
    """Index of the best score among options where mask is true; None when nothing is allowed."""
    best, best_score = None, None
    for index, allowed in enumerate(mask):
        if allowed and (best is None or scores[index] > best_score):
            best, best_score = index, scores[index]
    return best


def margin(scores, mask):
    """Gap between the best and second best allowed scores (0 when fewer than two are allowed)."""
    allowed = sorted((scores[i] for i, ok in enumerate(mask) if ok), reverse=True)
    return allowed[0] - allowed[1] if len(allowed) >= 2 else 0
