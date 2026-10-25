"""Benchmarks for inference methods and SDK calls. Weights are built at import, which runs at build time."""
from array import array
from itertools import compress
from operator import mul

import _cambc
import cambc
from cambc import game_map

fuel = _cambc.fuel_remaining
TARGET = 900_000  # fuel to spend per benchmark turn
_noop_per_rep = 0


def _rng(seed):
    state = seed
    while True:
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        yield state >> 8


def _matrix(n_out, n_in, seed, lo=-127, hi=127):
    g = _rng(seed)
    span = hi - lo + 1
    return [[lo + next(g) % span for _ in range(n_in)] for _ in range(n_out)]


def _vector(n, seed, lo=0, hi=255):
    g = _rng(seed)
    return [lo + next(g) % (hi - lo + 1) for _ in range(n)]


LANE_OFFSET = 1 << 24


class PackedLayer:
    """int8 weights, uint8 inputs, 32-bit lanes; one sum(map(mul)) per layer."""

    def __init__(self, weights, bias, mult=1, shift=10, last=False):
        self.n_out = len(weights)
        n_in = len(weights[0])
        self.cols = [sum((weights[j][i] + 128) << (32 * j) for j in range(self.n_out)) for i in range(n_in)]
        self.bias = sum((bias[j] + LANE_OFFSET) << (32 * j) for j in range(self.n_out))
        self.mult, self.shift, self.last = mult, shift, last
        self.nbytes = 4 * self.n_out

    def forward(self, x):
        acc = sum(map(mul, x, self.cols), self.bias)
        lanes = array('I', acc.to_bytes(self.nbytes, 'little'))
        off = 128 * sum(x) + LANE_OFFSET
        if self.last:
            return [v - off for v in lanes]
        m, s = self.mult, self.shift
        return [0 if v <= off else min(255, ((v - off) * m) >> s) for v in lanes]

    def forward_sparse(self, x):
        # Drop zero activations and their columns before multiplying.
        acc = sum(map(mul, compress(x, x), compress(self.cols, x)), self.bias)
        lanes = array('I', acc.to_bytes(self.nbytes, 'little'))
        off = 128 * sum(x) + LANE_OFFSET
        m, s = self.mult, self.shift
        return [0 if v <= off else min(255, ((v - off) * m) >> s) for v in lanes]


def _packed(n_in, n_out, seed, last=False):
    return PackedLayer(_matrix(n_out, n_in, seed), _vector(n_out, seed + 1, -2000, 2000), last=last)


# Float layers (32 -> 32).
_FW = [[w / 127.0 for w in row] for row in _matrix(32, 32, 1)]
_FX = [v / 255.0 for v in _vector(32, 2)]
# Int per-row layer (128 -> 64).
_IW = _matrix(64, 128, 3)
_IX = _vector(128, 4)
# Packed layers.
_P64 = _packed(64, 32, 5)
_P128 = _packed(128, 64, 7)
_P256 = _packed(256, 128, 9)
_X64, _X128, _X256 = _vector(64, 11), _vector(128, 12), _vector(256, 13)
_X128_HALF = [v if i % 2 else 0 for i, v in enumerate(_X128)]
# Mode selector 64 -> 32 -> 16 -> 6.
_M1, _M2, _M3 = _packed(64, 32, 21), _packed(32, 16, 23), _packed(16, 6, 25, last=True)


def noop():
    pass


def float_naive():
    out = []
    for row in _FW:
        s = 0.0
        for a, b in zip(row, _FX):
            s += a * b
        out.append(s)
    return out


def float_map():
    return [sum(map(mul, row, _FX)) for row in _FW]


def int_rows():
    return [sum(map(mul, row, _IX)) for row in _IW]


def packed64():
    return _P64.forward(_X64)


def packed128():
    return _P128.forward(_X128)


def packed256():
    return _P256.forward(_X256)


def packed128_sparse_half():
    return _P128.forward_sparse(_X128_HALF)


def mode_selector():
    scores = _M3.forward(_M2.forward(_M1.forward(_X64)))
    return max(range(6), key=scores.__getitem__)


# Camp scorer candidates: bilinear (state tower 20 -> 16 -> 22, then one dot per candidate) against
# the factored MLP (42 -> 32 -> 16 -> 1 per candidate), twelve candidates each.
_CS = _vector(20, 31)
_CC = [_vector(22, 40 + i) for i in range(12)]
_T1, _T2 = _packed(20, 16, 33), _packed(16, 22, 35, last=True)
_F1, _F2, _F3 = _packed(42, 32, 37), _packed(32, 16, 39), _packed(16, 1, 41, last=True)


_T0 = _packed(20, 22, 43, last=True)
_DOT_LANE, _DOT_BIAS = 48, 1 << 44
_DOT_SHIFT, _DOT_MASK = _DOT_LANE * 21, (1 << _DOT_LANE) - 1
_DOT_ADD = sum(_DOT_BIAS << (_DOT_LANE * k) for k in range(43))


def camp_bilinear_12():
    w = _T2.forward(_T1.forward(_CS))
    return [sum(map(mul, w, c)) for c in _CC]


