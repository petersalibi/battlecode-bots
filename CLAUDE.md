# Cambridge Battlecode 2026 bots

Python bots for Cambridge Battlecode 2026 (event cambc2026a). The deadline is around early November 2026.

## Read first
- `docs/GameRules.md`: the game rules.
- `docs/BotAPIPython.md`: the Python bot API.
- `docs/NEURAL_NET_GUIDE.md`: the agreed design for neural networks in the bot (hybrid: scripted bot, decisions as scoring functions in `decide.py`, small packed-int8 networks swapped in behind them; the first is the 64→32→16→6 mode selector).
- `bots/fuelbench/RESULTS.md`: measured fuel costs in release builds.

## Setup
- cambc 3.0.1 is installed in `.venv` (gitignored). Use `.\.venv\Scripts\cambc`, or activate with `.\.venv\Scripts\Activate.ps1`. `sdk/` and `cambc.toml` match it (refreshed with `cambc init --yes .` on 2026-10-20).
- cambc 3.0 brought engine 1.0.0 and the week 2 patch (2026-10-20): camp rewards cut hard, kill gold 100, a Golem camp, a twelfth champion (Heart of Ruin), longer camp ranges, and the map pool is now the `00*` and `01*` files (21 maps). `cambc maps pull` works again. The 2.2.5-era maps are kept under `maps/old-2.2.5/` for comparison only. `docs/` was refreshed from the handbook's "Download for LLM" page (https://2026a.battlecode.co.uk/game/docs/agents) the same day; patch notes live at /game/patch-notes.
- Submit: `cambc submit bots/hybrid` (needs `cambc login`, typed by Tyler). Tag the commit `hybrid-vN` to match the submission number.
- Run a match: `cambc run <botA> <botB> --map <name> --release`. Bot stdout is inside the `.repl26a` replay (a zip): `team-a.stdout`, `team-a.fuel`.
- Evaluate a change: `python tools/run_matches.py hybrid hybrid@hybrid-v1` plays the working copy against a tagged version on every map from both sides (about 45 seconds for 42 games) and reports wins, gem margins, deaths, fuel exhaustions and stalls. Keep a change only if it wins there, and treat any `exh` as a bug. `name@gitref` exports any tagged version as an opponent.
- Open a replay: `cambc view <replay> --port 7026`, then the printed link in Firefox (Chrome freezes on this PC).

## Constraints
- The runtime is CPython 3.13 in WebAssembly. No NumPy, PyTorch or compiled extensions. Pure Python and the stdlib only.
- A champion turn has 2.5M fuel (about 4.5M usable on the first turn). Running out suspends the bot until next round and loses the rest of that turn's actions. An empty Python loop iteration costs about 1,000 fuel; a whole-map `DistanceField` about 80 per tile; `game_map.camp_groups()` plus per-group setup 2 to 3M on the biggest maps, so first-turn work must stay lean.
- Every module except `main.py` is imported at build time, so precompute at module level.

## Working agreements
- Ask Tyler before pushing, merging or opening a PR. The repo is shared with the team (petersalibi/battlecode-bots).
- Keep bots under `bots/<name>/src/`.
