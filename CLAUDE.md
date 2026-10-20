# Cambridge Battlecode 2026 bots

Python bots for Cambridge Battlecode 2026 (event cambc2026a). The deadline is around early November 2026.

## Read first
- `docs/GameRules.md`: the game rules.
- `docs/BotAPIPython.md`: the Python bot API.
- `docs/NEURAL_NET_GUIDE.md`: the agreed design for neural networks in the bot (hybrid: scripted bot, decisions as scoring functions in `decide.py`, small packed-int8 networks swapped in behind them; the first is the 64→32→16→6 mode selector).
- `bots/fuelbench/RESULTS.md`: measured fuel costs in release builds.

## Setup
- cambc 2.2.5 is installed in `.venv` (excluded locally via `.git/info/exclude`). Use `.\.venv\Scripts\cambc`, or activate with `.\.venv\Scripts\Activate.ps1`.
- `sdk/` and `cambc.toml` are still 2.1.2, and the runner warns about it. Refreshing with `cambc init` is the team's call.
- Run a match: `cambc run <botA> <botB> --map <name> --release`. Bot stdout is inside the `.repl26a` replay (a zip): `team-a.stdout`, `team-a.fuel`.

## Constraints
- The runtime is CPython 3.13 in WebAssembly. No NumPy, PyTorch or compiled extensions. Pure Python and the stdlib only.
- A champion turn has 2.5M fuel. Running out suspends the bot until next round. An empty Python loop iteration costs about 1,000 fuel.
- Every module except `main.py` is imported at build time, so precompute at module level.

## Working agreements
- Ask Tyler before pushing, merging or opening a PR. The repo is shared with the team (petersalibi/battlecode-bots).
- Keep bots under `bots/<name>/src/`.
