"""Distance fields to job targets, cached for the match; the per-turn blocked board; moves.

At most one new field is built a turn (a whole-map field is ~80 fuel a tile). A job whose field
is not built yet waits a turn: which turn depends only on the game, never on fuel left."""
import cambc
from cambc import vision

Bitboard = cambc.Bitboard
DistanceField = cambc.DistanceField
UNREACHABLE = 60000


class Paths:
    def __init__(self, w):
        self.w = w
        self.fields = {}
        self.built = False
        self._blocked = None

    def turn_reset(self):
        self.built = False
        self._blocked = None

    def blocked(self):
        """Terrain plus every visible unit, minus our own tile: where we cannot stand this turn."""
        if self._blocked is None:
            b = (self.w.terrain | vision.champion_tiles() | vision.camp_tiles() | vision.teleport_tiles())
            b.set(self.w.pos, False)
            self._blocked = b
        return self._blocked

    def _field(self, key, roots, walls):
        f = self.fields.get(key)
        if f is not None:
            return f
        if self.built or roots.is_empty():
            return None
        self.built = True
        f = DistanceField()
        f.compute(walls, roots, Bitboard())
        self.fields[key] = f
        return f

    def ring(self, tiles, radius=1):
        out = Bitboard()
        tmp = Bitboard()
        for t in tiles:
            tmp.clear()
            tmp.draw_chebyshev(t, radius)
            out |= tmp
        return out & ~self.w.blocked

    def to_group(self, g):
        """Walking turns to stand within our attack range of group g (its camps are walls while
        alive): beside it for melee, three tiles off for ranged bodies."""
        return self._field(("g", g.index), self.ring(g.tile_list, self.w.attack_range), self.w.blocked)

    def onto_group(self, g):
        """Walking turns to stand on one of group g's tiles (to capture it)."""
        return self._field(("c", g.index), g.tiles, self.w.blocked & ~g.tiles)

    def to_altar(self, altar):
        return self._field(("a", altar.x, altar.y), self.ring([altar], self.w.submit_range), self.w.blocked)

    def get(self, field, tile):
        v = field.get(tile)
        return UNREACHABLE if v >= UNREACHABLE else v

    def move(self, tile):
        w = self.w
        try:
            if not w.me.move_absolute(tile):
                return False
        except Exception:
            return False
        if self._blocked is not None:
            self._blocked.set(w.pos, False)
            self._blocked.set(tile, True)
        w.last_pos, w.pos = w.pos, tile
        w.x, w.y = tile.x, tile.y
        w.moved = True
        w.at_spawn = w.spawn_ally.is_set(tile)
        if w.ranged:
            w.can_attack = False          # ranged bodies walk or attack
        return True
