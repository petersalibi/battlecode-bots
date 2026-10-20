# Neural Network Guide

How we use neural networks in our Cambridge Battlecode 2026 Python bot. It collects every decision and consideration from our planning so far that is about the networks. General strategy and the non-network parts of the bot are left out. Training is also left out, on purpose.

Sources: the planning docs "Neural Network Bot: Feasibility and Technical Considerations", "Bot Module Plan" and "Mode Selector Inputs" (all 2026-10-19), plus the fuel benchmarks in `bots/fuelbench/RESULTS.md`. The game itself is described in `docs/GameRules.md` and `docs/BotAPIPython.md`.

---

## 1. Verdict

Networks are feasible as **small decision-makers inside a scripted bot**. A network can't be one big policy that reads the map and outputs every move.

- **Feasible:** small MLPs that choose between options (mode, camp target, fight or flee, attack target), run by each champion on its own turn.
- **Feasible:** a set encoder over a capped number of visible entities, at a few hundred multiply-adds per entity, sliced across turns if needed.
- **Feasible:** a draft network of about 150,000 multiply-adds on the first draft turn, which has 10x fuel.
- **Not feasible:** convolutions over the map or a local window, per-tile action outputs, or search that calls a network many times per turn.

**The hybrid rule:** build the scripted bot first, with every decision written as a scoring function in `decide.py`. Then swap a network in behind that same interface, one decision at a time. Keep it only if it beats the scripted version head-to-head. That way the network can only add to the bot. If a network doesn't pan out, we still have a working scripted bot.

## 2. What the runtime allows

Checked against the official toolchain (`cambc` 2.2.5).

| Check | Result |
|---|---|
| Python | CPython 3.13.7 compiled to WASI WebAssembly, metered by fuel |
| `numpy`, `torch`, `scipy` | `ModuleNotFoundError` |
| `ctypes` | Fails, `_ctypes` is missing |
| Standard library | Available, minus about 91 modules the WASI build can't support. `array`, `itertools`, `operator` and `gc` work. |
| Third-party packages | Pure-Python source copied into the bot folder only. Compiled extensions can't load. |
| What gets submitted | The bot's source folder (`cambc submit` / `cambc bundle`). The platform builds it. |
| Module imports | Every bot module except `main.py` is imported at build time and frozen into a snapshot. |

Two consequences shape everything below:

- **Inference is hand-written plain Python.** We use the packed-integer method in section 7. NumPy and PyTorch are only needed for training, on our own machines.
- **Loading weights is free.** Work done at module import happens at build time, so unpacking, packing and precomputing weights all live at module level.

## 3. Fuel budget

- A champion turn has **2.5M fuel**, and the first champion turn about 4.6M. The first draft turn has 25M, and later draft turns less.
- Running out of fuel **suspends the bot**. It resumes next round where it stopped, and the rest of that turn's actions are lost. Never let a network hit that.
- Measure in **release builds** (`cambc run --release`), which match the platform.

### Measured costs (release build, this repo, 2026-10-19)

| What | Fuel |
|---|---|
| Empty Python loop iteration + function call | ~1,000 |
| Float layer, plain loops | ~1,165 per multiply-add |
| Float layer, `sum(map(mul))` per row | ~833 per multiply-add |
| Int layer, `sum(map(mul))` per row | ~990 per multiply-add |
| **Packed int8, 64 → 32** | **~180 per multiply-add (0.37M per layer)** |
| **Packed int8, 128 → 64** | **~122 per multiply-add (1.0M per layer)** |
| **Packed int8, 256 → 128** | **~98 per multiply-add (3.2M per layer)** |
| Packed 128 → 64 with half the inputs zero | ~0.65M (zero-skipping saves about 35%) |
| **Mode selector 64 → 32 → 16 → 6, full forward + argmax** | **~0.55M** |
| `cambc.me()` / `me().position()` / `me().health()` | ~1.6K / 3.4K / 3.2K |
| `vision.champions(ENEMY)` / `vision.camps()` | ~11K / 23K |
| `game_map.camp_groups()` | **~600K, so cache it** |
| `DistanceField.compute_to`, whole map | ~64K |
| Shared memory read (8 bytes) / draft memory read | ~9K / 10K |

Turning the garbage collector off made no measurable difference. The earlier estimates (124 per multiply-add packed, 847 for float `sum(map)`) were confirmed within a few percent. Small layers cost more per multiply-add because each output neuron has a fixed overhead of a few hundred fuel.

