"""Fetch the visualiser's engine (WebAssembly) and decoder chunks that tools/replay_dump.mjs needs.

    python tools/fetch_viewer.py

Starts `cambc view` on any replay, downloads every engine wasm (one per engine version) and the
JavaScript chunks the bundle references into replays/dev/viewer/ (gitignored), then stops the
server. Re-run after upgrading cambc so a new engine version's wasm is available.
"""
import os
import re
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIEWER = os.path.join(ROOT, "replays", "dev", "viewer")
CAMBC = os.path.join(ROOT, ".venv", "Scripts", "cambc.exe")
PORT = 7039


def fetch(path):
    return urllib.request.urlopen(f"http://127.0.0.1:{PORT}/{path}", timeout=30).read()


def main():
    replay = None
    for folder in ("replays/batch", "replays/ladder", "replays"):
        for root, _, files in os.walk(os.path.join(ROOT, folder)):
            for f in files:
                if f.endswith(".repl26a") or f.endswith(".zip"):
                    replay = os.path.join(root, f)
                    break
            if replay:
                break
        if replay:
            break
    if not replay:
        sys.exit("no replay found to serve; run a match first")
    os.makedirs(os.path.join(VIEWER, "chunks"), exist_ok=True)
    server = subprocess.Popen([CAMBC, "view", replay, "--port", str(PORT)], cwd=ROOT,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(30):
            try:
                html = fetch("").decode()
                break
            except Exception:
                time.sleep(0.5)
        else:
            sys.exit("viewer did not start")
        index = re.search(r'assets/(index-[^"]+\.js)', html).group(1)
        bundle = fetch(f"assets/{index}").decode()
        open(os.path.join(VIEWER, "index-bundle.js"), "w", encoding="utf-8").write(bundle)
        for wasm in sorted(set(re.findall(r"v\d+_\d+_\d+-[A-Za-z0-9_-]+\.wasm", bundle))):
            data = fetch(f"assets/{wasm}")
            open(os.path.join(VIEWER, wasm), "wb").write(data)
            print(f"{wasm}: {len(data) // 1024} KB")
        for chunk in sorted(set(re.findall(r'"\./([A-Za-z0-9_-]+\.js)"', bundle))):
            data = fetch(f"assets/{chunk}")
            open(os.path.join(VIEWER, "chunks", chunk), "wb").write(data)
        print(f"viewer files in {VIEWER}")
    finally:
        server.terminate()


if __name__ == "__main__":
    main()
