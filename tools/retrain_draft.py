"""One command to refresh the draft head after a patch: collect the top teams' recent drafts,
rebuild the pick rows from the map statics, retrain, export and run the parity test.

    python tools/retrain_draft.py --engine 1.0.3 [--teams 30 --per-team 80]

Needs dumps for every map in the pool (any dump folders with a sibling raw/index.json; the
defaults cover the 1.0.1 sets and our own games). After a map pool change, dump at least one
replay per new map first (tools/fetch_ours.py or fetch_replays.py + replay_dump.py). Then sweep
the result against the current tag before submitting.
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
DEFAULT_DUMPS = [os.path.join(ROOT, "data", "replays", "1.0.1", "ladder25"), os.path.join(ROOT, "data", "replays", "1.0.1", "ladder"),
                 os.path.join(ROOT, "data", "replays", "ours-v11", "ladder")]


def run(*args):
    print("+", " ".join(os.path.relpath(a, ROOT) if os.path.isabs(a) else a for a in args), flush=True)
    subprocess.run(list(args), cwd=ROOT, check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--engine", required=True, help="engine version to weight up (others at 0.3)")
    ap.add_argument("--teams", type=int, default=30)
    ap.add_argument("--per-team", type=int, default=80)
    ap.add_argument("--dumps", nargs="+", default=[d for d in DEFAULT_DUMPS if os.path.isdir(d)])
    ap.add_argument("--drafts", default=os.path.join(ROOT, "data", "drafts", "drafts.json"))
    ap.add_argument("--rows", default=os.path.join(ROOT, "data", "drafts", "drafts.npz"))
    args = ap.parse_args()
    tools = os.path.join(ROOT, "tools")
    run(PY, os.path.join(tools, "fetch_drafts.py"), "--teams", str(args.teams), "--per-team", str(args.per_team), "-o", args.drafts)
    run(PY, os.path.join(tools, "build_draft_dataset.py"), args.drafts, "--dumps", *args.dumps, "-o", args.rows)
    run(PY, os.path.join(tools, "train_draft_head.py"), args.rows, "--winners", "--engine", args.engine)
    run(PY, os.path.join(tools, "parity_test.py"), args.rows)
    print("draft head refreshed: sweep it (python tools/run_matches.py hybrid hybrid@<current tag>) before submitting")


if __name__ == "__main__":
    main()
