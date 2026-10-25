import json, os, sys, statistics as st
from collections import Counter, defaultdict
W = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "analysis")
groups = {}
for spec in sys.argv[1:]:
    name, f, who = spec.split("=")
    rows = json.load(open(os.path.join(W, f)))
    if who == "us": rows = [r for r in rows if r["team"] == "Teto Territory"]
    elif who == "them": rows = [r for r in rows if r["team"] != "Teto Territory"]
    groups[name] = rows
def mean(xs):
    xs = [x for x in xs if x is not None]; return st.mean(xs) if xs else float("nan")
print(f"{'':40}" + "".join(f"{n:>16}" for n in groups))
print(f"{'team-games':40}" + "".join(f"{len(g):>16}" for g in groups.values()))
def line(label, fn): print(f"{label:40}" + "".join(f"{fn(g):>16.1f}" for g in groups.values()))
for ph in ("1-150", "151-500", "501+"):
    line(f"dist to nearest ally, rounds {ph}", lambda g, ph=ph: mean(r["spread"].get(ph) for r in g))
    line(f"% turns ally within 4, rounds {ph}", lambda g, ph=ph: 100 * mean(r["clustered"].get(ph) for r in g))
line("first trip: bodies sharing a first camp %", lambda g: 100 * sum(r["first_trip"]["shared"] for r in g) / max(1, sum(r["first_trip"]["bodies"] for r in g)))
line("first trip: distinct first camps", lambda g: mean(r["first_trip"]["groups"] for r in g))
line("first trip: distance from spawn", lambda g: mean(r["first_trip"]["dist"] for r in g))
line("casts per 100 living turns", lambda g: 100 * sum(r["cast_total"] for r in g) / max(1, sum(sum(r["turns_by_type"].values()) for r in g)))
line("% casts landing on an enemy champion", lambda g: 100 * sum(r["cast_on_champ"] for r in g) / max(1, sum(r["cast_total"] for r in g)))
line("tributes per game", lambda g: mean(len(r["tributes"]) for r in g))
line("gems per tribute (median)", lambda g: st.median([x[1] for r in g for x in r["tributes"]] or [0]))
line("first tribute round (median)", lambda g: st.median([min(x[0] for x in r["tributes"]) for r in g if r["tributes"]] or [0]))
line("gems carried at death (mean)", lambda g: mean(x for r in g for x in r["carried_at_death"]))
line("kills per game", lambda g: mean(len(r["kills"]) for r in g))
line("kills: tiles from victim's spawn", lambda g: mean(k["from_victim_spawn"] for r in g for k in r["kills"]))
line("kills: tiles from killer's spawn", lambda g: mean(k["from_own_spawn"] for r in g for k in r["kills"]))
line("kills: victim in sight of them (rounds)", lambda g: mean(k["victim_seen_for"] for r in g for k in r["kills"]))
for cp in ("150", "300", "600", "1000"):
    line(f"gold held at r{cp} (median)", lambda g, cp=cp: st.median([r["gold_at"][cp] for r in g if cp in r["gold_at"]] or [0]))
line("set_priority per 100 turns", lambda g: mean(r["prio"] for r in g))
print("\ncasts per 100 living turns by champion:")
champs = Counter(); [champs.update(r["turns_by_type"]) for g in groups.values() for r in g]
for ch, _ in champs.most_common(10):
    vals = []
    for g in groups.values():
        turns = sum(r["turns_by_type"].get(ch, 0) for r in g)
        casts = sum(r["casts"].get(ch, 0) * r["turns_by_type"].get(ch, 0) / 100 for r in g)
        vals.append(f"{100*casts/turns:>16.1f}" if turns > 2000 else f"{'-':>16}")
    print(f"  {ch:38}" + "".join(vals))
