"""The macro plan (redesign phase 4), made once in the draft and read by every body.

The first draft turn has ten times the fuel and sees the whole map, so the team's plan is made
there and carried in draft memory:

    map class   how much of the map's camp value lies on the line between the spawns
    pairs       bodies move in pairs (slot 2k with 2k+1; an odd last body joins the pair before)
    routes      per pair, the order of camp groups on our side of the map, nearest-valuable first
    bank altar  per pair, the altar on our side nearest its route
    ward spots  per pair, a tile on the lane between its route and the enemy spawn, a little on
                our side of the line where both spawns are equally far

During the game a body prefers its route's groups and its partner's claim, banks at its pair's
altar unless another is much nearer, and wards its pair's spot when passing within reach. The
plan is static: events (a group taken, a partner dead) fall back on the greedy script.

Layout from MACRO_AT in draft memory (MACRO_MAGIC first):
    map class, number of pairs, then per pair: altar x, y, ward x, y, route length, route groups
"""
import cambc
from cambc import game_map

import config

Team = cambc.Team
MAGIC = b"M1"
UNREACHABLE = 0x7FFFFFFF


def _centre(board):
    tiles = board.positions()
    if not tiles:
        return None
    return (sum(t.x for t in tiles) // len(tiles), sum(t.y for t in tiles) // len(tiles))


def pair_of(slot, team_size):
    n_pairs = max(1, team_size // 2)
    return min(slot // 2, n_pairs - 1)


def partners(slot, team_size):
    """The other slots in this slot's pair (a trio when the team is odd)."""
    p = pair_of(slot, team_size)
    return [s for s in range(team_size) if s != slot and pair_of(s, team_size) == p]


def plan(team_size):
    """The plan's bytes (MAGIC first). Costs a few million fuel: two whole-map fields, the camp
    groups and a pass over each group's tiles."""
    Bitboard, DistanceField, Vec2 = cambc.Bitboard, cambc.DistanceField, cambc.Vec2
    width, height = game_map.width(), game_map.height()
    groups = game_map.camp_groups()
    camp_tiles = Bitboard()
    tile_lists = []
    for g in groups:
        tiles = g.spawn_tiles()
        camp_tiles |= tiles
        tile_lists.append(tiles.positions())
    ours, theirs = game_map.spawn_region(Team.ALLY), game_map.spawn_region(Team.ENEMY)
    altar_board = game_map.altars()
    blocked = game_map.walls() | game_map.holes() | altar_board | camp_tiles
    f_us, f_them = DistanceField(), DistanceField()
    f_us.compute(blocked | theirs, ours, Bitboard())
    f_them.compute(blocked | ours, theirs, Bitboard())

    def side_distances(tiles):
        """(our walking turns, theirs, the tile beside the group nearest us) over the tiles round it."""
        best_us, best_them, anchor = UNREACHABLE, UNREACHABLE, None
        for t in tiles:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    x, y = t.x + dx, t.y + dy
                    if (dx or dy) and 0 <= x < width and 0 <= y < height:
                        v = Vec2(x, y)
                        if blocked.is_set(v):
                            continue
                        a, b = f_us.get(v), f_them.get(v)
                        if a < best_us:
                            best_us, anchor = a, (x, y)
                        if b < best_them:
                            best_them = b
        return best_us, best_them, anchor

    info = []                     # (index, value, our turns, their turns, anchor)
    central_value = total_value = 0.0
    for i, g in enumerate(groups):
        d_us, d_them, anchor = side_distances(tile_lists[i])
        value = g.gem_value() * config.GEM_VALUE + g.gold_value() * config.GOLD_VALUE
        if anchor is None or d_us == UNREACHABLE:
            continue
        info.append((i, value, d_us, d_them, anchor))
        total_value += value
        if abs(d_us - d_them) <= config.MACRO_CENTRAL_BAND:
            central_value += value
    map_class = int(255 * central_value / total_value) if total_value > 0 else 0

    # Our side: groups no further from us than from them, plus a margin. Routes are dealt to the
    # pairs in turn, each taking the most value per tile of walk from where its route stands.
    mine = [g for g in info if g[2] <= g[3] + config.MACRO_SIDE_MARGIN]
    n_pairs = max(1, team_size // 2)
    start = _centre(ours) or (0, 0)
    enemy = _centre(theirs) or (width - 1, height - 1)
    cur = [start] * n_pairs
    routes = [[] for _ in range(n_pairs)]
    free = list(mine)
    for _ in range(config.MACRO_ROUTE_LEN):
        for p in range(n_pairs):
            if not free:
                break
            cx, cy = cur[p]
            best, best_key = None, None
            for g in free:
                ax, ay = g[4]
                key = g[1] / (config.MACRO_ROUTE_STEP + max(abs(ax - cx), abs(ay - cy)))
                if best_key is None or key > best_key:
                    best, best_key = g, key
            free.remove(best)
            routes[p].append(best[0])
            cur[p] = best[4]

    by_index = {g[0]: g for g in info}
    altars = altar_board.positions()
    out = bytearray(MAGIC)
    out.append(map_class & 0xFF)
    out.append(n_pairs)
    for p in range(n_pairs):
        anchors = [by_index[i][4] for i in routes[p]] or [start]
        mx = sum(a[0] for a in anchors) // len(anchors)
        my = sum(a[1] for a in anchors) // len(anchors)
        # Bank altar: on our side, nearest the route's middle.
        altar, altar_key = None, None
        for a in altars:
            near_us = min((f_us.get(Vec2(a.x + dx, a.y + dy)) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                           if 0 <= a.x + dx < width and 0 <= a.y + dy < height), default=UNREACHABLE)
            near_them = min((f_them.get(Vec2(a.x + dx, a.y + dy)) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                             if 0 <= a.x + dx < width and 0 <= a.y + dy < height), default=UNREACHABLE)
            if near_us == UNREACHABLE:
                continue
            key = (0 if near_us <= near_them + config.MACRO_SIDE_MARGIN else 1, max(abs(a.x - mx), abs(a.y - my)))
            if altar_key is None or key < altar_key:
                altar, altar_key = (a.x, a.y), key
        # Ward spot: walk the straight line from the route's middle to the enemy spawn and stop
        # where the enemy is MACRO_WARD_LEAD turns further than we are: the lane in front of
        # our farm, seen before anything reaches it.
        ward = None
        steps = max(abs(enemy[0] - mx), abs(enemy[1] - my))
        for k in range(1, steps + 1):
            x = mx + (enemy[0] - mx) * k // steps
            y = my + (enemy[1] - my) * k // steps
            v = Vec2(x, y)
            if blocked.is_set(v):
                continue
            a, b = f_us.get(v), f_them.get(v)
            if a == UNREACHABLE or b == UNREACHABLE:
                continue
            if b - a <= config.MACRO_WARD_LEAD:
                ward = (x, y)
                break
        ax, ay = altar if altar is not None else (255, 255)
        wx, wy = ward if ward is not None else (255, 255)
        out.extend((ax, ay, wx, wy))
        route = routes[p][:config.MACRO_ROUTE_LEN]
        out.append(len(route))
        out.extend(route)
    return bytes(out)


def decode(raw):
    """{"map_class", "pairs": [{"altar", "ward", "route"}]} or None."""
    if len(raw) < 4 or raw[:2] != MAGIC:
        return None
    try:
        n_pairs = raw[3]
        at = 4
        pairs = []
        for _ in range(n_pairs):
            ax, ay, wx, wy, n = raw[at:at + 5]
            route = list(raw[at + 5:at + 5 + n])
            at += 5 + n
            pairs.append({"altar": None if ax == 255 else (ax, ay),
                          "ward": None if wx == 255 else (wx, wy),
                          "route": route})
        return {"map_class": raw[2], "pairs": pairs}
    except Exception:
        return None
