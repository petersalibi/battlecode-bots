"""Draft: lock a pick in first (a missed pick is random), then, on our first draft turn (100x fuel),
build the map model (plan.py) and leave it in draft memory. Later turns carry it over."""
import _cambc
import cambc
from cambc import game_map

import config
import plan

Team = cambc.Team
Draft = cambc.ChampionDraft
Bitboard = cambc.Bitboard
DistanceField = cambc.DistanceField
UNREACHABLE = 60000          # field values at or above this are unreached


def _pick():
    chosen = set(int(p) for p in cambc.draft.current_draft(Team.ALLY))
    for name in config.DRAFT_PRIORITY:
        pick = getattr(Draft, name, None)
        if pick is not None and int(pick) not in chosen:
            return pick
    for pick in range(config.N_DRAFT_PICKS):
        if pick not in chosen:
            return pick
    return None


def _ring(tiles, blocked, width, height):
    """Walkable tiles touching any of tiles (a group is entered from beside it)."""
    ring = Bitboard()
    tmp = Bitboard()
    for t in tiles:
        tmp.clear()
        tmp.draw_chebyshev(t, 1)
        ring |= tmp
    return ring & ~blocked


def _min_over(field, tiles):
    best = UNREACHABLE
    for t in tiles:
        v = field.get(t)
        if v < best:
            best = v
    return best


def build_plan():
    width, height = game_map.width(), game_map.height()
    groups = game_map.camp_groups()
    camp_tiles = Bitboard()
    tile_lists = []
    for g in groups:
        tiles = g.spawn_tiles()
        camp_tiles |= tiles
        tile_lists.append(tiles.positions())
    altar_board = game_map.altars()
    ours, theirs = game_map.spawn_region(Team.ALLY), game_map.spawn_region(Team.ENEMY)
    blocked = game_map.walls() | game_map.holes() | altar_board | camp_tiles
    rings = [_ring(tl, blocked, width, height).positions() for tl in tile_lists]
    n = len(groups)

    def field_from(roots, walls):
        f = DistanceField()
        f.compute(walls, roots, Bitboard())
        return f

    f_us = field_from(ours, blocked | theirs)
    f_them = field_from(theirs, blocked | ours)
    spawn = [plan.cap(_min_over(f_us, r), UNREACHABLE) for r in rings]
    side = [0] * n
    for i, r in enumerate(rings):
        a, b = _min_over(f_us, r), _min_over(f_them, r)
        side[i] = 0 if a >= UNREACHABLE or b >= UNREACHABLE else max(-127, min(127, b - a))
    altars = altar_board.positions()
    altar = [255] * n
    altar_turns = [plan.FAR] * n
    for ai, a in enumerate(altars):
        f = field_from(_ring([a], blocked, width, height), blocked | theirs)
        for i, r in enumerate(rings):
            t = plan.cap(_min_over(f, r), UNREACHABLE)
            if t < altar_turns[i]:
                altar[i], altar_turns[i] = ai, t
    pairs = [[plan.FAR] * n for _ in range(n)]
    for a in range(n):
        if not rings[a]:
            continue
        roots = Bitboard()
        for t in rings[a]:
            roots.set(t, True)
        f = field_from(roots, blocked | theirs)
        for b in range(a + 1, n):
            t = plan.cap(_min_over(f, rings[b]), UNREACHABLE)
            pairs[a][b] = pairs[b][a] = t
    return plan.encode(spawn, side, altar, altar_turns, pairs)


def play():
    pick = _pick()
    if pick is not None:
        cambc.draft.pick(pick)
    previous = bytes(cambc.DraftMemoryBuffer())
    if previous[:2] == plan.MAGIC:
        cambc.draft_memory.submit(previous)
        return
    before = _cambc.fuel_remaining()
    try:
        raw = build_plan()
    except Exception as e:
        print("draft: plan failed: %r" % (e,), flush=True)
        return
    cambc.draft_memory.submit(raw)
    print("draft: plan %d bytes, %d fuel" % (len(raw), before - _cambc.fuel_remaining()), flush=True)
