"""Reads a bots/campprobe replay and checks who each awake camp hit.

    .\\.venv\\Scripts\\cambc run campprobe campprobe --map 00rushbait-v2 --rounds 185 --release -o replays/campprobe
    python tools/camp_target_probe.py replays/campprobe/<bundle>.repl26a [--reach 3] [--rounds A-B] [--all]

Result on engine 1.0.3 (2026-10-23), 103 of 103 scored rounds: an awake camp hits the champion
nearest it by Chebyshev distance among those in its reach, of either team; on a tie, the champion
that last damaged it, else the first of the tied to move this round (the starting team alternates,
then draft order). It stays awake until two rounds pass with no champion in reach, however long
ago it was last damaged, and a sleeping camp does not attack a champion standing beside it.

Camps act at the start of a round, before any champion, so the tiles they choose from are the
end-of-turn tiles of the round before, and the hit shows up in the next turn-start health. The
probe bots never attack champions, so every loss away from spawn is camp damage.

Output: one line per stretch of rounds with the same picture (the champions in the camp's reach
with Chebyshev/Euclidean/Manhattan distances, and who lost health), then a score for each
targeting hypothesis over the rounds where exactly one damaging camp was awake.
"""
import argparse
import math
import zipfile
from collections import defaultdict

SPAWNS = {"L": (11, 14, 38, 41), "R": (15, 18, 38, 41)}   # rushbait-v2 spawn boxes (healing)
HARMLESS_HP = 120                                         # gem and gold clusters: 120 health, no damage


def load(path):
    z = zipfile.ZipFile(path)
    lines = []
    for name in ("team-a.stdout", "team-b.stdout"):
        lines += z.read(name).decode("utf-8", "replace").replace("\x00", "").splitlines()
    start, end, awake, hits, names = {}, {}, defaultdict(dict), defaultdict(list), {}
    for line in lines:
        p = line.split()
        if len(p) < 2 or p[0] != "P":
            continue
        kind = p[1]
        if kind == "init":
            names[int(p[5])] = "%s%s" % (p[3], p[4])
            continue
        if kind not in ("s", "e", "a", "h") or len(p) < 6:
            continue
        r, side, role, cid = int(p[2]), p[3], p[4], int(p[5])
        if kind == "s":
            start[(r, cid)] = (float(p[6]), int(p[7]), int(p[8]), side, role)
        elif kind == "e":
            end[(r, cid)] = (int(p[6]), int(p[7]))
        elif kind == "a":
            awake[r][(int(p[6]), int(p[7]))] = float(p[8])
        elif kind == "h":
            hits[r].append((cid, (int(p[6]), int(p[7]))))
    return start, end, awake, hits, names


def dists(a, b):
    dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
    return max(dx, dy), math.hypot(dx, dy), dx + dy


def on_spawn(side, x, y):
    x0, x1, y0, y1 = SPAWNS[side]
    return x0 <= x <= x1 and y0 <= y <= y1


def turn_rank(cid, r, team_a):
    """Position in round r's turn order: team A (ids 0..n-1) moves first in odd rounds, team B in even
    ones (read off the probe's own logs: in round 45 a team B hit lands between two team A turns
    after team A's first body, and in round 44 every body already sees it), then draft order."""
    first_is_a = r % 2 == 1
    return (0 if (cid in team_a) == first_is_a else 1, cid)


def predict(near, metric, tiebreak, last_attacker, r, team_a):
    """near: [(dists, cid)]. Returns the predicted cid, or None when the rule cannot decide."""
    best = min(d[metric] for d, c in near)
    tied = sorted(c for d, c in near if abs(d[metric] - best) < 1e-9)
    if len(tied) == 1:
        return tied[0]
    if tiebreak == "lowest id":
        return tied[0]
    if tiebreak == "highest id":
        return tied[-1]
    if tiebreak == "attacker, else lowest id":
        return last_attacker if last_attacker in tied else tied[0]
    if tiebreak == "attacker, else turn order":
        return last_attacker if last_attacker in tied else min(tied, key=lambda c: turn_rank(c, r, team_a))
    return None


