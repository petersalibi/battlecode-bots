"""Collect top teams' ranked drafts from the match listing (no replay downloads): map, side,
result, engine version and the picks in order.

    python tools/fetch_drafts.py --teams 25 --per-team 60 -o data/drafts/drafts.json

Appends to the output (keyed by match id and side), so repeated runs after a patch grow the
set; filter by engine version when building the dataset.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_replays import cambc_json, ladder as top_teams, ROOT  # noqa: E402
from progress import Progress  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--teams", type=int, default=25)
    ap.add_argument("--per-team", type=int, default=60)
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "data", "drafts", "drafts.json"))
    args = ap.parse_args()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    rows = json.load(open(args.out, encoding="utf-8")) if os.path.exists(args.out) else {}
    teams = top_teams(args.teams)
    bar = Progress(len(teams), "teams")
    added = 0
    for team in teams:
        got, cursor = 0, None
        while got < args.per_team:
            a = ["matches", "list", "--team", team["teamId"], "--kind", "ranked", "--limit", "50"]
            if cursor:
                a += ["--cursor", cursor]
            d = cambc_json(*a)
            items = d.get("matches", []) if isinstance(d, dict) else d
            for m in items:
                if m.get("status") != "finished":
                    continue
                for t in m.get("teams", []):
                    if t.get("teamId") != team["teamId"] or not t.get("draftPicks"):
                        continue
                    key = f"{m['matchId']}:{t['side']}"
                    if key not in rows:
                        rows[key] = {"map": m["map"], "side": t["side"], "result": t.get("result"),
                                     "engine": t.get("engineVersion"), "picks": [int(p) for p in t["draftPicks"]],
                                     "teamId": team["teamId"], "rating": team.get("rating"), "createdAt": m.get("createdAt")}
                        added += 1
                    got += 1
            cursor = d.get("nextCursor") if isinstance(d, dict) else None
            if not items or not cursor:
                break
        bar.step(note=team["name"][:20])
    bar.done()
    json.dump(rows, open(args.out, "w", encoding="utf-8"), indent=1)
    from collections import Counter
    print(f"{len(rows)} drafts ({added} new) -> {args.out}; by engine {dict(Counter(r['engine'] for r in rows.values()))}; "
          f"wins {sum(r['result'] == 'win' for r in rows.values())}")


if __name__ == "__main__":
    main()