**Plain Python is expensive here.** At about 1,000 fuel per loop iteration, scripted logic and feature building compete with the networks for the same budget. Build features from cached state, and keep per-turn Python loops short.

### Network budget per turn

Plan on about **1M fuel per champion turn for networks**, leaving 1.5M for sensing, SDK calls, pathing and a safety margin. A starting split:

| Part | Fuel |
|---|---|
| Mode selector | ~0.55M when it runs (measured). Run it on events, not every turn (section 5). |
| Camp target model, on decision turns | up to ~1M |
| Fight target model, during fights | ~0.3M |
| Everything else (sensing, pathing, margin) | the rest |

**Spend extra fuel on the action models, not the mode selector.** The mode choice is low-dimensional (6 options from 64 summaries), so a bigger mode network mostly adds overfitting risk. Target choices compare many candidates on many factors, and camp choice drives gems per round, which decides most games. The camp model also only runs on decision turns, so it can take a big slice on those turns. The exception is fight micro: if fights turn out to decide games, move fuel there. Treat the split as a starting point. Change one model's width, play a batch of games, and keep the change only if the win rate moves.

Reference sizes at packed int8 rates:

| Network | Shape | Multiply-adds | Fuel | Fits? |
|---|---|---|---|---|
| Mode selector | 64 → 32 → 16 → 6 | ~2,700 | ~0.55M | Yes, on event turns or every turn |
| Candidate scorer, 12 options, factored first layer | state 48 → 32 once, then 16 → 24 → 1 per option | ~6,500 | ~0.8M | Yes, on decision turns |
| Set encoder over 16 entities, then 64 → 32 → 8 head | 12 → 32 → 32 per entity, pooled | ~25,000 | ~3M | Only across 3–4 turns |
| General champion MLP | 128 → 64 → 32 → 8 | ~10,500 | ~1.3M | Barely, on decision turns |
| Draft network | 512 → 256 → 128 → 11 | ~165,000 | ~16M | First draft turn only (25M) |
| One 3x3 conv, 16 filters, 17x17 window, 8 channels | spatial | ~330,000 | ~40M | No |

C++ with WebAssembly SIMD measured about 2.2 fuel per multiply-add but with a 100K-fuel turn, so only about 2x more network per turn. Switching language isn't worth it.

## 4. Where the networks sit

- **Script generates, network chooses.** `candidates.py` lists legal, sensible options (reachable camps, altars, attack targets, retreat tiles) using the SDK's native `DistanceField`, `Bitboard` and `reachable_tiles`. The network only ranks them, so it can never pick an illegal action.
- **Script executes.** Pathing, step-by-step movement and ability timing stay scripted. They're exact and cheap in native helpers, and learning them would waste capacity.
- **Every decision is a scoring function** in `decide.py`: features in, a score per candidate out. The first version is a hand-written utility formula with weights from `config.py`. A network head replaces it later behind the same interface, and nothing else changes.
- **One set of weights for every champion.** Champion type, ability readiness and stats are inputs, so one network serves every body and every roster.
- **Build order for heads:** the mode selector first (cheapest, easiest to compare against the script), then camp targeting, then fight or flee and the attack target, then shop and draft.

## 5. The first network: mode selector

Picks one of six modes for a champion: **farm, capture, tribute, recall and shop, fight, flee**. The scripted action modules carry out the chosen mode, and target choice inside each mode stays scripted at first.

### Architecture: 66 → 32 → 16 → 6

*2026-10-20: widened from 64 to 66 inputs so the champion one-hot covers all 13 body types (Tavi and Heart of Ruin had no slot). Everything after the one-hot keeps its order, shifted by two; the tables below still use the original numbering. `features.py` defines the block offsets.*

| Layer | Size | Activation | Weights + biases | Multiply-adds |
|---|---|---|---|---|
| Input | 66 | already 0–255 | none | none |
| Hidden 1 | 32 | ReLU | 2,144 | 2,112 |
| Hidden 2 | 16 | ReLU | 528 | 512 |
| Output | 6 | none (scores) | 102 | 96 |
| **Total** | | | **2,710** | **2,656** |

