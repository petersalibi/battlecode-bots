"""Which maps of a run_matches.py batch mirror exactly: the game with the first bot as team A and
the game with it as team B came out identical with the roles swapped (gems, rounds, deaths).

    python tools/analysis/mirror_check.py replays/batch/<run>

Run it on a bot against a copy of itself with dormant code or a fuel burn added: behaviour that
does not depend on fuel mirrors on every map (samit-sucks README, phase 1).
"""
import collections
import json
import os
import sys

matches = json.load(open(os.path.join(sys.argv[1], "results.json")))["matches"]
by_map = collections.defaultdict(dict)
for m in matches:
    by_map[m["map"]][m["side"]] = m
exact, diverge = [], []
for name, games in sorted(by_map.items()):
    if set(games) != {"A", "B"}:
        continue
    a, b = games["A"], games["B"]
    same = (a["gems_a"], a["gems_b"], a["rounds"], a["deaths_a"], a["deaths_b"]) == \
           (b["gems_b"], b["gems_a"], b["rounds"], b["deaths_b"], b["deaths_a"])
    (exact if same else diverge).append((name, (a["gems_a"], a["gems_b"]), (b["gems_b"], b["gems_a"])))
print(f"{len(exact)} maps mirror exactly, {len(diverge)} diverge")
for name, first, second in diverge:
    print(f"  {name:<24} as A {first[0]}-{first[1]}   as B {second[0]}-{second[1]}")
