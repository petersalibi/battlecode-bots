"""Purchase sequences of top winners: per champion type, the (family, stat) of each shard bought, in order."""
import json, gzip, os, sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
FAM = ["MONSTER", "POWER", "DURABILITY", "TREASURE", "JUNGLE", "HOME", "BRUISER"]
STAT = ["STRENGTH", "FOCUS", "HEALTH"]
BASE = "data/replays/1.0.1"

def one(item):
    mid, v, winners = item
    f = f"{BASE}/ladder25/{mid}.jsonl.gz"
    if not os.path.exists(f): return []
    win = {0 if t["side"] == "a" else 1 for t in v["teams"] if t["result"] == "win" and (t.get("rating") or 0) >= winners}
    if not win: return []
    last = {}; seqs = defaultdict(list); types = {}; first_buy = {}
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        next(fh)
        for l in fh:
            r = json.loads(l)
            if r.get("kind") != "turn" or r["team"] not in win: continue
            s = r["state"]["self"] or {}
            items = s.get("items"); key = (r["team"], r["champ"])
            if items is None: continue
            types[key] = s.get("type")
            p = last.get(key)
            if p is not None:
                for i, (a, b) in enumerate(zip(p, items)):
                    if b > a:
                        fam, rest = divmod(i, 6); stat, emb = divmod(rest, 2)
                        if emb == 0:
                            for _ in range(b - a): seqs[key].append((FAM[fam], STAT[stat], r["round"]))
            last[key] = items
    return [(types[k], seq) for k, seq in seqs.items()]

if __name__ == "__main__":
    idx = json.load(open(f"{BASE}/raw25/index.json", encoding="utf-8"))
    rs = sorted(t.get("rating") or 0 for v in idx.values() for t in v["teams"])
    cut = rs[len(rs) // 2]
    items = [(m, v, cut) for m, v in idx.items()]
    by = defaultdict(list)
    with ProcessPoolExecutor(max_workers=int(os.environ.get("BC_JOBS", 8))) as ex:
        for res in ex.map(one, items, chunksize=2):
            for t, seq in res: by[t].append(seq)
    json.dump({str(k): v for k, v in by.items()}, open(sys.argv[1], "w"))
    names = ["RYTH","VESPER_VALE","VENDETTA","CASSI","ISSAC","STELLAR","PRISM","MARCELLA","WILLOW","REN","VARG","TAVI","HEART_OF_RUIN"]
    for t in sorted(by, key=lambda x: -len(by[x])):
        seqs = by[t]
        if len(seqs) < 4: continue
        print(f"== {names[t] if t is not None and t < len(names) else t}: {len(seqs)} champions, shards per game median {sorted(len(s) for s in seqs)[len(seqs)//2]}")
        tot = Counter((f, s) for seq in seqs for f, s, _ in seq)
        st_tot = Counter(s for seq in seqs for _, s, _ in seq)
        n = sum(st_tot.values())
        print("   stat share:", {k: f"{100*v/n:.0f}%" for k, v in st_tot.most_common()})
        # order: for slot i (0..19), most common (family, stat)
        order = []
        for i in range(0, 30, 5):
            c = Counter((seq[i][0], seq[i][1]) for seq in seqs if len(seq) > i)
            rounds = sorted(seq[i][2] for seq in seqs if len(seq) > i)
            if c: order.append(f"#{i+1}: {c.most_common(2)} (r{rounds[len(rounds)//2]})")
        print("   " + "\n   ".join(order))
