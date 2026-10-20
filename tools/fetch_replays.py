"""Download ranked ladder replays of the strongest teams for offline training.

    python tools/fetch_replays.py --teams 5 --per-team 10 -o data/replays/raw

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
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAMBC = os.path.join(ROOT, ".venv", "Scripts", "cambc.exe")
if not os.path.exists(CAMBC):
    CAMBC = "cambc"


def cambc_json(*args):
    proc = subprocess.run([CAMBC, *args, "--json"], cwd=ROOT, capture_output=True, text=True)
    if proc.returncode:
        sys.exit(f"cambc {' '.join(args)} failed: {proc.stderr.strip()[:300]}")
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


def team_matches(team_id, want, min_engine=1):
    out, cursor = [], None
    while len(out) < want:
        args = ["matches", "list", "--team", team_id, "--kind", "ranked", "--limit", "50"]
        if cursor:
            args += ["--cursor", cursor]
        d = cambc_json(*args)
        items = d.get("matches", []) if isinstance(d, dict) else d
        for m in items:
            if m.get("status") != "finished":
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


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--teams", type=int, default=5, help="how many top ladder teams")
    ap.add_argument("--per-team", type=int, default=10, help="matches per team")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "data", "replays", "raw"))
    ap.add_argument("--dry-run", action="store_true", help="list what would be downloaded")
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
    for t in teams:
        for m in team_matches(t["teamId"], args.per_team):
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
            proc = subprocess.run([CAMBC, "matches", "download", mid, "-o", path], cwd=ROOT, capture_output=True, text=True)
            if proc.returncode or not os.path.exists(path):
                print(f"{mid}: download failed: {proc.stderr.strip()[:200]}")
                continue
            downloaded += 1
            print(f"{mid}: {m.get('map')} {' vs '.join(x.get('name', '?') for x in m.get('teams', []))} ({os.path.getsize(path) // 1024} KB)")
    json.dump(index, open(index_path, "w", encoding="utf-8"), indent=1)
    print(f"{downloaded} new replays; index has {len(index)} matches -> {index_path}")


if __name__ == "__main__":
    main()