- **Why two hidden layers:** good mode choices depend on combinations. Low health matters only when an enemy is near, and a big bag matters only when an altar is far. Most of the cost is in the first layer anyway.
- **Why not wider:** about 2,700 weights is plenty for 64 inputs and 6 outputs. Wider layers cost fuel roughly in proportion and overfit more.
- **Optional value output:** a 7th output predicting gems gained over the next N rounds costs 16 multiply-adds. It doubles as a confidence check for falling back to the script.
- **Fallback shape if fuel is tight:** 64 → 16 → 6 (about 1,100 multiply-adds).
- **Measured cost:** about 0.55M fuel, roughly 22% of a turn. The original estimate was 0.35–0.45M. The gap comes from the per-neuron overhead on small layers.

### Flip-flopping and when to run it

A per-turn selector can switch farm, tribute, farm on consecutive turns and waste moves. Three defences, which can be combined:

- Re-decide only on **events**: camp cleared, capture finished, enemy appears, bag full, health drops sharply. Reuse the last mode otherwise. With the measured 0.55M cost, this is the default.
- Inputs 27–33 (current mode and turns in it) let the network learn to stick with a mode.
- Optionally give the current mode a small score bonus.

### Inputs (64 in the original numbering below; 66 as built, each an integer 0–255)

Encoding rules for every input:

- Each input is one integer from 0 to 255, the network's native format. Ratios are scaled by 255, and counts and distances are clipped.
- Distances are walking turns from a distance field, clipped at 63 and multiplied by 4, so they mean the same on a 30x30 map and a 100x100 one.
- **Unknown has its own value.** Anything about enemies comes with an age or a "known" flag, so "not seen" never reads as "zero".
- Everything comes from the world model in `world.py`, not fresh SDK calls, apart from cheap reads of the champion's own state.

**Self (0–33)**, read from the champion's own object

| # | Input | How it's computed | Encoding |
|---|---|---|---|
| 0 | Health ratio | `health() / max_health()` | × 255 |
| 1 | Max health | `max_health()`, a proxy for items bought | ÷ 20, clip 255 |
| 2 | Strength | `strength()` | ÷ 2, clip 255 |
| 3 | Focus | `focus()` | ÷ 2, clip 255 |
| 4 | Carried gems | `gem_count()` | clip 255 |
| 5 | Gold | `gold_count()` | ÷ 4, clip 255 |
| 6 | Shards bought | sum of `item_count()` | × 8, clip 255 |
| 7–17 (now 7–19) | Champion type | 13-way one-hot from `champion_name()` | 0 or 255 each |
| 18 | Slot 0 cooldown | `abilities()` slot 0 `cooldown()`, 0 = ready | × 32, clip 255 |
| 19 | Slot 1 cooldown | same for slot 1 | × 32, clip 255 |
| 20 | Stunned | `restrictions()` or the buff list's `has_stun()` | 0 or 255 |
| 21 | Recalling | `is_recalling()` | 0 or 255 |
| 22 | Tributing | `is_submitting_gems()` | 0 or 255 |
| 23 | Teleporting | `is_teleporting()` | 0 or 255 |
| 24 | Channel time left | `remaining_channel_time()` | × 32, clip 255 |
| 25 | Carries altar lock | `consumables()` | 0 or 255 |
| 26 | Carries teleport crystal | `consumables()` | 0 or 255 |
| 27–32 | Current mode | 6-way one-hot of last turn's mode | 0 or 255 each |
| 33 | Turns in current mode | counter kept by `decide.py` | × 4, clip 255 |

**Position (34–39)**, distances from cached `DistanceField`s in `navigation.py`

| # | Input | How it's computed | Encoding |
|---|---|---|---|
| 34 | Turns to nearest usable altar | distance field to altars, skipping ones with a known enemy lock | clip 63, × 4 |
| 35 | Turns to own spawn | distance field to `spawn_region(Team.ALLY)` | clip 63, × 4 |
| 36 | Recall length | 5 turns, or 3 with the Home emblem | × 32 |
| 37 | On own spawn | position inside own spawn region | 0 or 255 |
| 38 | In a bush | position inside `bushes()` | 0 or 255 |
| 39 | Which side of the map | turns to own spawn ÷ (turns to own + enemy spawn) | × 255 |

**Farming opportunity (40–45)**, summaries of the scripted camp ranking from `candidates.py`

