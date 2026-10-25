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
        return self.finish(self.accumulate(x), sum(x))

    def accumulate(self, x, start=0):
        """Multiply-accumulate of an input slice against the columns from `start`, without the
        bias: a layer's input can be summed in pieces (a per-turn part plus a per-candidate part)."""
        cols = self.cols if start == 0 and len(x) == self.n_in else self.cols[start:start + len(x)]
        # Zero activations and their columns are dropped before the multiply.
        return sum(map(mul, compress(x, x), compress(cols, x)))

    def finish(self, acc, total):
        """Requantise an accumulator (sum of accumulate() pieces); total is the sum of the inputs."""
        lanes = array("I", (acc + self.bias).to_bytes(self.nbytes, "little"))
        off = 128 * total + LANE_OFFSET
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


class Factored:
    """A candidate scorer: the first layer's input is [state | candidate], the state part is
    accumulated once per call and the candidate part once per candidate, so ranking twelve
    candidates costs a little more than one forward pass. The last layer has one output."""

    def __init__(self, net, n_state):
        self.net = net
        self.n_state = n_state
        self.n_in = net.n_in
        self.n_cand = net.n_in - n_state

    def score(self, state, candidates):
        """state: n_state ints 0-255; candidates: list of n_cand-int lists. Returns one integer
        score per candidate (higher is better)."""
        if len(state) != self.n_state:
            raise ValueError(f"expected {self.n_state} state inputs, got {len(state)}")
        first = self.net.layers[0]
        rest = self.net.layers[1:]
        base = first.accumulate(state, 0)
        state_sum = sum(state)
        out = []
        for cand in candidates:
            if len(cand) != self.n_cand:
                raise ValueError(f"expected {self.n_cand} candidate inputs, got {len(cand)}")
            h = first.finish(base + first.accumulate(cand, self.n_state), state_sum + sum(cand))
            for layer in rest:
                h = layer.forward(h)
            out.append(h[0])
        return out

    def forward(self, x):
        """Plain forward on a concatenated [state | candidate] row (the parity test uses it)."""
        return self.net.forward(x)


class Bilinear:
    """A candidate scorer whose per-candidate cost is one C-level dot product. The layers run on
    the state vector alone (once per call) and their last output is a weight per candidate
    feature; a candidate's score is that weight vector dotted with its feature vector. Every
    requantised lane costs about 10K fuel in Python, so unlike Factored nothing per candidate is
    requantised: twelve candidates cost the state tower plus about 12 x 3K."""

    def __init__(self, net, n_state):
        self.net = net
        self.n_state = n_state
        self.n_cand = net.n_out
        self.n_in = n_state + net.n_out

    def score(self, state, candidates):
        """About 35K fuel per candidate (22 C-level multiplies plus the sum). Packing the dot into
        one big-integer multiply was measured twice as expensive: big-integer products are charged
        by their size."""
        if len(state) != self.n_state:
            raise ValueError(f"expected {self.n_state} state inputs, got {len(state)}")
        w = self.net.forward(state)
        return [sum(map(mul, w, cand)) for cand in candidates]

    def forward(self, x):
        """One concatenated [state | candidate] row -> [score] (the parity test uses it)."""
        return self.score(x[:self.n_state], [x[self.n_state:]])


def factored(spec):
    """A candidate scorer from a spec that carries "n_state" (and "kind": "bilinear" or the
    default "factored"); None when there are no weights."""
    net = from_spec(spec)
    if net is None:
        return None
    if spec.get("kind") == "bilinear":
        return Bilinear(net, spec["n_state"])
    return Factored(net, spec["n_state"])


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
