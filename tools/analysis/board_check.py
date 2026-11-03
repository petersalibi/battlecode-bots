"""How well each body's shared-memory picture (memory_codec.Board) matches where its allies really
were: the samit-sucks phase 2 check.

    python tools/run_matches.py samit-sucks hybrid@hybrid-v42 --debug --log-limit 20000000
    python tools/analysis/board_check.py replays/batch/<run>

Needs config.LOG_BOARD on and a debug build. Every body prints one `board` line a turn with its own
position after acting and the cell it holds for each ally. A team's stdout is in turn order, so the
truth for a belief is the ally's own latest line before it: the position the ally published, or
would have.
"""
import collections
import glob
import os
import re
import sys
import zipfile

CELLS = 16
LINE = re.compile(rb"^board r(\d+) s(\d+) (dead|\d+,\d+) (\d+)x(\d+)(.*)$")


def cell(x, y, w, h):
    return x * CELLS // w, y * CELLS // h


def check(text, t):
    latest = {}                              # slot -> (x, y) after its last turn, or None when dead
    before = {}                              # slot -> the same one turn earlier
    for raw in text.splitlines():
        m = LINE.match(raw.strip())
        if not m:
            continue
        rnd, slot, me, w, h, rest = int(m[1]), int(m[2]), m[3], int(m[4]), int(m[5]), m[6].decode()
        for part in rest.split():
            key, _, val = part.partition(":")
            if key == "e":
                x, y, count, age = map(int, val.split(","))
                t["sightings"] += 1
                t["sighting_age"][min(age, 20)] += 1
                continue
            k = int(key)
            x, y, age = map(int, val.split(","))
            truth = latest.get(k)
            if truth is None:
                t["ally_dead_or_unknown"] += 1
                continue
            t["beliefs"] += 1
            t["age"][min(age, 10)] += 1
            if cell(x, y, w, h) == cell(*truth, w, h):
                t["same_cell"] += 1
                t["same_by_age"][min(age, 10)] += 1
            err = max(abs(x - truth[0]), abs(y - truth[1]))
            t["tile_err"][min(err, 30)] += 1
            if age <= 1:
                t["fresh"] += 1
                prev = before.get(k)
                if cell(x, y, w, h) == cell(*truth, w, h) or (prev is not None and cell(x, y, w, h) == cell(*prev, w, h)):
                    t["fresh_same"] += 1      # the ally's cell now or one turn ago: within a cell and a round
        before[slot] = latest.get(slot)
        latest[slot] = None if me == b"dead" else tuple(map(int, me.split(b",")))
        t["lines"] += 1


def main():
    run = sys.argv[1]
    t = collections.defaultdict(int)
    for key in ("age", "same_by_age", "tile_err", "sighting_age"):
        t[key] = collections.Counter()
    bundles = sorted(glob.glob(os.path.join(run, "*.repl26a")))
    for bundle in bundles:
        with zipfile.ZipFile(bundle) as z:
            for team in "ab":
                name = f"team-{team}.stdout"
                if name in z.namelist():
                    check(z.read(name).replace(b"\x00", b""), t)
    if not t["beliefs"]:
        print(f"no board lines in {len(bundles)} bundles: LOG_BOARD on and --debug?")
        return
    n = t["beliefs"]
    print(f"{len(bundles)} games, {t['lines']} board lines, {n} beliefs about living allies")
    print(f"  same cell as the truth: {t['same_cell'] / n:.1%}")
    print(f"  read this round or last (age <= 1): {t['fresh'] / n:.1%}, of those in its cell now or a turn ago: "
          f"{t['fresh_same'] / max(1, t['fresh']):.1%}")
    print("  by age (rounds since the byte last held a position):")
    for age in sorted(t["age"]):
        c = t["age"][age]
        print(f"    {age:>2}{'+' if age == 10 else ' '} {c:>8} {c / n:6.1%}  same cell {t['same_by_age'][age] / c:6.1%}")
    errs = t["tile_err"]
    print("  tile error (Chebyshev): " + ", ".join(f"<={e} {sum(c for k, c in errs.items() if k <= e) / n:.1%}"
                                                   for e in (0, 2, 5, 10)))
    if t["sightings"]:
        ages = t["sighting_age"]
        print(f"  sightings held: {t['sightings']}, age <= 1: {(ages[0] + ages[1]) / t['sightings']:.1%}")


if __name__ == "__main__":
    main()
