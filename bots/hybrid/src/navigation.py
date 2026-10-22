"""Pathing on the SDK's native distance fields and bitboards.

Fields are rooted on goal tiles and computed over static terrain once per goal, then cached for
the rest of the match. Each turn only the next step is checked against visible units, so a cached
field costs one next_moves_from call plus a few is_set checks.
"""
import cambc
from cambc import vision

import config
import world as W

Bitboard = cambc.Bitboard
DistanceField = cambc.DistanceField


class Navigator:
    hole_failures = 0

    def __init__(self, world):
        self.w = world
        self.fields = {}
        self._blocked = None
        self._from_me = None
        self._from_me_round = -1

    def turn_reset(self):
        self._blocked = None
        self._from_me = None

    # ---- boards --------------------------------------------------------------------
    def blocked(self):
        """Tiles we cannot step on this turn: terrain, enemy spawn and every visible unit, minus our own tile."""
        if self._blocked is None:
            # Terrain plus visible units: a cleared camp's tiles are walkable (that is how captures
            # happen) even though every cached field treats camp tiles as walls.
            board = (self.w.terrain_blocked | vision.champion_tiles() | vision.camp_tiles()
                     | vision.teleport_tiles())
            board.set(self.w.pos, False)
            self._blocked = board
        return self._blocked

    def occupy(self, tile):
        """Mark a tile we just left or entered so later steps this turn see it."""
        if self._blocked is not None:
            self._blocked.set(tile, False)

    # ---- fields --------------------------------------------------------------------
    def field_to(self, key, goals):
        """Cached field with distance 0 on goals, computed over static terrain."""
        field = self.fields.get(key)
        if field is None:
            field = DistanceField()
            field.compute(self.w.static_blocked & ~goals, goals, Bitboard())
            self.fields[key] = field
        return field

    def local_field(self, goals, max_distance):
        """Uncached field rooted on goals, explored only max_distance tiles out: for moving targets
        such as enemies, where a whole-map field per position would cost ~750K on a 100x100 map."""
        field = DistanceField()
        field.compute(self.w.static_blocked & ~goals, goals, Bitboard(), max_distance)
        return field

    def live_field(self, goals, max_distance):
        """Like local_field, but over terrain plus the camps standing now instead of every camp
        tile: fights happen beside cleared camps and on capture regions, where the static walls
        left a body one blocked step from its target, or inside a wall with no way out (probe:
        80 stalled fight steps in four 500-round games)."""
        walls = self.w.terrain_blocked | vision.camp_tiles()
        walls.set(self.w.pos, False)
        field = DistanceField()
        field.compute(walls & ~goals, goals, Bitboard(), max_distance)
        return field

    def from_me(self, max_distance=None):
        """Field of walking turns from our current tile, recomputed at most once per turn (~64K fuel)."""
        if self._from_me is None or self._from_me_round != self.w.round:
            roots = Bitboard()
            roots.set(self.w.pos)
            walls = self.w.static_blocked
            if walls.is_set(self.w.pos):
                walls = walls & ~roots              # standing on a captured camp's tile
            field = DistanceField()
            if max_distance is None:
                field.compute(walls, roots, Bitboard())
            else:
                field.compute(walls, roots, Bitboard(), max_distance)
            self._from_me = field
            self._from_me_round = self.w.round
        return self._from_me

    def ring(self, key, centres, radius):
        """Cached bitboard of walkable tiles within radius of any centre tile."""
        board = self.fields.get(("ring", key))
        if board is None:
            board = Bitboard()
            tmp = Bitboard()
            for centre in centres:
                tmp.clear()
                tmp.draw_chebyshev(centre, radius)
                board |= tmp
            board &= ~self.w.static_blocked
            self.fields[("ring", key)] = board
        return board

    # ---- stepping ------------------------------------------------------------------
    def step_along(self, field):
        """Take one step that lowers the field value. Returns True if we moved."""
        w = self.w
        if not w.can_move or w.cast_exclusive:
            return False
        here = field.get(w.pos)
        if here == 0:
            return False
        blocked = self.blocked()
        if here == W.UNREACHABLE:
            # Standing on a tile the field treats as a wall (a captured camp's spawn tile): step
            # off it onto the neighbour nearest the goal.
            best, best_value = None, W.UNREACHABLE
            for tile in blocked.reachable_tiles(w.pos, 1).positions():
                value = field.get(tile)
                if value < best_value:
                    best, best_value = tile, value
            return best is not None and self._move(best)
        # Optimal steps first: one engine call plus a membership test each.
        through_hole = False
        for delta in field.next_moves_from(w.pos):
            dest = w.pos + delta
            if not blocked.is_set(dest):
                if self._move(dest):
                    return True
            elif w.has_dash and w.thin_holes.is_set(dest):
                through_hole = True
        if through_hole:
            if self.try_dash(field, min_gain=1):
                # Dashing and walking are compatible: keep going from the landing tile.
                here = field.get(w.pos)
                if here == 0:
                    return True
                for delta in field.next_moves_from(w.pos):
                    dest = w.pos + delta
                    if not blocked.is_set(dest) and self._move(dest):
                        return True
                return True
            if self.dash_ready():
                self.hole_failures += 1
                if self.hole_failures >= config.HOLE_FAIL_LIMIT:
                    self.hole_failures = 0
                    self.seal_holes(field)
                    return False
            else:
                w.waiting_dash = True                 # the stall guard leaves a body waiting on its dash alone
        # Every optimal step is occupied: take any reachable tile that still improves, or failing
        # that a sideways tile of equal distance that has a free step of its own (so a body blocked
        # by a unit walks round it instead of waiting). Never the tile we just left.
        best, best_value = None, here
        sideways = None
        for tile in blocked.reachable_tiles(w.pos, 1).positions():
            value = field.get(tile)
            if value < best_value:
                best, best_value = tile, value
            elif value == here and sideways is None and tile != w.last_pos:
                for delta in field.next_moves_from(tile):
                    if not blocked.is_set(tile + delta):
                        sideways = tile
                        break
        if best is None:
            best = sideways
        return best is not None and self._move(best)

    def goto(self, key, goals):
        return self.step_along(self.field_to(key, goals))

    def step_away(self, threats, home_field=None):
        """Step to the reachable tile furthest from the threats; ties go to bushes, then towards home."""
        w = self.w
        if not w.can_move or w.cast_exclusive or not threats:
            return False
        blocked = self.blocked()
        best, best_key = None, None
        for tile in [w.pos, *blocked.reachable_tiles(w.pos, 1).positions()]:
            nearest = min(tile.chebyshev_distance(t) for t in threats)
            home = -home_field.get(tile) if home_field is not None else 0
            key = (nearest, 1 if w.bushes.is_set(tile) else 0, home)
            if best_key is None or key > best_key:
                best, best_key = tile, key
        return best is not None and best != w.pos and self._move(best)

    def hole_ahead(self, field, steps):
        """True when the field's descent from here reaches a thin hole within `steps` moves (one
        engine call per step)."""
        w = self.w
        pos = w.pos
        for _ in range(steps):
            moves = field.next_moves_from(pos)
            if not moves:
                return False
            pos = pos + moves[0]
            if w.thin_holes.is_set(pos):
                return True
        return False

    def dash_ready(self):
        """A dash ability off cooldown, with a cast still available this turn."""
        w = self.w
        if not w.can_cast or w.cast_exclusive:
            return False
        for rec in w.abilities:
            if rec.is_dash:
                try:
                    return rec.ability.cooldown() == 0
                except Exception:
                    return False
        return False

    def seal_holes(self, field):
        """The thin holes on the route's next steps become walls for this body: the crossing
        failed with the dash ready, so the route must go round. Cached fields are dropped."""
        w = self.w
        sealed = 0
        for delta in field.next_moves_from(w.pos):
            dest = w.pos + delta
            if w.thin_holes.is_set(dest):
                w.thin_holes.set(dest, False)
                w.static_blocked.set(dest, True)
                sealed += 1
        if sealed:
            self.fields.clear()
            w.spawn_field_stale = True
            print("r%d %s: sealed %d hole(s) at %d,%d after failed dashes" % (w.round, w.kind_name, sealed, w.pos.x, w.pos.y), flush=True)

    def try_dash(self, field, min_gain=2):
        """Use a dash ability to skip ahead along the field, over a hole if that is the way.
        Returns True when it fired. The landing must improve the field by min_gain (a plain step
        gains 1, so 2 is the default; the hole crossing asks for 1 because the alternative is
        standing still)."""
        w = self.w
        if not w.can_cast or w.cast_exclusive:
            return False
        if min_gain >= 2 and config.DASH_SAVE_STEPS and w.has_dash and not w.thin_holes.is_empty()                 and self.hole_ahead(field, config.DASH_SAVE_STEPS):
            return False
        for rec in w.abilities:
            if not rec.is_dash:
                continue
            try:
                if rec.ability.cooldown() > 0:
                    return False
                here = field.get(w.pos)
                blocked = self.blocked()
                # valid_tiles is the square within range; it ignores occupancy and terrain, so the
                # landing is filtered here. The engine rejects a landing with no legal 2-step path
                # (a wall in the way), in which case the next best tile is tried.
                if config.DASH_SQUARE_LANDINGS:
                    landings = rec.ability.valid_tiles() & ~blocked
                else:
                    landings = blocked.reachable_tiles(w.pos, rec.range or 2)
                options = []
                for tile in landings.positions():
                    value = field.get(tile)
                    if value <= here - min_gain:
                        options.append((value, tile.x, tile.y, tile))
                options.sort()
                for value, _x, _y, tile in options[:3]:
                    try:
                        if rec.ability.use(tile):
                            w.can_cast = False
                            self.occupy(w.pos)
                            w.pos = tile
                            w.at_spawn = w.spawn_ally.is_set(tile)
                            w.in_bush = w.bushes.is_set(tile)
                            blocked.set(tile, True)
                            return True
                    except Exception:
                        continue
            except Exception:
                return False
        return False

    def dash_away(self, threats, home_field=None):
        """Dash to the landing furthest from the threats (ties: bushes, then towards home), when
        that beats where we stand. A fleeing body that dashes and then steps opens three tiles on
        a melee pursuer in one turn, and crosses holes the pursuer may not be able to follow."""
        w = self.w
        if not threats or not w.can_cast or w.cast_exclusive:
            return False
        for rec in w.abilities:
            if not rec.is_dash:
                continue
            try:
                if rec.ability.cooldown() > 0:
                    return False
                blocked = self.blocked()
                landings = rec.ability.valid_tiles() & ~blocked

                def key(tile):
                    nearest = min(tile.chebyshev_distance(t) for t in threats)
                    home = -home_field.get(tile) if home_field is not None else 0
                    return (nearest, 1 if w.bushes.is_set(tile) else 0, home)

                here = key(w.pos)
                options = [(key(tile), tile.x, tile.y, tile) for tile in landings.positions()]
                options = [o for o in options if o[0] > here]
                options.sort(reverse=True)
                for _k, _x, _y, tile in options[:3]:
                    try:
                        if rec.ability.use(tile):
                            w.can_cast = False
                            self.occupy(w.pos)
                            w.pos = tile
                            w.at_spawn = w.spawn_ally.is_set(tile)
                            w.in_bush = w.bushes.is_set(tile)
                            blocked.set(tile, True)
                            return True
                    except Exception:
                        continue
            except Exception:
                return False
        return False

    def _move(self, dest):
        w = self.w
        try:
            if not w.me.move_absolute(dest):
                return False
        except Exception:
            return False
        # The tile we left and the one we stand on are both free for later checks this turn.
        w.note_moved(dest)
        return True