| # | Input | How it's computed | Encoding |
|---|---|---|---|
| 40 | Turns to best camp | walking turns to the top-ranked available camp group | clip 63, × 4 |
| 41 | Best camp's gems | gems from that group's monsters, from the camp table | clip 255 |
| 42 | Best camp's danger | estimated damage taken to clear it ÷ current health | × 128, clip 255 |
| 43 | Camps available nearby | camp groups alive or respawning before arrival within 20 turns | × 16, clip 255 |
| 44 | Capture in progress | rounds left on the 5-round capture timer, 0 if not capturing | × 51 |
| 45 | Camp damage incoming | summed strength of awake monsters within range 3 | ÷ 4, clip 255 |

**Threat (46–55)**, which mixes what's visible now with remembered sightings and their age, since `champions(team)` only returns visible champions

| # | Input | How it's computed | Encoding |
|---|---|---|---|
| 46 | Visible enemies | count from `champions(Team.ENEMY)` | × 32, clip 255 |
| 47 | Nearest visible enemy distance | Chebyshev distance | × 16, 255 if none visible |
| 48 | Nearest enemy health ratio | that enemy's `health() / max_health()` | × 255, 0 if none |
| 49 | Local power balance | visible enemy health × strength ÷ the same for us and visible allies | 128 = even, clip 0–255 |
| 50 | Biggest visible enemy bag | max `gem_count()` among visible enemies, if the SDK exposes it for enemies | clip 255 |
| 51 | Rounds since any enemy seen | from the world model | × 4, clip 255 |
| 52 | Turns to last-known enemy | walking turns to the most recent sighting | clip 63, × 4 |
| 53 | Visible allies | count within vision | × 64, clip 255 |
| 54 | Nearest ally distance | Chebyshev distance | × 16, 255 if none visible |
| 55 | Damage taken last turn | health lost since last turn ÷ `max_health()` | × 255, clip 255 |

Input 50 assumes the SDK reports a visible enemy's carried gems. Check that in the local runner, and drop the input if it doesn't.

**Team and game (56–63)**

| # | Input | How it's computed | Encoding |
|---|---|---|---|
| 56 | Game progress | `round_number() / 2000` | × 255 |
| 57 | Our tributed gems | `global_submitted_gems(Team.ALLY)` | ÷ 4, clip 255 |
| 58 | Their tributed gems | `global_submitted_gems(Team.ENEMY)` | ÷ 4, clip 255 |
| 59 | Gem lead | ours − theirs | ÷ 4, + 128, clip 0–255 |
| 60 | Allies tributing or recalling | count from status codes in shared memory | × 64, clip 255 |
| 61 | Allies dead | count from status codes in shared memory | × 64, clip 255 |
| 62 | Enemies known dead | enemies seen killed within the last 60 rounds | × 64, clip 255 |
| 63 | Plan aggression | aggression level the draft wrote into draft memory | 0–255 as written |

Inputs 60–62 depend on the shared-memory claims board: each body posts a status code (farming, tributing, recalling, dead) next to its camp claim.

### Masks (hard rules applied before the argmax)

- **Tribute** is masked when carrying no gems or when no usable altar is reachable.
- **Capture** is masked unless standing in a cleared camp region.
- **Recall** is masked while taking damage, since damage cancels it.
- **Fight** is masked when no enemy is visible.
- **Flee** is forced below a health floor set in `config.py`.

### Left out on purpose

- **The raw map and absolute positions.** They'd need far more inputs and would tie the network to particular maps. Distances and counts carry what the mode choice needs.
- **Per-camp and per-enemy detail.** Which camp or enemy to target is a decision for later heads. The mode selector only sees summaries.
- **Opponent identity.** Nothing in the API names the opposing team, and a network that learned one opponent wouldn't generalise.

## 6. General network design rules

### Inputs

- **Feed integers 0–255 directly**, in the same uint8 format as hidden activations. This skips a float conversion step.
- **Keep each decision to 32–64 features.** Inputs set the width of the first layer, usually the most expensive one.
- **Candidate features** for target heads: walking distance from a distance field, camp type, group health, gem and gold value, rounds until respawn, distance from the camp to the nearest altar, enemy last seen nearby and how long ago.
- **Egocentric and normalised:** positions relative to the champion, and distances in walking turns, so one network works from 30x30 to 100x100 maps.
- **Canonicalise by symmetry:** reflect everything into one team's frame using the map's symmetry, so team A and team B look identical to the network.
- **Encode the unknown:** vision is per champion, so every remembered fact carries its age.
- **Features cost fuel too.** Every SDK call crosses into the engine (see section 3). Build features from the cached world model.

