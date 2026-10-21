"""Integer features (0-255) for the escape head: where a fleeing body should step. One state
vector per turn and one candidate vector per tile (the eight neighbours plus standing still),
built from the world model only, so the same code runs in the bot and offline
(tools/replay_escape_dataset.py). The head is bilinear (nn.Bilinear): its score is a survival
logit, and the bot steps to the candidate with the highest score.
"""
import config
import world as W
from features import clip, turns

N_STATE = 12
N_CAND = 12
UNREACHABLE = W.UNREACHABLE
NEIGHBOURS = ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1))


def state(w, decider):
    x = [0] * N_STATE
    x[0] = clip(w.hp_ratio * 255)
    x[1] = clip(w.max_hp / 20)
    x[2] = clip(w.strength / 2)
    x[3] = clip(32 * len(w.enemies))
    e = w.nearest_enemy
    x[4] = clip(16 * e.dist) if e is not None else 255
    x[5] = clip(255 * e.hp / max(e.max_hp, 1)) if e is not None else 0
    x[6] = clip(e.strength / 2) if e is not None else 0
    x[7] = 255 if w.has_dash and w.can_cast and w.cooldowns[_dash_slot(w)] == 0 else 0
    x[8] = 255 if w.is_ranged else 0
    x[9] = clip(w.gems)
    x[10] = turns(w.turns_to_spawn)
    x[11] = clip(255 * w.damage_taken / max(w.max_hp, 1))
    return x


def _dash_slot(w):
    for rec in w.abilities:
        if getattr(rec, "is_dash", False):
            return rec.slot
    return 0


def candidates(w):
    """(tile, is_stay) for standing still and each walkable neighbour (terrain only: a tile an
    ally stands on is filtered by the navigator at the step)."""
    out = [(w.pos, True)]
    blocked = w.static_blocked
    width, height = w.width, w.height
    for dx, dy in NEIGHBOURS:
        x, y = w.pos.x + dx, w.pos.y + dy
        if 0 <= x < width and 0 <= y < height:
            tile = w.make_tile(x, y)
            if not blocked.is_set(tile):
                out.append((tile, False))
    return out


def build(w, decider, cands):
    """State vector and one candidate vector per (tile, is_stay)."""
    s = state(w, decider)
    threats = [e.pos for e in w.enemies]
    here_d = min(w.pos.chebyshev_distance(t) for t in threats) if threats else 16
    spawn_here = w.spawn_field.get(w.pos)
    altar_field = w.altar_field_or_none()
    altar_here = altar_field.get(w.pos) if altar_field is not None else UNREACHABLE
    allies = [pos for pos, hp, strength, d in w.allies]
    rows = []
    for tile, is_stay in cands:
        x = [0] * N_CAND
        d = min(tile.chebyshev_distance(t) for t in threats) if threats else 16
        x[0] = clip(16 * d)
        x[1] = clip(64 * sum(1 for t in threats if tile.chebyshev_distance(t) <= 2))
        x[2] = clip(64 * sum(1 for t in threats if tile.chebyshev_distance(t) <= 4))
        x[3] = 255 if w.bushes.is_set(tile) else 0
        sf = w.spawn_field.get(tile)
        x[4] = clip(128 + 32 * (spawn_here - sf)) if sf < UNREACHABLE and spawn_here < UNREACHABLE else 0
        if altar_field is not None:
            af = altar_field.get(tile)
            x[5] = clip(128 + 32 * (altar_here - af)) if af < UNREACHABLE and altar_here < UNREACHABLE else 0
        else:
            x[5] = 128
        x[6] = clip(32 * w.walkable_neighbours(tile))
        x[7] = clip(16 * min(tile.chebyshev_distance(a) for a in allies)) if allies else 255
        x[8] = 255 if is_stay else 0
        x[9] = clip(128 + 32 * (d - here_d))
        x[10] = 255 if w.spawn_ally.is_set(tile) else 0
        x[11] = clip(16 * sf) if sf < UNREACHABLE else 255
        rows.append(x)
    return s, rows
