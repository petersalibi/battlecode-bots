"""Summarise how the first bot's bodies die and spend their time in a run_matches.py output folder.

    python tools/death_report.py replays/batch/<run> [replays/batch/<other run> ...]

For each folder: deaths by killer (camp or champion), the mode the body logged last before dying,
its health and visible enemies on that line, deaths per champion and per map, plus how often each
mode was entered and how many camps were cleared, so a tuning change can be judged on more than
the win count.
"""
import collections
import glob
import json
import os
import re
import sys
import zipfile

LINE_RE = re.compile(rb"^r(\d+) ([A-Z_]+): (\w+) hp=(\d+)% gems=(\d+) gold=\d+ target=-?\d+ enemies=(\d+)", re.M)


def report(folder):
    ctx = collections.Counter()
    modes = collections.Counter()
    hp_at = collections.Counter()
    seen = collections.Counter()
    per_champ = collections.Counter()
    per_map = collections.Counter()
    entered = collections.Counter()
    deaths = kills = clears = subs = games = 0
    for js in sorted(glob.glob(os.path.join(folder, "*-[AB].json"))):
        name = os.path.basename(js)[:-5]
        side = name[-1]
        team = "teamA" if side == "A" else "teamB"
        games += 1
        d = json.load(open(js, encoding="utf-8"))
        try:
            out = zipfile.ZipFile(js[:-5] + ".repl26a").read(f"team-{side.lower()}.stdout").replace(b"\x00", b"")
        except (OSError, KeyError):
            out = b""
        lines = collections.defaultdict(list)
        for m in LINE_RE.finditer(out):
            lines[m.group(2).decode()].append((int(m.group(1)), m.group(3).decode(), int(m.group(4)),
                                               int(m.group(5)), int(m.group(6))))
            entered[m.group(3).decode()] += 1
        for e in d["events"]:
            if e["owner"].get("team") != team or not e["entries"]:
                continue
            n = len(e["entries"])
            if e["kind"] == "kill":
                kills += n
            elif e["kind"] == "campClear":
                clears += n
            elif e["kind"] == "gemSubmission":
                subs += n
            elif e["kind"] == "death":
                champ = e["owner"]["name"].upper().replace(" ", "_").replace("&", "AND")
                for x in e["entries"]:
                    deaths += 1
                    ctx[x.get("context")] += 1
                    per_map[name[:-2]] += 1
                    per_champ[e["owner"]["name"]] += 1
                    r = x["timestamp"]["round"]
                    hist = [l for l in lines.get(champ, []) if l[0] <= r]
                    if hist:
                        last = hist[-1]
                        modes[last[1]] += 1
                        hp_at[(last[2] // 25) * 25] += 1
                        seen[min(last[4], 3)] += 1
    print(f"\n== {folder}: {games} games")
    print(f"deaths {deaths} ({deaths / max(games, 1):.1f} per game), kills {kills}, camp clears {clears}, tributes {subs}")
    print("killed by:", dict(ctx.most_common()))
    print("mode logged last before death:", dict(modes.most_common()))
    print("health bucket on that line:", dict(sorted(hp_at.items())))
    print("enemies visible on that line (3 = 3 or more):", dict(sorted(seen.items())))
    print("mode changes logged:", dict(entered.most_common()))
    print("deaths per champion:", dict(per_champ.most_common()))
    print("deaths per map:", dict(per_map.most_common(8)))


if __name__ == "__main__":
    for folder in sys.argv[1:] or ["replays/batch"]:
        report(folder)
