"""Unranked challenges against chosen teams: queue match groups, collect and dump the replays,
report results by opponent and map.

    python tools/challenges.py queue  -o data/challenges/v13-weak --top 10 --maps weak --groups-per-team 2
    python tools/challenges.py collect -o data/challenges/v13-weak
    python tools/challenges.py report  -o data/challenges/v13-weak

A match group is five unranked games against one team on maps and sides we choose. The team's
quota is 50 groups an hour (shared with teammates using the website); `queue` keeps a rolling
count of the groups it created in <out>/groups.json and, with --wait, sleeps until the hour
frees room, otherwise stops at the limit. Maps are dealt five at a time from the list, and each
map is played from side A in one group and side B in the next, so every map is seen from both;
map and side pairs already played against a team are skipped (games are deterministic).

`collect` downloads finished games one at a time (the platform answers HTTP 409 otherwise) into
<out>/raw/<match id>.zip with <out>/raw/index.json, the same layout as tools/fetch_replays.py,
then dumps them into <out>/ladder with tools/replay_dump.py. The zips carry our own stdout.
The profile tools, ladder_deaths.py and the dataset builders all read that layout.

Map lists: `weak` (v11's seven worst ladder maps), `all` (the current pool from `cambc maps`),
or comma-separated names (platform spelling, underscores: 00fragment, 01boss_battle).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_replays import cambc_json, download, ladder, ROOT  # noqa: E402
from progress import Progress  # noqa: E402

TEAM = "Teto Territory"
QUOTA = 50
WEAK = ["00fragment", "01corridors", "00packs", "00collision", "00swirl", "00eight", "01hooks"]


def now():
    return datetime.now(timezone.utc)


def load(path, default):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else default


def save(path, data):
    tmp = path + ".tmp"
    json.dump(data, open(tmp, "w", encoding="utf-8"), indent=1)
    os.replace(tmp, path)


def pool_maps():
    d = cambc_json("maps", "list")
    items = d if isinstance(d, list) else (d.get("maps") or [])
    return sorted(m.get("name") for m in items if m.get("name"))


def recent(groups, hours=1.0):
    cut = now() - timedelta(hours=hours)
    return [g for g in groups if datetime.fromisoformat(g["created"]) >= cut]


def cmd_queue(args):
    os.makedirs(args.out, exist_ok=True)
    state_path = os.path.join(args.out, "groups.json")
    state = load(state_path, {"groups": []})
    if args.maps == "weak":
        maps = WEAK
    elif args.maps == "all":
        maps = pool_maps()
    else:
        maps = [m.strip() for m in args.maps.split(",") if m.strip()]
    if args.teams:
        names = set(args.teams.split(","))
        teams = [t for t in ladder(200) if t["name"] in names or t["teamId"] in names]
    else:
        teams = [t for t in ladder(args.top + 1) if t["name"] != TEAM][:args.top]
    # The work: games are deterministic (the same two submissions on the same map and side play
    # the same game), so every (team, map, side) is played once. Each team gets the pairs it has
    # not played yet, side A of every map first, then side B, dealt five to a group.
    played = defaultdict(set)
    for g in state["groups"]:
        for m, sd in zip(g["maps"], g["sides"]):
            played[g["teamId"]].add((m, sd))
    plan = []
    for t in teams:
        todo = [(m, sd) for sd in "ab" for m in maps if (m, sd) not in played[t["teamId"]]]
        for k in range(args.groups_per_team):
            chunk = todo[5 * k:5 * k + 5]
            if not chunk:
                break
            while len(chunk) < 5:                    # a group is always five games: pad with repeats
                chunk.append(chunk[len(chunk) % len(chunk)])
            plan.append((t, [m for m, _ in chunk], [sd for _, sd in chunk]))
    print(f"{len(plan)} groups planned against {len(teams)} teams ({', '.join(t['name'] for t in teams)})")
    bar = Progress(len(plan), "queue")
    for t, five, sides in plan:
        while len(recent(state["groups"])) >= args.quota:
            if not args.wait:
                bar.done()
                print(f"stopped at the hourly quota ({args.quota}); run again later or pass --wait")
                save(state_path, state)
                return
            oldest = min(datetime.fromisoformat(g["created"]) for g in recent(state["groups"]))
            pause = (oldest + timedelta(hours=1) - now()).total_seconds() + 5
            print(f"\nquota reached; waiting {pause / 60:.0f} minutes", file=sys.stderr, flush=True)
            time.sleep(max(pause, 5))
        for attempt in range(20):
            proc = subprocess.run([os.path.join(ROOT, ".venv", "Scripts", "cambc.exe"), "match-groups", "create", t["teamId"],
                                   "--maps", ",".join(five), "--sides", ",".join(sides), "--json"],
                                  cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace")
            err = (proc.stderr or proc.stdout or "").strip()[:300] if proc.returncode else ""
            m = re.search(r"retry after (\d+)", err)
            if proc.returncode and m:                 # the per-second rate limit: wait it out
                time.sleep(int(m.group(1)) + 1 + attempt)
                continue
            break
        if proc.returncode:
            print(f"\n{t['name']}: create failed: {err}", file=sys.stderr, flush=True)
            if "quota" in err.lower() or "429" in err:
                save(state_path, state)
                print("the platform refused more groups; run again later")
                return
            bar.step(note="failed")
            continue
        out = json.loads(proc.stdout[proc.stdout.find("{"):])
        state["groups"].append({"groupId": out["groupId"], "team": t["name"], "teamId": t["teamId"], "rating": t.get("rating"),
                                "maps": five, "sides": sides, "created": now().isoformat(), "label": args.label})
        save(state_path, state)
        bar.step(note=t["name"])
    bar.done()


def cmd_collect(args):
    state = load(os.path.join(args.out, "groups.json"), {"groups": []})
    raw = os.path.join(args.out, "raw")
    os.makedirs(raw, exist_ok=True)
    index_path = os.path.join(raw, "index.json")
    index = load(index_path, {})
    pending, finished = 0, []
    bar = Progress(len(state["groups"]), "status")
    for g in state["groups"]:
        if g.get("done"):
            bar.step(); continue
        d = cambc_json("match-groups", "show", g["groupId"])
        all_done = True
        for m in d.get("matches", []):
            m["challenge"] = {"groupId": g["groupId"], "label": g.get("label")}
            if m.get("status") == "finished":
                index[m["matchId"]] = m
                finished.append(m)
            elif m.get("status") in ("queued", "running", "pending", None):
                all_done = False
                pending += 1
            else:
                index[m["matchId"]] = m          # failed or cancelled: kept for the record
        if all_done:
            g["done"] = True
        bar.step()
    bar.done()
    save(index_path, index)
    save(os.path.join(args.out, "groups.json"), state)
    todo = [m for m in finished if not os.path.exists(os.path.join(raw, m["matchId"] + ".zip"))]
    bar = Progress(len(todo), "download")
    for m in todo:
        download(m["matchId"], os.path.join(raw, m["matchId"] + ".zip"))
        bar.step(note=m["map"])
    bar.done()
    print(f"{len(finished)} finished games indexed, {len(todo)} downloaded now, {pending} still queued or running")
    if todo and not args.no_dump:
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "replay_dump.py"), raw, "-o", os.path.join(args.out, "ladder"),
                        "--jobs", os.environ.get("BC_JOBS", "8")], cwd=ROOT)


def cmd_report(args):
    index = load(os.path.join(args.out, "raw", "index.json"), {})
    by_team, by_map = defaultdict(lambda: [0, 0, 0]), defaultdict(lambda: [0, 0, 0])
    rows = []
    for m in index.values():
        if m.get("status") != "finished":
            continue
        ours = next((t for t in m["teams"] if t["name"] == args.team), None)
        theirs = next((t for t in m["teams"] if t["name"] != args.team), None)
        if not ours or not theirs:
            continue
        r = ours.get("result")
        for table, key in ((by_team, theirs["name"]), (by_map, m["map"])):
            table[key][0 if r == "win" else 1 if r == "loss" else 2] += 1
        rows.append((m["map"], ours["side"], theirs["name"], r, m.get("endReason"), ours.get("engineVersion")))
    print(f"{len(rows)} finished games; engine versions: {sorted({r[5] for r in rows if r[5]})}")
    print("\nby opponent (wins-losses-other)")
    for k, (w, l, o) in sorted(by_team.items(), key=lambda kv: -sum(kv[1])):
        print(f"  {k[:28]:28} {w}-{l}-{o}")
    print("\nby map")
    for k, (w, l, o) in sorted(by_map.items()):
        print(f"  {k:24} {w}-{l}-{o}")
    if os.path.isdir(os.path.join(args.out, "ladder")):
        print("\ndeaths breakdown (tools/ladder_deaths.py):")
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "ladder_deaths.py"), os.path.join(args.out, "ladder"),
                        "--index", os.path.join(args.out, "raw", "index.json"), "--team", args.team], cwd=ROOT)


def main():
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("queue")
    q.add_argument("-o", "--out", required=True)
    q.add_argument("--top", type=int, default=10, help="challenge the top N ladder teams")
    q.add_argument("--teams", help="comma-separated team names or ids instead of --top")
    q.add_argument("--maps", default="weak", help="weak, all, or comma-separated map names")
    q.add_argument("--groups-per-team", type=int, default=2)
    q.add_argument("--quota", type=int, default=QUOTA, help="groups per rolling hour")
    q.add_argument("--wait", action="store_true", help="sleep through the hourly quota instead of stopping")
    q.add_argument("--label", default="", help="free text stored with each group (e.g. the submitted version)")
    c = sub.add_parser("collect")
    c.add_argument("-o", "--out", required=True)
    c.add_argument("--no-dump", action="store_true")
    r = sub.add_parser("report")
    r.add_argument("-o", "--out", required=True)
    r.add_argument("--team", default=TEAM)
    args = ap.parse_args()
    {"queue": cmd_queue, "collect": cmd_collect, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
