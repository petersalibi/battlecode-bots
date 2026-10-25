"""Paired comparison of challenge runs on the games they share (same opponent, map, side).

python compare_runs.py name=dir name=dir ...   (profiles and opening data are built if missing)
"""
import json, os, subprocess, sys, statistics as st
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "..", "..", "data", "analysis")   # per-run profiles, built once
os.makedirs(CACHE, exist_ok=True)
runs = [a.split("=", 1) for a in sys.argv[1:]]
data = {}
for name, d in runs:
    prof = os.path.join(CACHE, f"cmp_{name}.json"); op = os.path.join(CACHE, f"cmpo_{name}.json")
    if not os.path.exists(prof):
        subprocess.run([sys.executable, os.path.join(HERE, "profile.py"), d, "raw", "ladder", "teto", prof], check=True)
    if not os.path.exists(op):
        subprocess.run([sys.executable, os.path.join(HERE, "opening.py"), d, "raw", "ladder", "teto", op], check=True)
    p = {(r["opp"], r["map"], r["side"]): r for r in json.load(open(prof))}
    o = {}
    idx = json.load(open(os.path.join(d, "raw", "index.json"), encoding="utf-8"))
    for r in json.load(open(op)):
        o.setdefault((r["map"], r["team"]), []).append(r)
    data[name] = (p, json.load(open(op)))
common = set.intersection(*(set(p) for p, _ in data.values()))
print(f"{len(common)} games shared by {', '.join(data)}")
rows = [("wins", lambda r: 1 if r["result"] == "win" else 0, sum), ("deaths", lambda r: r["deaths"], st.mean),
        ("% turns dead", lambda r: 100 * r["dead"] / max(1, r["turns"]), st.mean), ("gathered", lambda r: r["gathered"], st.mean),
        ("banked by r300", lambda r: r["banked"].get("300", 0), st.mean), ("banked by r600", lambda r: r["banked"].get("600", 0), st.mean),
        ("banked by r1000", lambda r: r["banked"].get("1000", r["banked"].get("600", 0)), st.mean),
        ("% turns ally within 4", lambda r: 100 * r["ally_near"] / max(1, r["turns"]), st.mean)]
print(f"{'':24}" + "".join(f"{n:>10}" for n in data))
for label, fn, agg in rows:
    print(f"{label:24}" + "".join(f"{agg([fn(p[k]) for k in common]):>10.1f}" for p, _ in data.values()))
print("\nfirst 150 rounds, all games of each run:")
for name, (p, o) in data.items():
    act = {}
    for r in o:
        for k, v in r["act"].items(): act[k] = act.get(k, 0) + v
    T = sum(act.values())
    deaths = sum(len(r["deaths"]) for r in o) / max(1, len(o))
    print(f"  {name:10} dead {100*act.get('dead',0)/T:5.1f}%  hit camp {100*act.get('hit camp',0)/T:5.1f}%  deaths by r150 {deaths:.2f}/game  ({len(o)} games)")