### Architecture

- **Plain MLP heads** for fixed-size choices such as the mode selector.
- **Factored candidate scorer** for ranking a list. Split the first layer into a state part computed once per turn and a small per-candidate part, then add them. Scoring 12 candidates then costs little more than scoring one.
- **Set encoders** for variable numbers of entities: a small shared MLP on each camp, enemy or ally, then sum or max-pool. The result doesn't depend on order and handles any count. Cap the count at the nearest K.
- **Shared trunk, several heads:** compute one state embedding per turn and hang the mode, target and fight heads off it.
- **Skip attention.** Softmax and pairwise scores cost more than pooling, and with K under 16 it rarely pays off.
- **Memory across turns:** the bot instance persists between turns, so a recurrent state is possible. A 32-unit GRU step costs about 6,000 multiply-adds plus lookup-table sigmoids. Hand-built memory features (last-seen positions with ages) are usually cheaper for the same effect.
- **No convolutions.** For spatial context, summarise the map with native `Bitboard` operations (enemy counts per region, bush coverage, distance fields) and feed the results as scalars.
- **ReLU everywhere.** In integer maths it's a clamp, and the zeros it produces can be skipped. Where a bounded output is needed, use a clamp or a 256-entry lookup table, not sigmoid or tanh.

### Outputs

- **Integer scores and argmax.** No softmax at inference. If a random choice is ever needed, use a lookup-table exponential and the SDK's deterministic randomness.
- **Mask before the argmax.** Padded or invalid candidates get the minimum score.
- **Value head:** a predicted gem gain next to each score doubles as a confidence check. When the top two candidates are close, defer to the script.

### Quantisation

- **int8 weights, uint8 activations, 32-bit accumulators.** This is the format the packed method needs. The weights must come from quantisation-aware training to keep accuracy.
- **Requantise with integers** between layers: `y = clamp((acc * M) >> s, 0, 255)` with a per-layer multiplier and shift.
- **Pack biases into the start value** of the sum, so they cost nothing.
- **4-bit is a later option:** 4-bit weights and activations with 16-bit lanes ran about 1.6x faster (76 fuel per multiply-add), but they cap a layer at about 290 inputs and 16 levels of precision.
- **No floats anywhere.** Integer inference is bit-exact, so local and platform runs make identical decisions.

## 7. Inference engine (`nn.py`)

A working version is in `bots/fuelbench/src/benches.py` (`PackedLayer`), checked against a reference dot product.

- **Packed columns.** For each layer, store one big Python integer per input, holding that input's weight to every output in its own 32-bit lane, offset by +128 to stay positive. Build them at import, which happens at build time.
- **One C-level loop per layer:** `sum(map(operator.mul, x, columns), packed_bias)`, then `acc.to_bytes(4 * n_out, 'little')` and `array('I', ...)` to unpack the lanes. Subtract `128 * sum(x)` plus the bias offset from each lane to undo the offsets.
- **Skip zeros:** drop zero activations and their columns with `itertools.compress` before the multiply. ReLU layers are often half zeros, and this measured about 35% cheaper at half sparsity.
- **Reuse partial sums:** packed accumulators are plain integers, so the state part of a factored first layer is computed once and added to each candidate's part.
- **Keep layers narrow.** Requantising costs a few hundred fuel per output neuron, so widths of 64 or less keep that overhead small next to the multiply-adds.
- **Cache unchanged inputs.** A camp whose features didn't change since last turn doesn't need rescoring.
- **Resumable execution:** run as a generator that can pause between layers, so a larger network can be spread across several turns.

## 8. Weights (`weights.py`)

- **Generated, never edited by hand.** An export script writes it from the trained model as bytes literals (base85 or hex), plus each layer's shape, multiplier and shift.
- **Unpack and pack at module level.** This costs no match fuel because imports run at build time.
- **One block per head**, matching the on/off switches in `config.py`.
- **Size isn't the constraint.** 20,000 int8 weights is about 20KB of source, and the built bot is already about 43MB. No submission size or memory limit is published, so check one before shipping large weights.

## 9. Fuel scheduling

- **Guard every network:** check `fuel_remaining()` before running one, and fall back to the script when fuel is short. Each check costs about 600–2,000 fuel, so don't call it inside loops.
- **Decide on events:** re-run a head when a camp is cleared, a capture finishes or an enemy appears, and reuse the last choice otherwise.
- **Slice big networks across turns** with a generator that runs one layer per turn, while scripted logic acts. Its inputs will be a few turns stale.
- **Never hit suspension.** Keep a reserve for acting after the networks run.

