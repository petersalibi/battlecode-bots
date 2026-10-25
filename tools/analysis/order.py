"""Within a round, which of a team's champions acts first, among those with an enemy in sight?"""
import json, gzip, glob, os, sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
DIR = sys.argv[1]

def one(f):
    out = Counter()
    rounds = defaultdict(list)
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        next(fh)
        for l in fh:
            r = json.loads(l)
            if r.get("kind") != "turn": continue
            s = r["state"]["self"] or {}
            if not s.get("alive", True) or s.get("x") is None: continue
            en = [(c["x"], c["y"], c["hp"]) for c in r["state"]["champs"] if c["team"] != r["team"]]
            d = min((max(abs(e[0]-s["x"]), abs(e[1]-s["y"])) for e in en), default=99)
            lowest_enemy = min((e[2] for e in en if max(abs(e[0]-s["x"]), abs(e[1]-s["y"])) <= 4), default=None)
            atk = any(a["k"] == 5 and "to" in a for a in r["actions"])
            rounds[(r["round"], r["team"])].append(dict(champ=r["champ"], d=d, hp=s["hp"] / max(1, s["max"]), gems=s.get("gems") or 0,
                                                       str=s.get("str") or 0, atk=atk, type=s.get("type")))
    for key, turns in rounds.items():
        fighters = [t for t in turns if t["d"] <= 6]
        if len(fighters) < 2: continue
        first = fighters[0]
        out["n"] += 1
        out["first_nearest"] += first["d"] == min(t["d"] for t in fighters)
        out["first_strongest"] += first["str"] == max(t["str"] for t in fighters)
        out["first_healthiest"] += first["hp"] == max(t["hp"] for t in fighters)
        out["first_weakest_hp"] += first["hp"] == min(t["hp"] for t in fighters)
        out["first_most_gems"] += first["gems"] == max(t["gems"] for t in fighters)
        out["first_attacks"] += first["atk"]
        out["chance_attacks"] += sum(t["atk"] for t in fighters) / len(fighters)
        out["chance"] += 1 / len(fighters)
    return out

if __name__ == "__main__":
    files = glob.glob(DIR + "/*.jsonl.gz")[:int(sys.argv[2])]
    tot = Counter()
    with ProcessPoolExecutor(max_workers=int(os.environ.get("BC_JOBS", 8))) as ex:
        for c in ex.map(one, files): tot.update(c)
    n = tot["n"]
    print(f"{n} team-rounds with 2+ bodies within 6 of an enemy; chance level {100*tot['chance']/n:.0f}%")
    for k in ("first_nearest", "first_strongest", "first_healthiest", "first_weakest_hp", "first_most_gems", "first_attacks"):
        print(f"  {k:18} {100*tot[k]/n:.0f}%")
    print(f"  (attack rate of a random fighter {100*tot['chance_attacks']/n:.0f}%)")
