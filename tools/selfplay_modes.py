"""Self-play for the mode selector against a pool: our tagged versions, scripted archetypes and
an adversary whose own selector is trained to beat us.

    python tools/selfplay_modes.py --iterations 3 --explore 0.1

One iteration:
  1. Export profiles of the working copy (replays/selfplay/bots/<name>): `main` (exploring,
     logging), the archetypes `aggro`, `coward`, `script` (no network, no exploration) and
     `adversary` (the selector in weights_adv.py, exploring, logging).
  2. Debug sweeps with the data logger on: main against every pool member (tags from --pool,
     the archetypes, the adversary), both sides on every map; and the adversary against main.
  3. tools/build_dataset.py on main's games and on the adversary's games (outcome labels:
     gems tributed and collected over the next 60 rounds minus a death penalty).
  4. Retrain main on the ladder base plus its self-play rows (tools/train_mode_selector.py
     --extra), the adversary on the base plus its rows into weights_adv.py; parity test.
  5. Gate: the new main (release, no exploration) against --gate-tag on every map; the new
     weights stay if it wins at least --gate-wins of 70, else weights.py is restored.

Exploration (`MODE_EXPLORE`) swaps the chosen mode for another legal one at that probability
in data runs only; submitted builds never set it. Everything written goes under
replays/selfplay/ and data/selfplay/.
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
SRC = os.path.join(ROOT, "bots", "hybrid")
BOTS = os.path.join(ROOT, "replays", "selfplay", "bots")
RUNS = os.path.join(ROOT, "replays", "selfplay", "modes")
DATA = os.path.join(ROOT, "data", "selfplay")
WEIGHTS = os.path.join(SRC, "src", "weights.py")
WEIGHTS_ADV = os.path.join(SRC, "src", "weights_adv.py")

PROFILES = {
    "main": "DATALOG = True\nMODE_EXPLORE = {explore}\nMODE_EXPLORE_SALT = {salt}\n",
    "adversary": "DATALOG = True\nUSE_ADVERSARY_WEIGHTS = True\nMODE_EXPLORE = {explore}\nMODE_EXPLORE_SALT = {salt} + 500\n",
    "aggro": "USE_NN_MODE = False\nFIGHT_POWER_RATIO = 0.8\nFLEE_POWER_RATIO = 0.4\nFLEE_HEALTH = 0.2\nOUTNUMBERED_RULE = False\nSPAWN_GATE = False\n",
    "coward": "USE_NN_MODE = False\nFIGHT_POWER_RATIO = 2.0\nFLEE_POWER_RATIO = 1.2\nRECALL_HEALTH = 0.6\nFLEE_GROUP_EXTRA = 5\n",
    "script": "USE_NN_MODE = False\n",
}


def run(cmd, **kw):
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)
    if r.returncode:
        print(r.stdout[-2000:], r.stderr[-2000:])
        raise SystemExit(f"failed: {' '.join(cmd[:4])}")
    return r.stdout


def export_profile(name, explore, salt):
    dst = os.path.join(BOTS, name)
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    with open(os.path.join(dst, "src", "profile.py"), "w", encoding="utf-8", newline="\n") as f:
        f.write('"""Exported profile: constants overriding config.py (tools/selfplay_modes.py)."""\n')
        f.write(PROFILES[name].format(explore=explore, salt=salt))
    return dst


def sweep(bot_a, bot_b, out, debug):
    cmd = [PY, os.path.join(ROOT, "tools", "run_matches.py"), bot_a, bot_b, "--out", out]
    if debug:
        cmd += ["--debug", "--log-limit", "16000000"]
    stdout = run(cmd)
    line = next((l for l in stdout.splitlines() if l.startswith("hybrid") and " over " in l), "(no summary)")
    return line


def wins_of(summary):
    import re
    m = re.search(r": (\d+)-(\d+)-\d+ ", summary)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--iterations", type=int, default=1)
    ap.add_argument("--explore", type=float, default=0.1)
    ap.add_argument("--pool", nargs="*", default=["hybrid@hybrid-v9", "hybrid@hybrid-v10", "hybrid@hybrid-v11"])
    ap.add_argument("--archetypes", nargs="*", default=["aggro", "coward", "script"])
    ap.add_argument("--base", default=os.path.join(ROOT, "data", "ladder_modes.npz"), help="ladder rows the selector was trained on")
    ap.add_argument("--selfplay-weight", type=float, default=1.0)
    ap.add_argument("--gate-tag", default="hybrid-v11")
    ap.add_argument("--gate-wins", type=int, default=35)
    ap.add_argument("--beta", type=float, default=0.5)
    ap.add_argument("--salt-start", type=int, default=1)
    args = ap.parse_args()
    os.makedirs(RUNS, exist_ok=True); os.makedirs(DATA, exist_ok=True)
    done = [int(os.path.basename(d)) for d in glob.glob(os.path.join(RUNS, "[0-9]*")) if os.path.basename(d).isdigit()]
    it0 = max(done) + 1 if done else 1
    salt = args.salt_start + 10 * (it0 - 1)
    for it in range(it0, it0 + args.iterations):
        t0 = time.time()
        print(f"== iteration {it} (explore {args.explore}, salt {salt})", flush=True)
        shutil.copy(WEIGHTS, WEIGHTS + ".bak")
        main_bot = export_profile("main", args.explore, salt)
        adv_bot = export_profile("adversary", args.explore, salt)
        arch = {a: export_profile(a, 0.0, salt) for a in args.archetypes}
        out = os.path.join(RUNS, str(it))
        pool = {t.split("@")[-1]: t for t in args.pool}
        pool.update(arch)
        pool["adversary"] = adv_bot
        for name, opp in pool.items():
            line = sweep(main_bot, opp, os.path.join(out, f"main-vs-{name}"), debug=True)
            print(f"  main vs {name:14} {line[:80]}", flush=True)
        line = sweep(adv_bot, main_bot, os.path.join(out, "adversary-vs-main"), debug=True)
        print(f"  adversary vs main   {line[:80]}", flush=True)
        # Rows.
        main_rows = os.path.join(DATA, f"modes_main_{it}.npz")
        adv_rows = os.path.join(DATA, f"modes_adv_{it}.npz")
        run([PY, os.path.join(ROOT, "tools", "build_dataset.py"), *sorted(glob.glob(os.path.join(out, "main-vs-*"))), "-o", main_rows])
        run([PY, os.path.join(ROOT, "tools", "build_dataset.py"), os.path.join(out, "adversary-vs-main"), "-o", adv_rows])
        # Train main (base + its rows) and the adversary (base + its rows).
        extra_main = [f"{p}:{args.selfplay_weight}" for p in sorted(glob.glob(os.path.join(DATA, "modes_main_*.npz")))]
        extra_adv = [f"{p}:{args.selfplay_weight}" for p in sorted(glob.glob(os.path.join(DATA, "modes_adv_*.npz")))]
        log = run([PY, os.path.join(ROOT, "tools", "train_mode_selector.py"), args.base, "--winners", "--logit-l2", "1e-3",
                   "--beta", str(args.beta), "--extra", *extra_main, "-o", WEIGHTS])
        print("  main:", next((l for l in log.splitlines() if l.startswith("quantised")), "")[:110], flush=True)
        log = run([PY, os.path.join(ROOT, "tools", "train_mode_selector.py"), args.base, "--winners", "--logit-l2", "1e-3",
                   "--beta", str(args.beta), "--extra", *extra_adv, "-o", WEIGHTS_ADV])
        print("  adversary:", next((l for l in log.splitlines() if l.startswith("quantised")), "")[:110], flush=True)
        run([PY, os.path.join(ROOT, "tools", "parity_test.py")])
        # Gate: the new main in release, no exploration, against the gate tag.
        line = sweep("hybrid", f"hybrid@{args.gate_tag}", os.path.join(out, "gate"), debug=False)
        w, l = wins_of(line)
        print(f"  gate vs {args.gate_tag}: {line[:90]}", flush=True)
        if w < args.gate_wins:
            shutil.copy(WEIGHTS + ".bak", WEIGHTS)
            print(f"  kept the previous weights ({w} < {args.gate_wins})", flush=True)
        else:
            print(f"  new weights kept ({w} wins)", flush=True)
        json.dump({"iteration": it, "gate": line, "kept": w >= args.gate_wins, "explore": args.explore, "salt": salt},
                  open(os.path.join(out, "summary.json"), "w"), indent=1)
        print(f"  iteration {it} done in {(time.time() - t0) / 60:.0f} min", flush=True)
        salt += 10


if __name__ == "__main__":
    main()