## 10. Draft network and team memory

- **Draft network on the first draft turn.** It reads whole-map features (camp groups, altars, spawn distances, symmetry) with 25M fuel, scores each champion given the picks so far, and computes a plan. Later draft turns have much less fuel, so cache the plan. Lock in a safe default pick early in the turn before any expensive work, because a missed pick is chosen at random.
- **Draft memory as a learned plan.** The 1,024 bytes can hold a learned plan vector, for example 128 int8 values per body, which every champion reads as extra input features. Read it once at the start of play (about 10K fuel) and decode it into a Python object.
- **Shared memory stays hand-designed.** In principle, 8 bytes could carry a learned 64-bit message, but that's hard to learn. Hand-designed claims (which camp each ally is on, plus a status code) fed to the networks as features are the safer design.

## 11. Safety and testing

- **Parity tests (`parity_test.py`):** run fixed input vectors through an off-platform reference and through `nn.py`, and fail unless the integers match exactly. Run this before shipping any weights.
- **Fallback on everything:** exceptions, low fuel, a disabled head or a small score margin all fall back to the scripted choice.
- **Per-head switches** in `config.py` turn a bad head off without removing it.
- **Head-to-head testing (`run_matches.py`):** wrap `cambc run-batch --release` to play two bot versions on every map from both sides. Report win rate and average gem margin. Keep a network change only if it wins.
- **Fuel reporting (`fuel_report.py`):** read match logs and report worst-case fuel per stage, so a turn creeping toward the limit shows up before it suspends in a real match. Bot output is inside each `.repl26a` replay (a zip): `team-a.stdout`, `team-a.fuel`, `team-a.debug`.
- **Free logging:** `bc.debug_block` costs no fuel in debug builds and is skipped on the platform. Use it to dump features, scores and choices.

## 12. Files involved

| File | Role |
|---|---|
| `decide.py` | Every decision as a scoring function, with the network or the script behind the same interface. Also holds the mode state machine and mode counter. |
| `candidates.py` | Generates the legal options each head ranks. Caps each list (for example at 12). |
| `features.py` | Builds 0–255 integer features from the world model. Shared by the script and the networks. |
| `world.py` | Cached world model. Every fact carries its age. |
| `config.py` | Thresholds, the health floor, fuel reserves and per-head on/off switches. |
| `fuel.py` | "Can I afford X more fuel?" guards. |
| `memory_codec.py` | Draft memory (plan vector) and the shared-memory claims board. |
| `nn.py` | Packed int8 inference engine. |
| `weights.py` | Generated weights. |
| `datalog.py` | Debug-build-only decision logger (`config.DATALOG`): features, mask, script scores and the chosen mode per turn, free via `debug_block`. |
| Tools (not submitted) | `run_matches.py` (harness, `--debug --log-limit` for logging runs), `build_dataset.py` (logs + statistics JSON → features, choices and 60-round outcome labels), `train_mode_selector.py` (outcome-weighted imitation in PyTorch, post-training int8 quantisation, writes `weights.py`), `parity_test.py` (nn.py against a numpy integer reference), `death_report.py`; replay path: `fetch_replays.py` (ladder match zips + index), `fetch_viewer.py` (engine wasm), `replay_dump.py`/`replay_dump.mjs` (re-simulate a replay, dump each champion's perspective per turn), `replay_features.py` (dumps → the bot's own features, inferred mode labels, outcome/result/rating labels) |

## 13. Open questions

- [x] Are NumPy or PyTorch importable? No.
- [x] What does a multiply-add cost on the real runtime? 98–180 fuel packed int8, depending on layer size. Re-measured in this repo.
- [x] How much do common SDK reads cost? See section 3.
- [ ] Does the platform build freeze module imports the same way the local build does? Confirm with one submission.
- [ ] Is there a limit on submission size or bot memory?
- [ ] Does the SDK report a visible enemy's carried gems (mode selector input 50)?
- [ ] Do allies acting later in the same round see a shared-memory claim posted earlier that round?
- [ ] This repo's `sdk/` is still 2.1.2 while the runner is 2.2.5. Refresh it with `cambc init` before relying on 2.2.5-only API names.
