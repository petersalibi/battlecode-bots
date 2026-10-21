"""Turn replay dumps (tools/replay_dump.py) into training rows: the bot's own 66 features, an
inferred mode label and an outcome label, for every champion turn of both teams.

    python tools/replay_features.py data/replays/*.jsonl.gz -o data/replay_modes.npz
    python tools/replay_features.py data/replays/00lanes-A.jsonl.gz --check data/replays/00lanes-A.nnlog.txt

The features come from the bot's own `features.build()` (bots/hybrid/src/features.py), fed an
offline World/Turn/Decider that mirrors what the bot computes in a match from the engine's
perspective state: fog-of-war champions, visible camps, distance fields precomputed by the
driver, and the script's own camp ranking (decide.score_camp). `--check` compares the rows for
our own bot against its in-match datalog of the same game, feature by feature, which is how the
offline path is validated.

Mode labels are inferred from the inputs the champion made in that turn (tribute, recall,
attacks on camps or champions, standing on a cleared camp, moving away from threats), since
only our own bot logs its mode. Outcome labels follow tools/build_dataset.py: gems tributed and
collected over the next HORIZON rounds minus a death penalty.
"""
import argparse
import gzip
import json
import math
import os
import re
import sys
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from progress import Progress  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "bots", "hybrid", "src")


# ---- import the bot's own modules without the engine ------------------------------------------
class _Stub:
    """Stands in for the cambc engine modules at import time; nothing here is called offline."""
    def __getattr__(self, name):
        return _Stub()

    def __call__(self, *a, **k):
        return _Stub()

    def __int__(self):
        return 0


for name in ("cambc", "_cambc", "cambc.game_map", "cambc.vision"):
    sys.modules.setdefault(name, _Stub())
sys.modules["_cambc"].fuel_remaining = lambda: 10_000_000
sys.path.insert(0, SRC)
import config  # noqa: E402
import decide  # noqa: E402
import features  # noqa: E402
import memory_codec  # noqa: E402

UNREACHABLE = 65535
HORIZON = 60
DEATH_PENALTY = 10.0
ABILITY_COOLDOWN = 8
# Step kinds in the engine's input stream (see the Cr table in the viewer bundle).
K_TICK, K_MOVE, K_ATTACK, K_CAST, K_END, K_RECALL, K_SUBMIT, K_SHOP, K_WARD = 3, 4, 5, 6, 8, 11, 12, 13, 14
# Camp base stats per type (week 2 rules): health, strength, attack range.
CAMP_STATS = {0: (1350, 20, 3), 1: (2400, 60, 4), 2: (120, 0, 0), 3: (120, 0, 0), 4: (20000, 200, 5), 5: (5000, 120, 4)}
CAMP_STATS_OLD = {0: (1350, 20, 3), 1: (2400, 60, 3), 2: (500, 0, 0), 3: (500, 0, 0), 4: (20000, 200, 3)}
# Champion kits for the dps estimate: (ranged, [(kind, modifier)]) with kind in damage/aoe/cc/reset.
KITS = {   # (ranged, [slot 0, slot 1]) with (kind, focus modifier); only damage/aoe/cc/reset feed the dps estimate
    0: (False, [("cc", 1.0), ("reset", 0)]),        # Ryth
    1: (True, [("reset", 0), ("dash", 0)]),         # Vesper Vale
    2: (False, [("reset", 0), ("dash", 0)]),        # Vendetta
    3: (True, [("damage", 2.0), ("cc", 0)]),        # Cassi
    4: (False, [("reset", 0), ("dash", 0)]),        # Issac
    5: (True, [("damage", 2.0), ("aoe", 1.4)]),     # Stellar
    6: (True, [("cc", 1.5), ("aoe", 1.0)]),         # Prism
    7: (False, [("reset", 0), ("damage", 2.0)]),    # Marcella
    8: (True, [("heal", 0), ("reset", 0)]),         # Willow
    9: (False, [("cc", 1.25), ("dash", 0)]),        # Ren
    10: (False, [("reset", 0), ("shield", 0)]),     # Varg
    11: (True, [("reset", 0), ("cc", 1.4)]),        # Tavi
    12: (False, [("shield", 0), ("dash", 0)]),      # Heart of Ruin
}
BUFF_SHIELD, BUFF_STUN = 0, 1
CONSUMABLE_ALTAR_LOCK = 0


