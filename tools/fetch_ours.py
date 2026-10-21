"""Download our own ranked games since a time, dump them, and run the deaths breakdown.

    python tools/fetch_ours.py --since 2026-10-21T03:20:00Z -o data/replays/ours-v8

Writes <out>/raw/<id>.zip plus index.json (the same layout fetch_replays.py uses), dumps into
<out>/ladder, then prints tools/ladder_deaths.py's table. Downloads run one at a time (the
platform returns HTTP 409 otherwise) and wait out 429s.
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_replays import cambc_json, download, ROOT  # noqa: E402
from progress import Progress  # noqa: E402

TEAM = "Teto Territory"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since", required=True, help="ISO UTC time, e.g. 2026-10-21T03:20:00Z")
    ap.add_argument("--until", help="ISO UTC time (exclusive)")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--team", default=TEAM)
    ap.add_argument("--ladder", help="ladder json for opponent ranks (tools/ladder_deaths.py --ladder)")
    args = ap.parse_args()
    raw, ladder = os.path.join(args.out, "raw"), os.path.join(args.out, "ladder")
    os.makedirs(raw, exist_ok=True)
    matches, cursor = [], None
    while True:
        a = ["matches", "list", "--team", "me", "--kind", "ranked", "--limit", "50"]
        if cursor:
            a += ["--cursor", cursor]
        d = cambc_json(*a)
        items = d.get("matches", []) if isinstance(d, dict) else d
        stop = False
        for m in items:
            if m.get("createdAt", "") < args.since:
                stop = True
                break
            if args.until and m.get("createdAt", "") >= args.until:
                continue
            if m.get("status") == "finished":
                matches.append(m)
        cursor = d.get("nextCursor") if isinstance(d, dict) else None
        if stop or not items or not cursor:
            break
    index_path = os.path.join(raw, "index.json")
    index = json.load(open(index_path, encoding="utf-8")) if os.path.exists(index_path) else {}
    for m in matches:
        index[m["matchId"]] = m
    json.dump(index, open(index_path, "w", encoding="utf-8"), indent=1)
    bar = Progress(len(matches), "download")
    failed = []
    for m in matches:
        p = os.path.join(raw, m["matchId"] + ".zip")
        if not os.path.exists(p) and download(m["matchId"], p):
            failed.append(m["matchId"])
        bar.step(note=m["map"])
    bar.done()
    if failed:
        print("failed downloads:", failed)
    py = sys.executable
    subprocess.run([py, os.path.join(ROOT, "tools", "replay_dump.py"), raw, "-o", ladder, "--jobs", os.environ.get("BC_JOBS", "8")], cwd=ROOT)
    cmd = [py, os.path.join(ROOT, "tools", "ladder_deaths.py"), ladder, "--index", index_path, "--team", args.team]
    if args.ladder:
        cmd += ["--ladder", args.ladder]
    subprocess.run(cmd, cwd=ROOT)


if __name__ == "__main__":
    main()
