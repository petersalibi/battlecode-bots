"""Fight execution per side: contact turns (an enemy champion within 4), what bodies do in them.

python fightstats.py <base> <raw> <ladder> <keep: teto|opp|band> <out.json> [band names]
"""
import json, gzip, os, sys
from collections import defaultdict, Counter
from concurrent.futures import ProcessPoolExecutor
BASE, RAW, LAD, WHO, OUT = sys.argv[1:6]
BAND = set(json.load(open(sys.argv[6]))) if len(sys.argv) > 6 else set()
US = "Teto Territory"
NAMES = ["RYTH","VESPER_VALE","VENDETTA","CASSI","ISSAC","STELLAR","PRISM","MARCELLA","WILLOW","REN","VARG","TAVI","HEART_OF_RUIN"]
RANGED = {"VESPER_VALE", "CASSI", "STELLAR", "PRISM", "WILLOW", "HEART_OF_RUIN", "TAVI"}   # checked against attack distances below
def cheb(a, b): return max(abs(a[0]-b[0]), abs(a[1]-b[1]))

def one(item):
    mid, v = item
    f = f"{BASE}/{LAD}/{mid}.jsonl.gz"
    if not os.path.exists(f): return None
    teams = {0 if t["side"] == "a" else 1: t for t in v["teams"]}
    if WHO == "teto": keep = [k for k, t in teams.items() if t["name"] == US]
    elif WHO == "opp": keep = [k for k, t in teams.items() if t["name"] != US] if any(t["name"] == US for t in teams.values()) else []
    else: keep = [k for k, t in teams.items() if t["name"] in BAND]
    if not keep: return None
    S = {t: dict(contact=0, attacked=0, cast=0, moved_away=0, moved_in=0, idle=0, dmg_taken=0.0, dmg_dealt=0.0,
                 rounds_multi=0, rounds_focused=0, lowest_pick=0, picks=0, dist_by_type=defaultdict(Counter),
                 first_strike=0, first_struck=0, contact_by_type=Counter(), attack_by_type=Counter(), cast_by_type=Counter()) for t in keep}
    prev = {}; in_contact = {}
    attacks_round = defaultdict(list)    # (round, team) -> target tiles
    hp_prev = {}
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        next(fh)
        for l in fh:
            r = json.loads(l)
            if r.get("kind") != "turn": continue
            rd, t, c = r["round"], r["team"], r["champ"]
            s = r["state"]["self"] or {}
            ok = s.get("alive", True) and s.get("x") is not None
            key = (t, c)
            if not ok:
                prev[key] = None; in_contact[key] = False; continue
            me = (s["x"], s["y"]); hp = s["hp"]
            enemies = [ch for ch in r["state"]["champs"] if ch["team"] != t]
            near = [ch for ch in enemies if cheb((ch["x"], ch["y"]), me) <= 4]
            nm = NAMES[s["type"]] if s.get("type") is not None and s["type"] < 13 else "?"
            # damage taken this turn while in contact (hp drop since our last turn)
            p = prev.get(key)
            if t in S and near:
                a = S[t]; a["contact"] += 1; a["contact_by_type"][nm] += 1
                if p is not None and p[1] > hp: a["dmg_taken"] += p[1] - hp
                ks = [x for x in r["actions"]]
                hit = [x for x in ks if x["k"] == 5 and "to" in x and any((ch["x"], ch["y"]) == tuple(x["to"]) for ch in enemies)]
                casts = [x for x in ks if x["k"] == 6]
                if hit:
                    a["attacked"] += 1; a["attack_by_type"][nm] += 1
                    tgt = tuple(hit[0]["to"]); attacks_round[(rd, t)].append(tgt)
                    a["dist_by_type"][nm][cheb(me, tgt)] += 1
                    in_reach = [ch for ch in enemies if cheb((ch["x"], ch["y"]), me) <= 3]
                    if len(in_reach) >= 2:
                        a["picks"] += 1
                        low = min(in_reach, key=lambda ch: ch["hp"] / max(1, ch["max"]))
                        if (low["x"], low["y"]) == tgt: a["lowest_pick"] += 1
                if casts: a["cast"] += 1; a["cast_by_type"][nm] += 1
                if p is not None and p[0] is not None:
                    dn = min(cheb(p[0], (ch["x"], ch["y"])) for ch in near); dn2 = min(cheb(me, (ch["x"], ch["y"])) for ch in near)
                    if dn2 > dn: a["moved_away"] += 1
                    elif dn2 < dn: a["moved_in"] += 1
                if not hit and not casts and p is not None and p[0] == me: a["idle"] += 1
                # first strike: this body just came into contact
                if not in_contact.get(key):
                    a["_new"] = a.get("_new", 0) + 1
            # damage dealt: enemies' hp drops are counted from their own turns as dmg_taken of the other side
            if near and (1 - t) in S and p is not None and p[1] > hp:
                S[1 - t]["dmg_dealt"] += p[1] - hp
            in_contact[key] = bool(near)
            prev[key] = (me, hp)
    for (rd, t), tiles in attacks_round.items():
        if t in S and len(tiles) >= 2:
            S[t]["rounds_multi"] += 1
            if max(Counter(tiles).values()) >= 2: S[t]["rounds_focused"] += 1
    out = []
    for t, a in S.items():
        a["dist_by_type"] = {k: dict(v_) for k, v_ in a["dist_by_type"].items()}
        for k in ("contact_by_type", "attack_by_type", "cast_by_type"): a[k] = dict(a[k])
        out.append(dict(map=v["map"], team=teams[t]["name"], result=teams[t]["result"], **a))
    return out

if __name__ == "__main__":
    idx = json.load(open(f"{BASE}/{RAW}/index.json", encoding="utf-8"))
    items = [(m, v) for m, v in idx.items() if v.get("status", "finished") == "finished"]
    rows = []
    with ProcessPoolExecutor(max_workers=int(os.environ.get("BC_JOBS", 8))) as ex:
        for r in ex.map(one, items, chunksize=2):
            if r: rows.extend(r)
    json.dump(rows, open(OUT, "w")); print(len(rows), "team-games", file=sys.stderr)