class Vec:
    __slots__ = ("x", "y")

    def __init__(self, x, y):
        self.x, self.y = x, y

    def chebyshev_distance(self, other):
        return max(abs(self.x - other.x), abs(self.y - other.y))


class Group:
    def __init__(self, index, info, camps, old_rules):
        stats = CAMP_STATS_OLD if old_rules else CAMP_STATS
        self.index = index
        self.gems, self.gold, self.respawn = info["gems"], info["gold"], info["cooldown"]
        self.camp_indices = info["camps"]
        self.tile_list = [Vec(camps[c]["x"], camps[c]["y"]) for c in self.camp_indices]
        self.n_camps = len(self.camp_indices)
        self.kinds = [camps[c]["type"] for c in self.camp_indices]
        self.total_health = float(sum(stats.get(k, (1000, 50, 3))[0] for k in self.kinds))
        self.total_strength = float(sum(stats.get(k, (1000, 50, 3))[1] for k in self.kinds))
        self.attack_range = max(stats.get(k, (1000, 50, 3))[2] for k in self.kinds)
        self.harmless = self.total_strength == 0
        self.value = self.gems * config.GEM_VALUE + self.gold * config.GOLD_VALUE
        self.state = decide.W.ALIVE if hasattr(decide, "W") else 1
        self.reachable = True
        self.avoid_until = -1
        self.anchor = None
        self.respawn_round = -1
        self.seen_round = -1


class Cand:
    __slots__ = ("group", "travel", "clear_turns", "damage", "wait", "claimed", "score", "bodies")

    def __init__(self, group, travel, clear_turns, damage, wait, claimed):
        self.group, self.travel, self.clear_turns, self.damage = group, travel, clear_turns, damage
        self.wait, self.claimed, self.score, self.bodies = wait, claimed, 0.0, 1 + claimed


class Enemy:
    __slots__ = ("pos", "hp", "max_hp", "strength", "gems", "dist", "handle")

    def __init__(self, c, me):
        self.handle = c["i"]
        self.pos = Vec(c["x"], c["y"])
        self.hp, self.max_hp = float(c["hp"]), float(max(c["max"], 1))
        self.strength = float(c["str"] or 40)
        self.gems = c["gems"] or 0
        self.dist = me.chebyshev_distance(self.pos)


class Static:
    def __init__(self, rec, old_rules):
        self.width, self.height = rec["width"], rec["height"]
        self.camps = rec["camps"]
        self.groups = [Group(i, g, rec["camps"], old_rules) for i, g in enumerate(rec["groups"])]
        self.group_of_camp = {c: g.index for g in self.groups for c in g.camp_indices}
        self.camp_at = {(c["x"], c["y"]): c["index"] for c in rec["camps"]}
        self.bushes = {(x, y) for x, y in rec["bushes"]}
        self.blocked = {(x, y) for x, y in rec["walls"]} | {(x, y) for x, y in rec["holes"]} | {(x, y) for x, y in rec["altars"]}
        self.altars = [Vec(x, y) for x, y in rec["altars"]]
        self.altar_field = rec["altarField"]
        # Anchor-rooted fields per team (the anchor depends on the team's spawn); older dumps have one set.
        self.group_fields_by_team = [rec["groupFields"], rec.get("groupFieldsB") or rec["groupFields"]]
        self.group_fields = self.group_fields_by_team[0]
        a = rec["spawnA"]
        self.spawn_tiles = [set((x, y) for x in range(a["minX"], a["maxX"] + 1) for y in range(a["minY"], a["maxY"] + 1)), None]
        self.spawn_fields = [rec["spawnFieldA"], rec.get("spawnFieldB")]
        self._spawn_b = rec.get("spawnB")

    def finish(self, spawn_b_tiles):
        tiles = self._spawn_b or spawn_b_tiles
        self.spawn_tiles[1] = set(tuple(t) for t in tiles)
        if self.spawn_fields[1] is None:
            self.spawn_fields[1] = bfs(self, self.spawn_tiles[1])

    def field(self, f, pos):
        v = f[pos.y * self.width + pos.x] if f is not None else UNREACHABLE
        return v