def camp_bilinear_packed_12():
    w = _T0.forward(_CS)
    packed = sum(v << (_DOT_LANE * (21 - j)) for j, v in enumerate(w))
    out = []
    for c in _CC:
        buf = bytearray(6 * 22)
        buf[::6] = bytes(c)
        out.append((((packed * int.from_bytes(buf, "little") + _DOT_ADD) >> _DOT_SHIFT) & _DOT_MASK) - _DOT_BIAS)
    return out


def camp_factored_12():
    out = []
    for c in _CC:
        out.append(_F3.forward(_F2.forward(_F1.forward(_CS + c)))[0])
    return out


def sdk_me():
    return cambc.me()


def sdk_position():
    return cambc.me().position()


def sdk_health():
    return cambc.me().health()


def sdk_enemies():
    return cambc.vision.champions(cambc.Team.ENEMY)


def sdk_camps():
    return cambc.vision.camps()


def sdk_camp_groups():
    return game_map.camp_groups()


def sdk_blocked_bitboard():
    return (game_map.walls() | game_map.holes() | game_map.altars()
            | cambc.vision.champion_tiles() | cambc.vision.camp_tiles())


def sdk_distance_field():
    blocked = game_map.walls() | game_map.holes()
    goals = game_map.altars()
    field = cambc.DistanceField()
    field.compute_to(blocked & ~goals, goals, cambc.Bitboard(), cambc.Bitboard())
    return field


def sdk_shared_read8():
    buf = bytearray(8)
    cambc.shared_memory.read(0, buf)
    return buf


def sdk_draft_memory():
    return bytes(cambc.DraftMemoryBuffer())


def sdk_round():
    return cambc.round_number()


# (name, function, max reps, multiply-adds per call or 0)
BENCHES = [
    ("noop_loop", noop, 2000, 0),
    ("float_naive_32x32", float_naive, 200, 32 * 32),
    ("float_map_32x32", float_map, 200, 32 * 32),
    ("int_rows_128x64", int_rows, 200, 128 * 64),
    ("packed_64x32", packed64, 200, 64 * 32),
    ("packed_128x64", packed128, 200, 128 * 64),
    ("packed_256x128", packed256, 200, 256 * 128),
    ("packed_128x64_half_zero", packed128_sparse_half, 200, 64 * 64),
    ("mode_selector_64_32_16_6", mode_selector, 200, 64 * 32 + 32 * 16 + 16 * 6),
    ("camp_bilinear_20_16_22_x12", camp_bilinear_12, 200, 20 * 16 + 16 * 22 + 12 * 22),
    ("camp_bilinear_packed_20_22_x12", camp_bilinear_packed_12, 200, 20 * 22 + 12 * 22),
    ("camp_factored_42_32_16_1_x12", camp_factored_12, 50, 12 * (42 * 32 + 32 * 16 + 16)),
    ("sdk_me", sdk_me, 500, 0),
    ("sdk_position", sdk_position, 500, 0),
    ("sdk_health", sdk_health, 500, 0),
    ("sdk_vision_enemies", sdk_enemies, 500, 0),
    ("sdk_vision_camps", sdk_camps, 500, 0),
    ("sdk_camp_groups", sdk_camp_groups, 200, 0),
    ("sdk_blocked_bitboard", sdk_blocked_bitboard, 200, 0),
    ("sdk_distance_field_altars", sdk_distance_field, 50, 0),
    ("sdk_shared_memory_read8", sdk_shared_read8, 500, 0),
    ("sdk_draft_memory_bytes", sdk_draft_memory, 200, 0),
    ("sdk_round_number", sdk_round, 500, 0),
]


TURN_FUEL = 2_500_000


def stamp():
    return _cambc.round_number(), fuel()


def spent(r0, f0, r1, f1):
    return (r1 - r0) * TURN_FUEL + f0 - f1


def run(name, fn, max_reps, macs):
    global _noop_per_rep
    # Fuel-remaining pair overhead.
    pair = spent(*stamp(), *stamp())
    # One warm-up call sizes the rep count; keep the timed loop well inside this turn's fuel.
    # A call that runs out of fuel is suspended and resumes next round, so spans count whole turns.
    r0, start = stamp()
    fn()
    once = spent(r0, start, *stamp()) - pair
    suspended = _cambc.round_number() != r0
    left = fuel() - 400_000
    reps = 0 if suspended else max(0, min(max_reps, TARGET // max(once, 1), left // max(once, 1)))
    if reps == 0:
        total, reps, timed = once, 1, "warmup"
    else:
        r1, f0 = stamp()
        for _ in range(reps):
            fn()
        total = spent(r1, f0, *stamp()) - pair
        timed = "loop"
        suspended = suspended or _cambc.round_number() != r1
    per_call = total / reps
    if name == "noop_loop":
        _noop_per_rep = per_call
    net = per_call - _noop_per_rep if name != "noop_loop" else per_call
    line = (f"BENCH {name} round={r0} start={start} reps={reps} ({timed}) once={once} "
            f"fuel_per_call={net:.0f} pair={pair}{' SUSPENDED' if suspended else ''}")
    if macs:
        line += f" macs={macs} fuel_per_mac={net / macs:.1f}"
    print(line, flush=True)
