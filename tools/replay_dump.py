"""Re-simulate replays with the visualiser's engine and dump per-champion-turn state.

    python tools/replay_dump.py <replay.repl26a | match.zip | bundle.repl26a | folder> [...] -o data/replays/ --jobs 12

Accepts the platform's match zips (`cambc matches download`), local harness bundles, bare
replay files, or folders of them. The replay's engine version (bytes 8-10 of the header) picks
the matching engine wasm from replays/dev/viewer/; a patch-level mismatch falls back to the newest
wasm of the same major.minor. Each replay becomes <name>.jsonl.gz, see tools/replay_dump.mjs for
the record layout. Replays already dumped are skipped. Pass --probe to run the first few hundred
inputs with diagnostics.
"""
import argparse
import gzip
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from progress import Progress  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIEWER = os.path.join(ROOT, "replays", "dev", "viewer")
NODE_DRIVER = os.path.join(ROOT, "tools", "replay_dump.mjs")


def replay_bytes(path):
    """The raw a62per replay from a zip/bundle, or the file itself."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.endswith(".repl26a") or name == "replay":
                    data = z.read(name)
                    if data[:6] == b"a62per":
                        return data
        raise ValueError(f"{path}: no replay inside the zip")
    data = open(path, "rb").read()
    if data[:6] != b"a62per":
        raise ValueError(f"{path}: not a replay")
    return data


def engine_for(data):
    """The engine wasm matching the replay's version bytes, or the newest of the same major.minor
    (the platform moved to 1.0.1 while the viewer ships 1.0.0), with a note on stderr."""
    major, minor, patch = data[8], data[9], data[10]
    version = "v%d_%d_%d" % (major, minor, patch)
    candidates = {}
    for name in os.listdir(VIEWER):
        m = re.match(r"v(\d+)_(\d+)_(\d+)-[^.]+\.wasm$", name)
        if m:
            candidates[tuple(int(v) for v in m.groups())] = os.path.join(VIEWER, name)
    if (major, minor, patch) in candidates:
        return candidates[(major, minor, patch)], version
    same = sorted(v for v in candidates if v[:2] == (major, minor))
    if same:
        best = same[-1]
        sys.stderr.write("note: no engine wasm for %s, using v%d_%d_%d\n" % (version, best[0], best[1], best[2]))
        return candidates[best], version
    raise FileNotFoundError("no engine wasm for %s in %s" % (version, VIEWER))


def dump(path, out_dir, probe=False, verbose=True):
    data = replay_bytes(path)
    wasm, version = engine_for(data)
    name = os.path.splitext(os.path.basename(path))[0]
    with tempfile.TemporaryDirectory() as tmp:
        raw = os.path.join(tmp, "replay.repl26a")
        with open(raw, "wb") as f:
            f.write(data)
        jsonl = os.path.join(tmp, name + ".jsonl")
        old_rules = data[8] == 0          # engine 0.x: pre-patch camp rewards
        cmd = ["node", NODE_DRIVER, raw, wasm, os.path.join(VIEWER, "chunks"), jsonl]
        if probe:
            cmd.append("--probe")
        if old_rules:
            cmd.append("--old-rules")
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        sys.stderr.write(proc.stderr)
        if proc.returncode:
            print(f"{name}: driver failed (exit {proc.returncode})")
            return None
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, name + ".jsonl.gz")
        with open(jsonl, "rb") as src, gzip.open(out, "wb") as dst:
            shutil.copyfileobj(src, dst)
    if verbose:
        print(f"{name}: engine {version} -> {out} ({os.path.getsize(out) // 1024} KB)")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("replays", nargs="+", help="replay files, match zips, bundles or folders of them")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "data", "replays"))
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--jobs", type=int, default=int(os.environ.get("BC_JOBS", 8)), help="parallel workers (default: the BC_JOBS environment variable, else 8)")
    args = ap.parse_args()
    paths = []
    for path in args.replays:
        if os.path.isdir(path):
            paths += [os.path.join(path, f) for f in sorted(os.listdir(path)) if f.endswith((".zip", ".repl26a"))]
        else:
            paths.append(path)
    todo = [p for p in paths if not os.path.exists(os.path.join(args.out, os.path.splitext(os.path.basename(p))[0] + ".jsonl.gz"))]
    print(f"{len(todo)} replays to dump ({len(paths) - len(todo)} already done)")
    bar = Progress(len(todo), "dumping")

    def one(p):
        out = dump(p, args.out, args.probe, verbose=args.probe)
        bar.step(note=os.path.basename(p))
        return out
    if args.jobs > 1 and len(todo) > 1:
        with ThreadPoolExecutor(args.jobs) as pool:
            done = [o for o in pool.map(one, todo) if o]
    else:
        done = [o for o in map(one, todo) if o]
    bar.done()
    print(f"{len(done)} replays dumped to {args.out}")


if __name__ == "__main__":
    main()