def bfs(st, roots):
    """8-neighbour distance field over walkable tiles (walls, holes, altars and camps blocked)."""
    w, h = st.width, st.height
    blocked = st.blocked | set(st.camp_at)
    dist = [UNREACHABLE] * (w * h)
    frontier = [r for r in roots if r not in blocked or r in roots]
    for x, y in frontier:
        dist[y * w + x] = 0
    d = 0
    while frontier:
        nxt = []
        d += 1
        for x, y in frontier:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    nx, ny = x + dx, y + dy
                    if (dx or dy) and 0 <= nx < w and 0 <= ny < h and dist[ny * w + nx] == UNREACHABLE and (nx, ny) not in blocked:
                        dist[ny * w + nx] = d
                        nxt.append((nx, ny))
        frontier = nxt
    return dist


class Tracker:
    """Per-champion memory the bot keeps across turns."""
    def __init__(self):
        self.prev_hp = None
        self.prev_gems = 0
        self.last_enemy_round = -10_000
        self.last_enemy_pos = None
        self.enemies_killed = []
        self.decider = decide.Decider()
        self.ward_round = -10_000
        self.enemy_memory = {}         # enemy champ index -> (pos, round, hp_ratio), like World.enemy_memory
        self.last_camp_group = None    # group of the camp this body last attacked, and when
        self.last_camp_round = -10_000
        self.alive = True


