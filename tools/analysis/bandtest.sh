#!/bin/bash
# bandtest.sh <tag> <wait-until HH:MM UTC> <compare list...>: wait, tag HEAD, submit, push, replay 14 band groups, collect, compare.
set -u
cd "$(dirname "$0")/../.."
T=tools/analysis
W=data/analysis
mkdir -p "$W"
TAG=$1; UNTIL=$2; shift 2
python -c "
import time, datetime, sys
h, m = map(int, sys.argv[1].split(':'))
now = datetime.datetime.now(datetime.timezone.utc)
t = now.replace(hour=h, minute=m, second=0, microsecond=0)
w = (t - now).total_seconds()
if w < -12 * 3600: w += 86400
print(f'waiting {max(0, w)/60:.1f} min', flush=True); time.sleep(max(0, w))" "$UNTIL"
test -e bots/hybrid/src/profile.py && { echo "profile.py present, refusing to submit"; exit 1; }
git tag "hybrid-$TAG" HEAD && ./.venv/Scripts/cambc submit bots/hybrid 2>&1 | tail -1 && echo "$TAG submitted $(date -u +%H:%M) at $(git rev-parse --short HEAD)"
git push origin testing 2>&1 | tail -1; git push origin "hybrid-$TAG" 2>&1 | tail -1
python -c "import time; time.sleep(120)"
python tools/challenges.py queue -o "data/challenges/$TAG-band200" --replay-from data/challenges/v14-band200 --limit 14 --label "$TAG" 2>&1 | grep -v "^queue \[" | tail -2
for i in $(seq 1 60); do out=$(python tools/challenges.py collect -o "data/challenges/$TAG-band200" 2>/dev/null | tail -1); echo "$(date -u +%H:%M) $out"; echo "$out" | grep -q " 0 still queued" && break; python -c "import time; time.sleep(45)"; done
ARGS=""; for v in "$@"; do ARGS="$ARGS $v=data/challenges/$v-band200"; done
python "$T/compare_runs.py" $ARGS "$TAG=data/challenges/$TAG-band200" 2>&1 | grep -v "team-games\|^0/"
python "$T/fightstats.py" "data/challenges/$TAG-band200" raw ladder teto "$W/fs_us_$TAG.json" 2>/dev/null
python - "$W" "$TAG" <<'PY'
import json, os, sys
W, tag = sys.argv[1], sys.argv[2]
G = {n: json.load(open(os.path.join(W, f))) for n, f in (("v14", "fs_us14.json"), ("v24", "fs_us24.json"), (tag, f"fs_us_{tag}.json"), ("band", "fs_bvu.json")) if os.path.exists(os.path.join(W, f))}
def tot(g, k): return sum(r[k] for r in g)
print(f"\n{'contact turns':34}" + "".join(f"{n:>9}" for n in G))
for lab, f in (("contact turns per game", lambda g: tot(g, "contact") / len(g)), ("% basic attack on a champion", lambda g: 100 * tot(g, "attacked") / tot(g, "contact")),
               ("% idle", lambda g: 100 * tot(g, "idle") / tot(g, "contact")), ("damage taken per contact turn", lambda g: tot(g, "dmg_taken") / tot(g, "contact")),
               ("damage dealt per contact turn", lambda g: tot(g, "dmg_dealt") / tot(g, "contact")), ("damage dealt / taken", lambda g: tot(g, "dmg_dealt") / max(1, tot(g, "dmg_taken")))):
    print(f"{lab:34}" + "".join(f"{f(g):>9.2f}" for g in G.values()))
PY
python "$T/contact_modes.py" "data/challenges/$TAG-band200"
