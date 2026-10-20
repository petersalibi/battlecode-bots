"""Play two bots against each other across maps, from both sides, and report the result.

    python tools/run_matches.py hybrid starter-python
    python tools/run_matches.py hybrid hybrid@hybrid-v1 --maps corners,lanes --jobs 4
    python tools/run_matches.py hybrid hybrid --sides a            # mirror match

Bots are folder names under bots/ (or paths). `name@gitref` plays the bot as committed at a
tag, branch or commit, so the working copy can be measured against a frozen earlier version.
Maps are names under maps/ (or paths); the default is every map. Each pair is played with the
first bot as team A and again as team B, because turn order alternates and team B wins full ties.

Matches run through `cambc run-batch --release`, which matches the platform. Each match's
statistics JSON (only written for matches that finish) and replay bundle land in the output
directory, with a results.json summarising everything the report prints.

Reported per match: winner, tributed gems, rounds, deaths, kills, bot faults, fuel exhaustions,
and the peak fuel per turn each side logged (from the `max=` field of the hybrid bot's log lines).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOTS = os.path.join(ROOT, "bots")
MAPS = os.path.join(ROOT, "maps")

FUEL_RE = re.compile(rb"\bmax=(\d+)")
ERROR_RE = re.compile(rb"^bot error", re.M)
STALL_RE = re.compile(rb"^r\d+ \w+: stuck ", re.M)


def find_cambc():
    for candidate in (os.path.join(ROOT, ".venv", "Scripts", "cambc.exe"),
                      os.path.join(ROOT, ".venv", "bin", "cambc"),
                      shutil.which("cambc")):
        if candidate and os.path.exists(candidate):
            return candidate
    sys.exit("cambc not found: install it in .venv or on PATH")


def resolve_bot(name):
    if "@" in name:
        return export_bot(*name.split("@", 1))
    for path in (name, os.path.join(BOTS, name)):
        if os.path.isdir(os.path.join(path, "src")):
            return os.path.abspath(path)
    sys.exit(f"bot not found: {name} (expected a folder with src/ under bots/, or name@gitref)")


def export_bot(name, ref):
    """`name@ref` plays the bot as it was at a git tag, branch or commit, exported to a cache."""
    import io
    import tarfile
    commit = subprocess.run(["git", "rev-parse", "--short", ref + "^{commit}"], cwd=ROOT,
                            capture_output=True, text=True)
    if commit.returncode:
        sys.exit(f"unknown git ref {ref}: {commit.stderr.strip()}")
    target = os.path.join(ROOT, "replays", "batch", "_bots", f"{name}@{ref}-{commit.stdout.strip()}")
    if not os.path.isdir(os.path.join(target, "src")):
        archive = subprocess.run(["git", "archive", "--format=tar", ref, f"bots/{name}"], cwd=ROOT,
                                 capture_output=True)
        if archive.returncode or not archive.stdout:
            sys.exit(f"git archive failed for bots/{name} at {ref}: {archive.stderr.decode().strip()}")
        os.makedirs(target, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
            for member in tar.getmembers():
                member.name = member.name[len(f"bots/{name}/"):]
                if member.name:
                    tar.extract(member, target, filter="data")
        print(f"exported bots/{name} at {ref} ({commit.stdout.strip()}) to {target}")
    return target


def resolve_maps(spec):
    if not spec:
        names = sorted(f for f in os.listdir(MAPS) if f.endswith(".map26a"))
        return [os.path.join(MAPS, f) for f in names]
    maps = []
    for name in spec.split(","):
        name = name.strip()
        for path in (name, os.path.join(MAPS, name), os.path.join(MAPS, name + ".map26a")):
            if os.path.isfile(path):
                maps.append(os.path.abspath(path))
                break
        else:
            sys.exit(f"map not found: {name}")
    return maps


def map_name(path):
    return os.path.basename(path).rsplit(".", 1)[0]


def team_stdout_stats(bundle, team):
    """Peak fuel per body and the number of logged bot errors, from the replay bundle."""
    try:
        with zipfile.ZipFile(bundle) as z:
            data = z.read(f"team-{team}.stdout").replace(b"\x00", b"")
    except (OSError, KeyError, zipfile.BadZipFile):
        return None, 0, 0
    peaks = [int(m) for m in FUEL_RE.findall(data)]
    return (max(peaks) if peaks else None), len(ERROR_RE.findall(data)), len(STALL_RE.findall(data))


def read_match(stats_path, bundle, a_is_team_a):
    """One row of the report, always from the first bot's point of view."""
    row = {"finished": False}
    mine, theirs = ("teamA", "teamB") if a_is_team_a else ("teamB", "teamA")
    for side, team in (("a", mine), ("b", theirs)):
        peak, errors, stalls = team_stdout_stats(bundle, "a" if team == "teamA" else "b")
        row[f"peak_fuel_{side}"] = peak
        row[f"errors_{side}"] = errors
        row[f"stalls_{side}"] = stalls
    if not os.path.exists(stats_path):
        return row
    with open(stats_path, encoding="utf-8") as f:
        d = json.load(f)
    row["finished"] = True
    row["rounds"] = d["match"]["roundsCompleted"]
    outcome = d["match"]["outcome"]
    row["result"] = "win" if outcome == mine else "loss" if outcome == theirs else "draw"
    gems = {t["team"]: int(t["submittedGems"]) for t in d["teams"]}
    row["gems_a"], row["gems_b"] = gems.get(mine, 0), gems.get(theirs, 0)
    for side, team in (("a", mine), ("b", theirs)):
        champs = [c for c in d["champions"] if c["team"] == team]
        row[f"deaths_{side}"] = sum(int(c["metrics"].get("deaths", 0)) for c in champs)
        row[f"kills_{side}"] = sum(int(c["metrics"].get("kills", 0)) for c in champs)
        row[f"gold_{side}"] = sum(int(c["metrics"].get("earnedGold", 0)) for c in champs)
        row[f"faults_{side}"] = d["diagnostics"]["botFaults"].get(team, 0)
        row[f"exhausted_{side}"] = sum(
            len(e["entries"]) for e in d["events"]
            if e["kind"] == "fuelExhausted" and e["owner"].get("team") == team)
        row[f"roster_{side}"] = [c["name"] for c in champs]
    return row


