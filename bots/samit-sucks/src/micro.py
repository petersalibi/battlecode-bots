"""The threat layer (redesign phase 3): one influence evaluation per step, always on.

A step is scored over the tiles we can reach (standing still and the free neighbours) as

    progress along the objective field
    - damage that could land on the tile next turn, as a share of our effective health
    + allies within reach of the tile, while there is danger about

Damage comes from visible enemies (their reach plus one step), awake camps for which we would be
the nearest champion in reach (memory: camp-targeting-rules; a tie counts half), and the shared
enemy sighting when no enemy is in sight, its reach growing and its weight fading with age. No
whole-board map: only the handful of tiles about to be chosen between are scored.
"""
from cambc import vision

import config


class Micro:
    def __init__(self, world, nav):
        self.w = world
        self.nav = nav
        self.ignore_group = -1     # the camp group we mean to fight this turn: its camps are the objective
        self.weight = config.MICRO_THREAT_WEIGHT
        self._round = -1
        self._built = False

    def turn_reset(self, ignore_group=-1, weight=None):
        self.ignore_group = ignore_group
        self.weight = config.MICRO_THREAT_WEIGHT if weight is None else weight
        self._built = False

    # ---- what is around us ---------------------------------------------------------
    def _build(self):
        """Threat sources and allies as plain ints, once per turn and only when a step asks. Each
        source carries what the tile loop needs, so scoring a tile is a few compares per source
        (the wasm interpreter makes every attribute, call and config lookup count)."""
        w = self.w
        self._built = True
        soft_range, soft_share = config.MICRO_SOFT_RANGE, config.MICRO_SOFT_SHARE
        # Enemies and the sighting: (x, y, reach next turn, soft reach, damage, soft damage).
        sources = []
        for e in w.enemies:
            reach = e.reach + 1
            sources.append((e.pos.x, e.pos.y, reach, reach + soft_range, e.dmg, e.dmg * soft_share))
        if not w.enemies and w.board is not None and w.board.sighting is not None:
            (x, y), count, seen = w.board.sighting
            age = w.round - seen
            if age <= config.MICRO_SIGHTING_FRESH and max(abs(x - w.pos.x), abs(y - w.pos.y)) <= config.MICRO_SIGHTING_RANGE:
                dmg = w.my_dmg() * count * (1.0 - age / (config.MICRO_SIGHTING_FRESH + 1))
                sources.append((x, y, 2 + age, 2 + age + soft_range, dmg, dmg * soft_share))
        # Allies: exact where we see them, otherwise the cells the board holds (fresh ones only).
        allies = [(pos.x, pos.y) for pos, hp, strength, d in w.allies]
        if not allies and w.board is not None:
            b = w.board
            for k in range(min(w.n_bodies, len(b.cells))):
                if (k != w.slot and b.cells[k] is not None and b.age(k, w.round) <= config.MICRO_ALLY_AGE
                        and b.claims[k] != (3, -1)):          # BUSY without a group: dead or channelling
                    allies.append(b.cells[k])
        self.allies = allies
        # Awake camps outside the group we are fighting. A camp hits the nearest champion in its
        # reach: who else is nearer does not depend on our tile, so it is found here once.
        # Each camp: (x, y, reach, the nearest other champion's distance, damage).
        camps = []
        awake = vision.awake_camp_tiles()
        if not awake.is_empty():
            others = allies + [(e.pos.x, e.pos.y) for e in w.enemies]
            for camp, gi, pos, hp in w.visible_camps:
                if hp > 0 and gi != self.ignore_group and awake.is_set(pos):
                    g = w.groups[gi]
                    cx, cy, reach = pos.x, pos.y, g.attack_range
                    nearest = reach + 1
                    for ox, oy in others:
                        d = max(abs(cx - ox), abs(cy - oy))
                        if d < nearest:
                            nearest = d
                    camps.append((cx, cy, reach, nearest, g.total_strength / max(g.n_camps, 1)))
        self.sources = sources
        self.camps = camps
        self.cost = self.weight / max(1.0, w.my_ehp())      # score per point of damage

    def danger(self):
        """Anything that could hurt us near enough to change a step."""
        if not self._built:
            self._build()
        return bool(self.sources or self.camps)

    # ---- choosing a step ------------------------------------------------------------
    def step(self, field, unreachable):
        """The best of standing still and the free neighbours, scored against `field` (walking
        turns to the objective). Returns PASS when no threat or ally tells the tiles apart (the
        caller paths as usual), None to stand still, or the tile to step to.

        A source whose edge does not pass through our 3x3 neighbourhood adds the same to every
        tile, so only sources with an edge here are scored (a tile costs ~13K fuel to score)."""
        w = self.w
        if not self._built:
            self._build()
        pos = w.pos
        px, py = pos.x, pos.y
        sources = []
        for src in self.sources:
            d = max(abs(src[0] - px), abs(src[1] - py))
            if src[2] - 1 <= d <= src[3] + 1:             # the reach or the soft edge is within a step
                sources.append(src)
        camps = []
        for camp in self.camps:
            d = max(abs(camp[0] - px), abs(camp[1] - py))
            reach, nearest = camp[2], camp[3]
            if d - 1 > reach or (d + 1 <= reach and d + 1 < nearest):
                continue                                  # out of reach from every tile, or on us from every tile
            camps.append(camp)
        if not sources and not camps:
            return PASS
        near = config.MICRO_ALLY_NEAR
        allies = []
        for ax, ay in self.allies:
            if near - 1 <= max(abs(ax - px), abs(ay - py)) <= near + 1:
                allies.append((ax, ay))
        cap, ally_value = config.MICRO_ALLY_CAP, config.MICRO_ALLY_WEIGHT
        base_allies = 0                                   # allies near every tile: count towards the cap
        for ax, ay in self.allies:
            if max(abs(ax - px), abs(ay - py)) < near - 1:
                base_allies += 1
        cost, progress_value = self.cost, config.MICRO_PROGRESS_WEIGHT
        here = field.get(pos)
        last = w.last_pos
        lx, ly = (last.x, last.y) if last is not None else (-1, -1)
        backtrack = config.MICRO_BACKTRACK
        best, best_score = None, None
        for tile in self.nav.blocked().reachable_tiles(pos, 1).positions():
            x, y = tile.x, tile.y
            if x == px and y == py:
                value = here
            else:
                value = field.get(tile)
                if value == unreachable:
                    continue
            damage = 0.0
            for tx, ty, reach, soft, dmg, soft_dmg in sources:
                d = max(abs(tx - x), abs(ty - y))
                if d <= reach:
                    damage += dmg
                elif d <= soft:
                    damage += soft_dmg
            for cx, cy, reach, nearest, dmg in camps:
                d = max(abs(cx - x), abs(cy - y))
                if d <= reach:
                    if d < nearest:
                        damage += dmg
                    elif d == nearest:
                        damage += 0.5 * dmg
            s = progress_value * (here - value) - cost * damage
            if allies:
                n = base_allies
                for ax, ay in allies:
                    if max(abs(ax - x), abs(ay - y)) <= near:
                        n += 1
                s += ally_value * (n if n < cap else cap)
            if x == lx and y == ly:
                s -= backtrack
            if best_score is None or s > best_score:
                best, best_score = tile, s
        if best is None or (best.x == px and best.y == py):
            return None
        return best


PASS = object()      # Micro.step: nothing here tells the tiles apart
