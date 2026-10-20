"""Pathing on the SDK's native distance fields and bitboards.

Fields are rooted on goal tiles and computed over static terrain once per goal, then cached for
the rest of the match. Each turn only the next step is checked against visible units, so a cached
field costs one next_moves_from call plus a few is_set checks.
"""
import cambc
from cambc import vision

import world as W

Bitboard = cambc.Bitboard
DistanceField = cambc.DistanceField


class Navigator:
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
            board = (self.w.static_blocked | vision.champion_tiles() | vision.camp_tiles()
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

    def from_me(self, max_distance=None):
        """Field of walking turns from our current tile, recomputed at most once per turn (~64K fuel)."""
        if self._from_me is None or self._from_me_round != self.w.round:
            roots = Bitboard()
            roots.set(self.w.pos)
            field = DistanceField()
            if max_distance is None:
                field.compute(self.w.static_blocked, roots, Bitboard())
            else:
                field.compute(self.w.static_blocked, roots, Bitboard(), max_distance)
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
        if here == 0 or here == W.UNREACHABLE:
            return False
        blocked = self.blocked()
        # Optimal steps first: one engine call plus a membership test each.
        for delta in field.next_moves_from(w.pos):
            dest = w.pos + delta
            if not blocked.is_set(dest):
                if self._move(dest):
                    return True
        # Every optimal step is occupied: take any reachable tile that still improves.
        best, best_value = None, here
        for tile in blocked.reachable_tiles(w.pos, 1).positions():
            value = field.get(tile)
            if value < best_value:
                best, best_value = tile, value
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

    def try_dash(self, field):
        """Use a dash ability to skip ahead along the field. Returns True when it fired."""
        w = self.w
        if not w.can_cast or w.cast_exclusive:
            return False
        for rec in w.abilities:
            if not rec.is_dash:
                continue
            try:
                if rec.ability.cooldown() > 0:
                    return False
                here = field.get(w.pos)
                blocked = self.blocked()
                best, best_value = None, here - 1       # a dash must beat a plain step
                for tile in blocked.reachable_tiles(w.pos, rec.range or 2).positions():
                    value = field.get(tile)
                    if value < best_value:
                        best, best_value = tile, value
                if best is not None and rec.ability.use(best):
                    w.can_cast = False
                    self.occupy(w.pos)
                    w.pos = best
                    blocked.set(best, True)
                    return True
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
