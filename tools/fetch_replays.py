"""Download ranked ladder replays of the strongest teams for offline training.

    python tools/fetch_replays.py --teams 100 --per-team 10 --since 2026-10-20T19:05:00Z --won-only -o data/replays/1.0.1/raw

Keep one folder per game version (data/replays/<engine>/raw) so a balance patch does not mix
old and new rules in one dataset.

Reads the ladder (`cambc ladder --json`), then for each of the top N teams lists its finished
ranked matches on the current engine (1.x) and downloads up to M of them with
`cambc matches download`. Each download is the platform's match zip (replay plus the bots'
logs). An index.json beside the zips records map, teams, ratings and results so the dataset can be
weighted by who played and who won. Needs `cambc login` (done by Tyler). Already-downloaded
matches are skipped, so the script can be re-run as the ladder grows.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from progress import Progress  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAMBC = os.path.join(ROOT, ".venv", "Scripts", "cambc.exe")
if not os.path.exists(CAMBC):
    CAMBC = "cambc"


def cambc_json(*args):
    # Team names carry any script; with the console code page subprocess would drop the output.
    for attempt in range(12):
        proc = subprocess.run([CAMBC, *args, "--json"], cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace")
        if not proc.returncode:
            break
        err = proc.stderr.strip()
        if "429" not in err and "Rate limit" not in err:
            sys.exit(f"cambc {' '.join(args)} failed: {err[:300]}")
        m = re.search(r"retry after (\d+)", err)
        time.sleep(max(int(m.group(1)) if m else 1, 1) + attempt)     # the platform rate-limits listing
    else:
        sys.exit(f"cambc {' '.join(args)}: rate limited for too long")
    text = proc.stdout
    start = text.find("{") if text.lstrip().startswith("{") else text.find("[")
    return json.loads(text[start:])


def ladder(n):
    teams = []
    page = 1
    while len(teams) < n:
        d = cambc_json("ladder", "--page", str(page), "--limit", "25")
        items = d if isinstance(d, list) else (d.get("ladder") or d.get("teams") or d.get("items") or [])
        if not items:
            break
        teams.extend(items)
        page += 1
    return teams[:n]


def team_matches(team_id, want, min_engine=1, since=None, won_only=False):
    """Finished ranked matches of a team, newest first; stops at `since` (ISO time) and keeps only
    the matches the team won when won_only is set."""
    out, cursor, done = [], None, False
    while len(out) < want and not done:
        args = ["matches", "list", "--team", team_id, "--kind", "ranked", "--limit", "50"]
        if cursor:
            args += ["--cursor", cursor]
        d = cambc_json(*args)
        items = d.get("matches", []) if isinstance(d, dict) else d
        for m in items:
            if since and (m.get("createdAt") or "") < since:
                done = True
                break
            if m.get("status") != "finished":
                continue
            if won_only and not any(x.get("teamId") == team_id and x.get("result") == "win" for x in m.get("teams", [])):
                continue
            versions = [t.get("engineVersion", "0") for t in m.get("teams", [])]
            if not all(v.split(".")[0].isdigit() and int(v.split(".")[0]) >= min_engine for v in versions):
                continue
            out.append(m)
            if len(out) >= want:
                break
        cursor = d.get("nextCursor") if isinstance(d, dict) else None
        if not items or not cursor:
            break
    return out


def download(mid, path):
    """Download one match zip; returns the match id on failure, None on success."""
    proc = subprocess.run([CAMBC, "matches", "download", mid, "-o", path], cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace")
    if proc.returncode or not os.path.exists(path):
        print(f"{mid}: download failed: {proc.stderr.strip()[:200]}")
        return mid
    return None


def main():
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--teams", type=int, default=5, help="how many top ladder teams")
    ap.add_argument("--per-team", type=int, default=10, help="matches per team")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "data", "replays", "raw"))
    ap.add_argument("--dry-run", action="store_true", help="list what would be downloaded")
    ap.add_argument("--since", help="only matches created at or after this ISO time (UTC), e.g. 2026-10-20T19:05:00Z")
    ap.add_argument("--won-only", action="store_true", help="only matches the listed top team won")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    index_path = os.path.join(args.out, "index.json")
    index = json.load(open(index_path, encoding="utf-8")) if os.path.exists(index_path) else {}
    rating = {}
    teams = ladder(args.teams)
    for t in teams:
        rating[t["teamId"]] = t.get("rating")
        print(f"#{t.get('rank')} {t['name']} ({t.get('rating', 0):.0f})")
    downloaded = 0
    bar = Progress(len(teams), "teams")
    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(4)                     # downloads overlap; listing stays sequential (rate limit)
    pending = []
    for t in teams:
        bar.step(note=f"{t['name']} ({downloaded} queued)")
        for m in team_matches(t["teamId"], args.per_team, since=args.since, won_only=args.won_only):
            mid = m["matchId"]
            path = os.path.join(args.out, f"{mid}.zip")
            entry = {"matchId": mid, "map": m.get("map"), "createdAt": m.get("createdAt"), "endReason": m.get("endReason"),
                     "teams": [{"name": x.get("name"), "teamId": x.get("teamId"), "side": x.get("side"), "result": x.get("result"),
                                "draftPicks": x.get("draftPicks"), "engineVersion": x.get("engineVersion"),
                                "rating": rating.get(x.get("teamId"))} for x in m.get("teams", [])]}
            index[mid] = entry
            if os.path.exists(path):
                continue
            if args.dry_run:
                print("would download", mid, m.get("map"), [x.get("name") for x in m.get("teams", [])])
                continue
            pending.append(pool.submit(download, mid, path))
            downloaded += 1
        json.dump(index, open(index_path, "w", encoding="utf-8"), indent=1)
    failed = [f.result() for f in pending if f.result()]
    pool.shutdown()
    bar.done()
    for mid in failed:
        index.pop(mid, None)
    downloaded -= len(failed)
    json.dump(index, open(index_path, "w", encoding="utf-8"), indent=1)
    if failed:
        print(f"{len(failed)} downloads failed")
    if args.since:
        print(f"matches since {args.since}")
    print(f"{downloaded} new replays; index has {len(index)} matches -> {index_path}")


if __name__ == "__main__":
    main()