HYPOTHESES = [(m, mi, tb) for mi, m in enumerate(("chebyshev", "euclidean", "manhattan"))
              for tb in ("none", "lowest id", "highest id", "attacker, else lowest id", "attacker, else turn order")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("replay")
    ap.add_argument("--reach", type=int, default=3)
    ap.add_argument("--rounds", default=None)
    ap.add_argument("--all", action="store_true", help="one line per round instead of stretches")
    args = ap.parse_args()
    start, end, awake, hits, names = load(args.replay)
    ids = sorted({cid for (_, cid) in start})
    rounds = sorted({r for (r, _) in start})
    lo, hi = (map(int, args.rounds.split("-")) if args.rounds else (rounds[0], rounds[-1]))
    label = lambda cid: "%s#%d" % (names.get(cid, "?"), cid)

    max_hp = defaultdict(float)
    for r, camps in awake.items():
        for pos, hp in camps.items():
            max_hp[pos] = max(max_hp[pos], hp)
    harmful = lambda pos: max_hp[pos] > HARMLESS_HP
    team_a = {cid for (r, cid), s in start.items() if s[3] == "L"}

    last_attacker = {}                 # camp -> cid of the last champion that hit it
    score = {h: [0, 0, 0] for h in HYPOTHESES}
    rows = []
    for r in rounds:
        for cid, cpos in hits.get(r - 1, []):
            last_attacker[cpos] = cid
        if r < lo or r > hi or r - 1 not in rounds:
            continue
        lost = {}
        for cid in ids:
            a, b = start.get((r - 1, cid)), start.get((r, cid))
            if a is None or b is None:
                continue
            if b[0] < a[0] - 0.01 and not on_spawn(a[3], a[1], a[2]):
                lost[cid] = a[0] - b[0]
        # A camp acts at round start only if it was already awake: one woken by a hit during round
        # r-1 shows up awake in r-1's later turn-start lines, one woken during round r does not act
        # until r+1 (its awake lines in r come from bodies that moved after the hit).
        before = set(awake.get(r - 1, {})) | {cpos for cid, cpos in hits.get(r - 1, [])}
        camps = {p: hp for p, hp in awake.get(r, {}).items() if harmful(p) and p in before}
        if not camps and not lost:
            continue
        where = {cid: end[(r - 1, cid)] for cid in ids if (r - 1, cid) in end}
        parts = []
        for cpos, chp in sorted(camps.items()):
            near = sorted((dists(where[c], cpos), c) for c in where if dists(where[c], cpos)[0] <= args.reach)
            parts.append("wolf%s: %s" % (cpos, ", ".join(
                "%s %d/%.2f/%d" % (label(c), d[0], d[1], d[2]) for d, c in near) or "nobody in reach"))
            if near and len(camps) == 1:
                victims = sorted(lost)
                for h in HYPOTHESES:
                    p = predict(near, h[1], h[2], last_attacker.get(cpos), r, team_a)
                    if p is None:
                        score[h][2] += 1
                    elif victims == [p]:
                        score[h][0] += 1
                    else:
                        score[h][1] += 1
        hit_by = sorted({label(c) for c, cpos in hits.get(r - 1, [])})
        rows.append((r, " ; ".join(parts) or "no damaging camp awake",
                     ", ".join("%s -%.0f" % (label(c), v) for c, v in sorted(lost.items())) or "nobody",
                     (" [hit by %s]" % ",".join(hit_by)) if hit_by else ""))

    print("Distances are chebyshev/euclidean/manhattan from the end of the previous round.")
    merged = []
    for row in rows:
        if not args.all and merged and merged[-1][2:] == row[1:] and merged[-1][1] == row[0] - 1:
            merged[-1][1] = row[0]
        else:
            merged.append([row[0], row[0], *row[1:]])
    for a, b, picture, lost, hit in merged:
        span = ("r%d" % a) if a == b else ("r%d-%d" % (a, b))
        print("%-9s %s  =>  lost: %s%s" % (span, picture, lost, hit))
    print()
    print("Hypothesis: the camp hits the nearest champion in reach (rounds with one damaging camp awake)")
    print("  %-10s %-26s %5s %5s %9s" % ("metric", "tie-break", "right", "wrong", "undecided"))
    for (m, mi, tb), (right, wrong, und) in score.items():
        print("  %-10s %-26s %5d %5d %9d" % (m, tb, right, wrong, und))


if __name__ == "__main__":
    main()
