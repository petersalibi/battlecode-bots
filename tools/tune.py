"""Tier-1 parameter sweep: the working copy with one config constant at several values, each
against a tagged version on every map from both sides (tools/run_matches.py), scored by the paired
gem margin and wins. Prints a table and writes <out>/tune.json.

    python tools/tune.py --vs hybrid-v35 STAND_FUTILE_MIN_HEALTH=0.5,0.35,0.25
    python tools/tune.py --vs hybrid-v35 DOOMED_TURNS=1,2,3 --jobs 8 --maps 00collision,00corners

Several NAME=a,b,c arguments sweep the cross product. The constant is rewritten in
bots/hybrid/src/config.py for each run (the line must read `NAME = value`), and the file is
restored afterwards, also on Ctrl-C. A 70-game sweep is good to about +-8 wins and +-60 gems of
margin, so this tier only throws out points that clearly lose; the band (tools/challenges.py)
and the ladder decide between the survivors.
"""
import argparse, itertools, json, os, re, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "bots", "hybrid", "src", "config.py")
RUN = os.path.join(ROOT, "tools", "run_matches.py")


def set_constants(text, values):
    for name, value in values.items():
        pat = re.compile(r"^(%s\s*=\s*)([^#\n]*?)(\s*(#.*)?)$" % re.escape(name), re.M)
        if len(pat.findall(text)) != 1:
            raise SystemExit(f"{name}: expected exactly one `{name} = ...` line in config.py")
        text = pat.sub(lambda m: f"{m.group(1)}{value}{m.group(3)}", text)
    return text


def parse_value(s):
    for cast in (int, float):
        try:
            return cast(s)
        except ValueError:
            pass
    return {"True": True, "False": False}.get(s, s)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("params", nargs="+", help="NAME=v1,v2,... (several: cross product)")
    ap.add_argument("--vs", default="hybrid-v35", help="git tag or ref of the opponent (default hybrid-v35)")
    ap.add_argument("--jobs", type=int, default=int(os.environ.get("BC_JOBS", 8)))
    ap.add_argument("--maps", help="comma-separated map names (default: all)")
    ap.add_argument("--out", help="output directory (default replays/batch/tune-<time>)")
    ap.add_argument("--no-baseline", action="store_true", help="skip the run with the file's current values")
    args = ap.parse_args()
    grid = []
    for p in args.params:
        name, vals = p.split("=", 1)
        grid.append([(name, parse_value(v)) for v in vals.split(",")])
    points = [dict(c) for c in itertools.product(*grid)]
    out = args.out or os.path.join(ROOT, "replays", "batch", "tune-" + time.strftime("%m%d-%H%M"))
    os.makedirs(out, exist_ok=True)
    original = open(CONFIG, encoding="utf8").read()
    current = {}
    for name in {n for p in points for n in p}:
        m = re.search(r"^%s\s*=\s*([^#\n]*?)\s*(#.*)?$" % re.escape(name), original, re.M)
        current[name] = parse_value(m.group(1).strip()) if m else None
    if not args.no_baseline and current not in points:
        points.insert(0, current)
    results = []
    try:
        for i, point in enumerate(points):
            label = ",".join(f"{k}={v}" for k, v in point.items())
            print(f"[{i + 1}/{len(points)}] {label}", flush=True)
            open(CONFIG, "w", encoding="utf8", newline="\n").write(set_constants(original, point))
            run_dir = os.path.join(out, re.sub(r"[^A-Za-z0-9_.=-]", "_", label))
            cmd = [sys.executable, RUN, "hybrid", f"hybrid@{args.vs}", "--jobs", str(args.jobs), "--out", run_dir]
            if args.maps:
                cmd += ["--maps", args.maps]
            subprocess.run(cmd, check=True)
            s = json.load(open(os.path.join(run_dir, "results.json")))["summary"]
            results.append(dict(point=point, label=label, wins=s["wins"], losses=s["losses"], margin=s["avg_gem_margin"],
                                gems_a=s["gems_per_100_rounds_a"], gems_b=s["gems_per_100_rounds_b"],
                                deaths_a=s["deaths_a"], deaths_b=s["deaths_b"], exh=s["exhausted_a"], errors=s["errors_a"], stalls=s["stalls_a"]))
            json.dump(results, open(os.path.join(out, "tune.json"), "w"), indent=1)
    finally:
        open(CONFIG, "w", encoding="utf8", newline="\n").write(original)
    print()
    print(f"{'point':40s} {'wins':>7s} {'margin':>7s} {'gems/100':>12s} {'deaths':>11s} {'exh':>4s} {'err':>4s} {'stall':>6s}")
    for r in sorted(results, key=lambda r: -r["margin"]):
        print(f"{r['label']:40s} {r['wins']:3d}-{r['losses']:<3d} {r['margin']:+7.0f} {r['gems_a']:5.1f} vs {r['gems_b']:4.1f} "
              f"{r['deaths_a']:5d}/{r['deaths_b']:<5d} {r['exh']:4d} {r['errors']:4d} {r['stalls']:6d}")
    print(f"results in {out}")


if __name__ == "__main__":
    main()
