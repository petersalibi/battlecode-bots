# Fuel benchmark results

Measured 2026-10-19 with `cambc run fuelbench starter-python --map corners --release` (cambc 2.2.5, repo `sdk/` still at 2.1.2).
Champion turns get 2.5M fuel (the first champion turn about 4.6M). Turning the garbage collector off made no measurable difference.

| Benchmark | Fuel per call | Fuel per multiply-add |
|---|---|---|
| Empty Python loop iteration + call | ~1,000 | |
| Float layer 32x32, plain loops | 1.19M | 1,165 |
| Float layer 32x32, `sum(map(mul))` per row | 0.85M | 833 |
| Int layer 128x64, `sum(map(mul))` per row | 8.13M | 992 |
| Packed int8 64x32 | 0.37M | 180 |
| Packed int8 128x64 | 1.00M | 122 |
| Packed int8 256x128 | 3.21M | 98 |
| Packed int8 128x64, half the inputs zero (compress) | 0.65M | 160 per non-zero MAC |
| Mode selector 64-32-16-6, full forward + argmax | 0.55M | 208 |
| `cambc.me()` | 1,600 | |
| `me().position()` | 3,400 | |
| `me().health()` | 3,200 | |
| `cambc.round_number()` | 1,400 | |
| `vision.champions(ENEMY)` | 11,000 | |
| `vision.camps()` | 23,000 | |
| `game_map.camp_groups()` | 600,000 | |
| Blocked-tiles bitboard (5 unions) | 32,000 | |
| `DistanceField.compute_to` to altars, whole map | 64,000 | |
| Shared memory read, 8 bytes | 9,300 | |
| Draft memory read, `bytes(DraftMemoryBuffer())` | 10,400 | |

A benchmark that ran past a turn's fuel was suspended and resumed next round; those are counted as whole 2.5M turns plus the partial ones.

Re-run: `cambc run fuelbench starter-python --release --rounds 80`, then unzip the `.repl26a` in `replays/` and read `team-a.stdout`.
