"""Ranked games of the band teams (the 14 from the band challenges), engine 1.0.2, newest first."""
import json, os, sys, subprocess
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from fetch_replays import team_matches, download
from progress import Progress
out = os.path.join(ROOT, "data", "replays", "band-1.0.2")
raw = os.path.join(out, "raw"); os.makedirs(raw, exist_ok=True)
groups = json.load(open(os.path.join(ROOT, "data", "challenges", "v14-band200", "groups.json")))["groups"]
teams = {}
for g in groups: teams[g["teamId"]] = g["team"]
index = {}
for tid, name in teams.items():
    ms = team_matches(tid, int(sys.argv[1]), since="2026-10-21T06:00:00Z")
    ms = [m for m in ms if all((t.get("engineVersion") or "") == "1.0.2" for t in m["teams"])]
    for m in ms: index[m["matchId"]] = m
    print(f"{name}: {len(ms)} games", flush=True)
json.dump(index, open(os.path.join(raw, "index.json"), "w", encoding="utf-8"), indent=1)
bar = Progress(len(index), "download")
for mid in index:
    p = os.path.join(raw, mid + ".zip")
    if not os.path.exists(p): download(mid, p)
    bar.step()
bar.done()
subprocess.run([sys.executable, os.path.join(ROOT, "tools", "replay_dump.py"), raw, "-o", os.path.join(out, "ladder"), "--jobs", "8"], cwd=ROOT)