class OfflineWorld:
    """The attributes features.build() and decide.score_camp() read, from a dumped turn."""
    recall_time = 5
    max_rounds = 2000
    team_deaths = {}          # the bot's claims-board death memory; not reconstructed offline

    def __init__(self, st, turn, tracker, team_sizes, prev_mode):
        s = turn["state"]
        me = s["self"]
        self.static = st
        self.round = turn["round"]
        self.team = turn["team"]
        self.slot = turn["champ"] - (team_sizes[0] if self.team == 1 else 0)
        self.n_bodies = team_sizes[self.team]
        self.width, self.height = st.width, st.height
        self.pos = Vec(me["x"], me["y"])
        self.hp, self.max_hp = float(me["hp"]), float(max(me["max"], 1))
        self.hp_ratio = self.hp / self.max_hp
        self.gems, self.gold = me["gems"] or 0, me["gold"] or 0
        self.strength, self.focus = float(me["str"] or 40), float(me["focus"] or 100)
        self.kind = me["type"]
        self.items = me["items"] or []
        self.cooldowns = list(me["cd"] or [0, 0])
        self.stunned = BUFF_STUN in (me["buffs"] or [])
        cons = me["cons"] or [0, 0, 0, 0]
        self.has_altar_lock = cons[CONSUMABLE_ALTAR_LOCK] > 0 if cons else False
        self.has_tp_crystal = any(c > 0 for c in cons[1:]) if cons else False
        self.recalling = self.tributing = self.teleporting = False
        self.channel_time = 0
        ranged, kit = KITS.get(self.kind, (False, []))
        self.is_ranged = ranged
        self.has_dash = any(k == "dash" for k, _m in kit)
        self.attack_range = 3 if ranged else 1
        self.abilities = [types.SimpleNamespace(slot=0), types.SimpleNamespace(slot=1)]
        dps = self.strength
        for kind, mod in kit:
            if kind in ("damage", "aoe", "cc") and mod > 0:
                dps += self.focus * mod / ABILITY_COOLDOWN
            elif kind == "reset":
                dps += self.strength / ABILITY_COOLDOWN
        self.dps = max(dps, 1.0)
        self.at_spawn = (self.pos.x, self.pos.y) in st.spawn_tiles[self.team]
        self.in_bush = (self.pos.x, self.pos.y) in st.bushes
        self.turns_to_altar = st.field(st.altar_field, self.pos)
        self.turns_to_spawn = st.field(st.spawn_fields[self.team], self.pos)
        self._enemy_spawn = st.field(st.spawn_fields[1 - self.team], self.pos)
        self.damage_taken = max(0.0, tracker.prev_hp - self.hp) if tracker.prev_hp is not None else 0.0
        self.events = set()
        # Champions in the perspective snapshot are the ones this body can see (or itself).
        self.enemies, self.allies = [], []
        for c in s["champs"]:
            if c["team"] == self.team:
                if c["i"] != turn["champ"]:
                    p = Vec(c["x"], c["y"])
                    self.allies.append((p, float(c["hp"]), float(c["str"] or 40), p.chebyshev_distance(self.pos)))
            else:
                self.enemies.append(Enemy(c, self.pos))
        self.nearest_enemy = min(self.enemies, key=lambda e: e.dist) if self.enemies else None
        for c in s["champs"]:
            if c["team"] != self.team:
                tracker.enemy_memory[c["i"]] = (Vec(c["x"], c["y"]), self.round, (c["hp"] or 0) / max(c["max"] or 1, 1))
        self.enemy_memory = tracker.enemy_memory
        self.enemy_power = sum(e.hp * e.strength for e in self.enemies)
        self.ally_power = self.hp * self.strength + sum(hp * strength for p, hp, strength, d in self.allies if d <= config.FIGHT_RANGE)
        if self.enemies:
            tracker.last_enemy_round = self.round
            tracker.last_enemy_pos = self.nearest_enemy.pos
        self.last_enemy_round, self.last_enemy_pos = tracker.last_enemy_round, tracker.last_enemy_pos
        self.enemies_killed = tracker.enemies_killed
        self.claims = [(memory_codec.STATUS_IDLE, -1)] * memory_codec.MAX_BODIES
        self.aggression = 90
        self.banked = s["gems"]
        # Camps: visible ones with health, group states from global camp health.
        self.groups = st.groups
        self.visible_camps = []
        for c in s["camps"]:
            ci = st.camp_at.get((c["x"], c["y"]))
            if ci is not None:
                self.visible_camps.append((ci, st.group_of_camp[ci], Vec(c["x"], c["y"]), float(c["hp"])))
        camp_hp = turn.get("campHp")        # {"x,y": hp} for every camp on the map
        for g in st.groups:
            if camp_hp is not None:
                alive = any(camp_hp.get(f"{t.x},{t.y}", 0) > 0 for t in g.tile_list)   # absent = dead
            else:
                alive = True
            flag = s["groups"][g.index]
            g.state = ALIVE if alive else (REWARD if flag & 1 else RESPAWNING)
            g.respawn_round = self.round + g.respawn if g.state == RESPAWNING else -1
            g.seen_round = self.round
        incoming = 0.0
        for ci, gi, pos, hp in self.visible_camps:
            if hp > 0 and pos.chebyshev_distance(self.pos) <= st.groups[gi].attack_range:
                g = st.groups[gi]
                incoming += g.total_strength / max(g.n_camps, 1)
        self.camp_damage_incoming = incoming
        self.capture_rounds_left = 0
        self.prev_mode = prev_mode

    def set_claims(self, team_modes, me, team_targets=None):
        """The bot's shared-memory claims board, approximated from teammates' inferred modes:
        tribute, recall, flee and death all post STATUS_BUSY; a farming or capturing ally posts
        its group when team_targets (champ -> group index or None) says which."""
        claims = [(memory_codec.STATUS_IDLE, -1)] * memory_codec.MAX_BODIES
        for champ, mode in team_modes.items():
            if champ == me:
                continue
            slot = champ - (self.n_bodies if self.team == 1 else 0)
            if not 0 <= slot < memory_codec.MAX_BODIES:
                continue
            if mode == "dead" or mode in (config.TRIBUTE, config.RECALL, config.FLEE):
                claims[slot] = (memory_codec.STATUS_BUSY, -1)
            elif team_targets is not None and mode in (config.FARM, config.CAPTURE):
                group = team_targets.get(champ)
                if group is not None:
                    claims[slot] = (memory_codec.STATUS_FARM if mode == config.FARM else memory_codec.STATUS_CAPTURE, group)
        self.claims = claims

    # features.build and decide.score_camp read these
    def ensure_feature_state(self, decider):
        self.capture_rounds_left = max(0, config.CAPTURE_TURNS - decider.mode_turns) if decider.mode == config.CAPTURE else 0

    def shards_bought(self):
        return sum(self.items)

    def spawn_centers(self):
        st = self.static
        if getattr(st, "_spawn_centers", None) is None:
            centers = []
            for tiles in st.spawn_tiles:
                tiles = list(tiles or [])
                n = max(len(tiles), 1)
                centers.append(Vec(sum(x for x, y in tiles) // n, sum(y for x, y in tiles) // n))
            st._spawn_centers = centers
        ours, theirs = st._spawn_centers[self.team], st._spawn_centers[1 - self.team]
        return ours, theirs

    def turns_to_enemy_spawn(self):
        return self._enemy_spawn

    def power_balance(self):
        return 99.0 if self.enemy_power <= 0 else self.ally_power / self.enemy_power

    def team_gems_banked(self):
        return self.banked[self.team]

    def enemy_gems_banked(self):
        return self.banked[1 - self.team]


ALIVE, REWARD, RESPAWNING = 1, 2, 3


class OfflineTurn:
    """Candidate lists the way candidates.py builds them, from the precomputed fields."""
    def __init__(self, w):
        self.w = w
        self._camps = None
        self._altar = None
        self.fresh_camps = True

    def altar(self):
        if self._altar is None:
            w = self.w
            turns = w.turns_to_altar
            if turns == UNREACHABLE or not w.static.altars:
                self._altar = False
            else:
                altar = min(w.static.altars, key=lambda a: a.chebyshev_distance(w.pos))
                self._altar = (altar, turns)
        return self._altar or None

    def captures(self):
        w, st = self.w, self.w.static
        out = []
        for g in st.groups:
            if g.state == REWARD:
                on = any((t.x, t.y) == (w.pos.x, w.pos.y) for t in g.tile_list)
                travel = 0 if on else st.field(st.group_fields[g.index], w.pos) + 1
                if travel <= 8:
                    out.append((g, travel))
        out.sort(key=lambda item: item[1])
        return out

    def fight_targets(self):
        return [e for e in self.w.enemies if e.dist <= config.FIGHT_RANGE]

    def camps(self, fresh=False):
        if self._camps is None:
            w, st = self.w, self.w.static
            reach = w.attack_range
            latest_ok = w.round + config.MAX_CAMP_TURNS + config.RESPAWN_WAIT_TURNS
            nearest = []
            for g in st.groups:
                if g.state == RESPAWNING and g.respawn_round > latest_ok:
                    continue
                travel = st.field(st.group_fields[g.index], w.pos)
                if travel != UNREACHABLE and travel <= config.MAX_CAMP_TURNS + reach:
                    nearest.append((travel, g.index))
            nearest.sort()
            claimed_groups = {}
            for slot, (status, group_index) in enumerate(w.claims):
                if slot != w.slot and group_index >= 0 and status in (memory_codec.STATUS_FARM, memory_codec.STATUS_CAPTURE):
                    claimed_groups[group_index] = claimed_groups.get(group_index, 0) + 1
            out = []
            for travel, index in nearest[:config.MAX_CAMP_CANDIDATES + 4]:
                g = st.groups[index]
                if g.state == REWARD:
                    continue
                travel = max(0, travel - (reach - 1))
                wait = g.respawn_round - w.round - travel if g.state == RESPAWNING else 0
                if wait > config.RESPAWN_WAIT_TURNS:
                    continue
                wait = max(0, wait)
                claimed = claimed_groups.get(g.index, 0)
                clear_turns = int(g.total_health / (w.dps * (1 + claimed))) + 1
                out.append(Cand(g, travel, clear_turns, g.total_strength * clear_turns / (1 + claimed), wait, claimed))
                if len(out) >= config.MAX_CAMP_CANDIDATES:
                    break
            self._camps = out
        return self._camps


# ---- labels --------------------------------------------------------------------------------------

def infer_mode(w, turn, st, prev_mode):
    """Mode from what the champion did this turn."""
    kinds = [a["k"] for a in turn["actions"]]
    if K_SUBMIT in kinds:
        return config.TRIBUTE
    if K_RECALL in kinds:
        return config.RECALL
    enemy_tiles = {(e.pos.x, e.pos.y) for e in w.enemies}
    hit_champion = hit_camp = False
    for a in turn["actions"]:
        if a["k"] in (K_ATTACK, K_CAST) and "to" in a:
            t = tuple(a["to"])
            if t in enemy_tiles:
                hit_champion = True
            elif t in st.camp_at:
                hit_camp = True
            elif any(abs(t[0] - ex) <= 1 and abs(t[1] - ey) <= 1 for ex, ey in enemy_tiles):
                hit_champion = True
    if hit_champion:
        return config.FIGHT
    if hit_camp:
        return config.FARM
    moves = [a for a in turn["actions"] if a["k"] == K_MOVE and "to" in a]
    end = Vec(*moves[-1]["to"]) if moves else w.pos
    on_group = st.camp_at.get((end.x, end.y))
    if on_group is not None and st.groups[st.group_of_camp[on_group]].state == REWARD:
        return config.CAPTURE
    # Walking towards a cleared group's reward counts as capturing too (the bot's capture mode
    # includes the approach).
    for g in st.groups:
        if g.state == REWARD and st.group_fields[g.index] is not None:
            before, after = st.field(st.group_fields[g.index], w.pos), st.field(st.group_fields[g.index], end)
            if moves and after < before and before <= 8:
                return config.CAPTURE
    if not moves:
        if w.at_spawn and w.hp_ratio < config.SPAWN_LEAVE_HEALTH:
            return config.RECALL
        return prev_mode if prev_mode in (config.FARM, config.CAPTURE) else config.FARM
    # Moved without acting: read the direction.
    if w.enemies and w.nearest_enemy.dist <= config.FLEE_ENEMY_RANGE:
        before = w.nearest_enemy.dist
        after = min(end.chebyshev_distance(e.pos) for e in w.enemies)
        if after > before:
            return config.FLEE
    if w.gems > 0:
        before, after = st.field(st.altar_field, w.pos), st.field(st.altar_field, end)
        if after < before and (w.gems >= config.TRIBUTE_BAG_NEAR or after <= config.TRIBUTE_NEAR_TURNS):
            return config.TRIBUTE
    f = st.spawn_fields[w.team]
    if w.hp_ratio < config.RECALL_HEALTH and st.field(f, end) < st.field(f, w.pos):
        return config.RECALL
    return config.FARM


def process(path, check=None):
    rows = [json.loads(l) for l in gzip.open(path, "rt", encoding="utf-8")]
    static_rec = rows[0]
    end = rows[-1] if rows[-1].get("kind") == "end" else {}
    turns = [r for r in rows if r.get("kind") == "turn"]
    if not turns:
        return None
    old_rules = static_rec["groups"][0]["gems"] > 10 if static_rec["groups"] else False
    st = Static(static_rec, old_rules)
    st.finish(end.get("spawnB") or [])
    n_per_team = static_rec["champsPerTeam"]
    sizes = (n_per_team, n_per_team)
    trackers = {}
    team_modes = [{}, {}]      # champ index -> last inferred mode (or 'dead'), for the claims-board inputs
    X, mask, y, meta = [], [], [], []
    seq = {}            # (team, champ) -> list of (round, row index, gems, alive) for outcome labels
    for t in turns:
        key = (t["team"], t["champ"])
        tr = trackers.setdefault(key, Tracker())
        s = t["state"]
        if not s["self"] or s["self"].get("x") is None or not s["self"].get("alive", True):
            tr.prev_hp = None
            team_modes[t["team"]][t["champ"]] = "dead"
            continue
        kinds = [a["k"] for a in t["actions"]]
        # Mid-channel turns have no inputs beyond the tick; the bot logs no decision there either.
        if kinds == [K_TICK] and tr.decider.mode in (config.TRIBUTE, config.RECALL) and tr.decider.mode_turns < 6:
            tr.decider.mode_turns += 1
            tr.prev_hp = float(s["self"]["hp"])
            continue
        st.group_fields = st.group_fields_by_team[t["team"]]
        w = OfflineWorld(st, t, tr, sizes, tr.decider.mode)
        w.set_claims(team_modes[t["team"]], t["champ"])
        turn = OfflineTurn(w)
        d = tr.decider
        x = features.build(w, turn, d)
        label = infer_mode(w, t, st, d.mode)
        if d.target_group is None and turn.camps():
            d.target_group = max(turn.camps(), key=lambda c: c.score).group
        m = d.masks(w, turn) if hasattr(d, "masks") else [True] * 6
        X.append(np.array(x, dtype=np.uint8)); mask.append(m); y.append(label)
        meta.append((t["round"], w.slot, w.kind, w.gems, w.team_gems_banked(), int(w.hp_ratio * 100), t["team"]))
        seq.setdefault(key, []).append((t["round"], len(X) - 1, w.gems, True))
        d._set_mode(label, "replay") if hasattr(d, "_set_mode") else None
        team_modes[t["team"]][t["champ"]] = label
        tr.prev_hp, tr.prev_gems = w.hp, w.gems
    # Outcome: tributes (bag drops to 0 after a submit) and collections (bag rises) in the window,
    # minus a penalty when the body dies (a turn with alive False or a respawn timer) in the window.
    reward = np.zeros(len(X), dtype=np.float32)
    for key, items in seq.items():
        rounds = [r for r, _, _, _ in items]
        gems = [g for _, _, g, _ in items]
        deaths = set()
        for t in turns:
            if (t["team"], t["champ"]) == key and t["state"]["self"] and (not t["state"]["self"].get("alive", True) or (t["state"]["self"].get("resp") or 0) > 0):
                deaths.add(t["round"])
        for j, (r, idx, g, _) in enumerate(items):
            tributed = collected = 0
            k = j + 1
            while k < len(items) and rounds[k] <= r + HORIZON:
                dg = gems[k] - gems[k - 1]
                if dg > 0:
                    collected += dg
                elif dg < 0 and any(a["k"] == K_SUBMIT for a in []):
                    pass
                if dg < 0 and gems[k] == 0:
                    tributed += gems[k - 1]          # a bag that went to zero: tributed (or lost)
                k += 1
            died = any(r < dr <= r + HORIZON for dr in deaths)
            reward[idx] = (0 if died else tributed) + 0.5 * collected - (DEATH_PENALTY if died else 0)
    out = dict(X=np.stack(X), mask=np.array(mask, dtype=np.bool_), y=np.array(y, dtype=np.int8), reward=reward,
               meta=np.array(meta, dtype=np.int32))
    if check:
        compare(out, check)
    return out


LOG_RE = re.compile(r"^nnlog r=(\d+) s=(\d+) k=(\d+) m=(\d+) src=\w+ mask=([01]{6}) sc=[-\d,]+ gems=(\d+) bank=(\d+) hp=(\d+) f=([0-9a-f]+)$")


def compare(out, log_path):
    """Feature-by-feature agreement with the bot's own datalog for team A of the same game."""
    logged = {}
    for line in open(log_path, encoding="utf-8"):
        m = LOG_RE.match(line.strip())
        if m:
            logged[(int(m.group(1)), int(m.group(2)))] = (int(m.group(4)), np.frombuffer(bytes.fromhex(m.group(9)), dtype=np.uint8))
    X, y, meta = out["X"], out["y"], out["meta"]
    pairs = [(i, logged[(int(r), int(s))]) for i, (r, s, k, g, b, hp, team) in enumerate(meta) if team == 0 and (int(r), int(s)) in logged]
    if not pairs:
        print("check: no matching turns"); return
    diff = np.zeros(X.shape[1]); n = len(pairs); agree = 0
    for i, (mode, xf) in pairs:
        if len(xf) == X.shape[1]:
            diff += np.abs(X[i].astype(int) - xf.astype(int))
        agree += int(mode == y[i])
    diff /= n
    conf = np.zeros((config.N_MODES, config.N_MODES), dtype=int)
    for i, (mode, xf) in pairs:
        conf[mode][y[i]] += 1
    print("mode confusion (rows = bot's logged mode, cols = inferred):")
    for mi in range(config.N_MODES):
        print(f"  {config.MODE_NAMES[mi]:8s}", " ".join(f"{v:5d}" for v in conf[mi]))
    print(f"check: {n} matched turns of {len(logged)} logged; mode label agreement {100 * agree / n:.1f}%")
    worst = np.argsort(-diff)[:12]
    # A few concrete rows for the worst inputs, to see whether the error is systematic.
    for fi in [int(i) for i in worst[:10]]:
        shown = 0
        for i, (mode, xf) in pairs:
            if len(xf) == X.shape[1] and abs(int(X[i][fi]) - int(xf[fi])) > 8 and shown < 4:
                r, sl = int(meta[i][0]), int(meta[i][1])
                print(f"  f{fi}: round {r} slot {sl} offline={int(X[i][fi])} logged={int(xf[fi])} (mode off/log {int(y[i])}/{mode})")
                shown += 1
    print("mean abs feature error, worst 12:", [(int(i), round(float(diff[i]), 1)) for i in worst])
    print("features within 2 of the log:", f"{100 * (diff <= 2).mean():.0f}%")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dumps", nargs="+")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "data", "replay_modes.npz"))
    ap.add_argument("--check", help="nnlog text of the same game for a feature comparison")
    ap.add_argument("--index", help="fetch_replays.py index.json: adds each side's ladder rating and result")
    ap.add_argument("--jobs", type=int, default=12, help="dumps to process in parallel")
    ap.add_argument("--winners-only", action="store_true", help="keep only the rows of sides that won (needs --index)")
    args = ap.parse_args()
    index = json.load(open(args.index, encoding="utf-8")) if args.index else {}
    dumps = []
    for d in args.dumps:
        dumps += [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".jsonl.gz")] if os.path.isdir(d) else [d]
    parts, side_info = [], []
    bar = Progress(len(dumps), "features")
    if args.jobs > 1 and len(dumps) > 1 and not args.check:
        from multiprocessing import Pool
        results = []
        with Pool(args.jobs) as pool:
            for out in pool.imap(process, dumps):
                results.append(out)
                bar.step()
    else:
        results = []
        for p in dumps:
            results.append(process(p, args.check))
            bar.step()
    bar.done()
    for p, out in zip(dumps, results):
        if out is not None:
            parts.append(out)
            match_id = os.path.basename(p).split(".")[0]
            entry = index.get(match_id, {})
            by_side = {t.get("side"): t for t in entry.get("teams", [])}
            info = []
            for side in ("a", "b"):
                t = by_side.get(side, {})
                info.append((float(t.get("rating") or 0.0), 1.0 if t.get("result") == "win" else 0.0))
            side_info.append(info)
            if len(dumps) <= 12:
                print(f"{os.path.basename(p)}: {len(out['X'])} rows" + (f" ({' vs '.join(t.get('name', '?') for t in entry.get('teams', []))})" if entry else ""))
    if parts and not args.check:
        X = np.concatenate([p["X"] for p in parts]); mask = np.concatenate([p["mask"] for p in parts])
        y = np.concatenate([p["y"] for p in parts]); reward = np.concatenate([p["reward"] for p in parts])
        meta = np.concatenate([p["meta"] for p in parts])
        match_id = np.concatenate([np.full(len(p["X"]), i, dtype=np.int32) for i, p in enumerate(parts)])
        rating = np.concatenate([np.array([side_info[i][int(team)][0] for team in p["meta"][:, 6]], dtype=np.float32) for i, p in enumerate(parts)])
        won = np.concatenate([np.array([side_info[i][int(team)][1] for team in p["meta"][:, 6]], dtype=np.float32) for i, p in enumerate(parts)])
        if args.winners_only:
            keep = won > 0
            print(f"keeping the {int(keep.sum())} rows of winning sides out of {len(keep)}")
            X, mask, y, reward, meta, match_id, rating, won = (a[keep] for a in (X, mask, y, reward, meta, match_id, rating, won))
        np.savez_compressed(args.out, X=X, mask=mask, y=y, reward=reward, meta=meta, match_id=match_id, rating=rating, won=won,
                            scores=np.zeros((len(X), config.N_MODES), dtype=np.float32))
        if args.index:
            print("rows from winning sides: %d%%, mean rating %.0f" % (100 * won.mean(), rating[rating > 0].mean() if (rating > 0).any() else 0))
        print(f"{len(X)} rows -> {args.out}; modes:", {config.MODE_NAMES[i]: int(c) for i, c in enumerate(np.bincount(y, minlength=config.N_MODES))})


if __name__ == "__main__":
    main()