def thousands(value, width):
    return ("-" if value is None else str(round(value / 1000))).rjust(width)


def print_report(bot_a, bot_b, rows):
    name_a, name_b = os.path.basename(bot_a), os.path.basename(bot_b)
    print(f"\n{name_a} (A) vs {name_b} (B); paired columns are A/B\n")
    print(f"{'map':<14}{'side':<6}{'result':<11}{'gems':>12}{'rounds':>8}{'deaths':>8}{'kills':>8}"
          f"{'faults':>8}{'exh':>6}{'fuelMax(K)':>14}")
    for r in rows:
        if not r["finished"]:
            line = f"{r['map']:<14}{r['side']:<6}{'unfinished':<11}{'':>12}{'':>8}{'':>8}{'':>8}{'':>8}{'':>6}"
        else:
            line = (f"{r['map']:<14}{r['side']:<6}{r['result']:<11}"
                    f"{str(r['gems_a']) + '-' + str(r['gems_b']):>12}{r['rounds']:>8}"
                    f"{str(r['deaths_a']) + '/' + str(r['deaths_b']):>8}"
                    f"{str(r['kills_a']) + '/' + str(r['kills_b']):>8}"
                    f"{str(r['faults_a']) + '/' + str(r['faults_b']):>8}"
                    f"{str(r['exhausted_a']) + '/' + str(r['exhausted_b']):>6}")
        line += f"{thousands(r['peak_fuel_a'], 8)}/{thousands(r['peak_fuel_b'], 5)}"
        if r["errors_a"] or r["errors_b"]:
            line += f"   errors {r['errors_a']}/{r['errors_b']}"
        if r["stalls_a"] or r["stalls_b"]:
            line += f"   stalls {r['stalls_a']}/{r['stalls_b']}"
        print(line)

    done = [r for r in rows if r["finished"]]
    if not done:
        print("\nno finished matches (statistics are only written for matches that run to the end)")
        return {}
    wins = sum(r["result"] == "win" for r in done)
    losses = sum(r["result"] == "loss" for r in done)
    rounds = max(1, sum(r["rounds"] for r in done))
    margin = sum(r["gems_a"] - r["gems_b"] for r in done) / len(done)
    rate_a = sum(r["gems_a"] for r in done) / rounds * 100
    rate_b = sum(r["gems_b"] for r in done) / rounds * 100
    peaks = [r["peak_fuel_a"] for r in rows if r["peak_fuel_a"]]
    summary = {
        "matches": len(done), "wins": wins, "losses": losses, "draws": len(done) - wins - losses,
        "win_rate": wins / len(done), "avg_gem_margin": margin,
        "gems_per_100_rounds_a": rate_a, "gems_per_100_rounds_b": rate_b,
        "deaths_a": sum(r["deaths_a"] for r in done), "deaths_b": sum(r["deaths_b"] for r in done),
        "faults_a": sum(r["faults_a"] for r in done), "exhausted_a": sum(r["exhausted_a"] for r in done),
        "errors_a": sum(r["errors_a"] for r in rows), "stalls_a": sum(r["stalls_a"] for r in rows),
        "peak_fuel_a": max(peaks) if peaks else None,
    }
    print(f"\n{name_a}: {wins}-{losses}-{summary['draws']} ({summary['win_rate']:.0%}) over {len(done)} matches, "
          f"avg gem margin {margin:+.0f}, gems per 100 rounds {rate_a:.1f} vs {rate_b:.1f}")
    print(f"{name_a}: deaths {summary['deaths_a']} (vs {summary['deaths_b']}), faults {summary['faults_a']}, "
          f"fuel exhaustions {summary['exhausted_a']}, logged errors {summary['errors_a']}, "
          f"stalls {summary['stalls_a']}, peak fuel {thousands(summary['peak_fuel_a'], 1).strip()}K")
    unfinished = len(rows) - len(done)
    if unfinished:
        print(f"{unfinished} matches did not finish (truncated with --rounds, timed out, or failed)")
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bot_a")
    ap.add_argument("bot_b")
    ap.add_argument("--maps", help="comma-separated map names (default: every map in maps/)")
    ap.add_argument("--sides", choices=("both", "a", "b"), default="both",
                    help="play bot_a as team A, team B, or both (default)")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--rounds", type=int, help="stop early (no statistics are written for truncated matches)")
    ap.add_argument("--timeout", type=int, default=600, help="wall-clock seconds per match")
    ap.add_argument("--out", help="output directory (default: replays/batch/<timestamp>)")
    ap.add_argument("--debug", action="store_true", help="debug build instead of release")
    args = ap.parse_args()

    cambc = find_cambc()
    bot_a, bot_b = resolve_bot(args.bot_a), resolve_bot(args.bot_b)
    maps = resolve_maps(args.maps)
    out = os.path.abspath(args.out or os.path.join(ROOT, "replays", "batch", time.strftime("%Y%m%d-%H%M%S")))
    os.makedirs(out, exist_ok=True)

    jobs, lines = [], []
    for m in maps:
        for side in ("A", "B"):
            if (args.sides == "a" and side == "B") or (args.sides == "b" and side == "A"):
                continue
            first, second = (bot_a, bot_b) if side == "A" else (bot_b, bot_a)
            stem = os.path.join(out, f"{map_name(m)}-{side}")
            jobs.append({"map": map_name(m), "side": side, "bundle": stem + ".repl26a", "stats": stem + ".json"})
            lines.append(f'"{first}" "{second}" "{m}" "{stem}.repl26a" --statistics "{stem}.json" --json')
    manifest = os.path.join(out, "manifest.txt")
    with open(manifest, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    cmd = [cambc, "run-batch", manifest, "--jobs", str(args.jobs), "--json", "--timeout", str(args.timeout)]
    if not args.debug:
        cmd.append("--release")
    if args.rounds:
        cmd += ["--rounds", str(args.rounds)]
    print(f"{len(jobs)} matches on {len(maps)} maps, {args.jobs} jobs, output in {out}")
    started = time.time()
    proc = subprocess.run(cmd, cwd=ROOT)
    print(f"runner finished in {time.time() - started:.0f}s (exit {proc.returncode})")

    rows = []
    for job in jobs:
        row = read_match(job["stats"], job["bundle"], job["side"] == "A")
        row.update(map=job["map"], side=job["side"])
        rows.append(row)
    summary = print_report(bot_a, bot_b, rows)
    with open(os.path.join(out, "results.json"), "w", encoding="utf-8") as f:
        json.dump({"bot_a": bot_a, "bot_b": bot_b, "release": not args.debug, "rounds": args.rounds,
                   "summary": summary, "matches": rows}, f, indent=1)
    print(f"results written to {os.path.join(out, 'results.json')}")


if __name__ == "__main__":
    main()
