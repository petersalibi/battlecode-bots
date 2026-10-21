"""Draft phase: pick champions and write the opening plan into draft memory.

The first draft turn has 10x fuel and sees the whole map. A safe pick is locked in before any
analysis, because a missed pick is chosen at random.
"""
import cambc
import _cambc
from cambc import game_map

import config
import draft_features
import memory_codec
import nn
import weights

Team = cambc.Team
Draft = cambc.ChampionDraft

# Shard stat per draft pick; Cassi & Issac are two bodies (focus caster, strength melee).
_STAT_CODE = {"STRENGTH": 0, "FOCUS": 1, "HEALTH": 2}


def _priority():
    return [getattr(Draft, name) for name in config.DRAFT_PRIORITY]


def _map_is_small():
    return game_map.width() * game_map.height() < config.SMALL_MAP_TILES


def _dash_map():
    """True when holes wall most camps off from our spawn: with holes as walls fewer than half as
    many camps are within MAX_CAMP_TURNS as with the thin (dash-crossable) holes passable. Costs
    about 1.8M fuel (two fields and the camp groups), so only on the first draft turn, which has
    ten times the budget; later turns read the flag back from the plan."""
    try:
        previous = memory_codec.decode_plan(bytes(cambc.DraftMemoryBuffer()))
        if previous["team_size"]:
            return previous["dash_map"]
    except Exception:
        pass
    if _cambc.fuel_remaining() < config.DASH_MAP_FUEL:
        return False
    try:
        Bitboard, DistanceField = cambc.Bitboard, cambc.DistanceField
        walls, holes = game_map.walls(), game_map.holes()
        if holes.is_empty():
            return False
        camp_tiles = Bitboard()
        for group in game_map.camp_groups():
            for camp in group.camps():
                camp_tiles.set(camp.spawn_position(), True)
        blocked = walls | holes | game_map.altars() | game_map.spawn_region(cambc.Team.ENEMY) | camp_tiles
        walk = ~blocked
        thin = Bitboard()
        for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
            a = walk
            b = walk
            if dx:
                a = a.shift_right(dx)
                b = b.shift_right(-dx)
            if dy:
                a = a.shift_up(dy)
                b = b.shift_up(-dy)
            thin |= holes & a & b
        if thin.is_empty():
            return False
        spawn = game_map.spawn_region(cambc.Team.ALLY)
        reach = config.MAX_CAMP_TURNS + 1
        width, height = game_map.width(), game_map.height()

        def camps_within(board):
            field = DistanceField()
            field.compute(board, spawn, Bitboard(), reach)
            count = 0
            for tile in camp_tiles.positions():
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        x, y = tile.x + dx, tile.y + dy
                        if (dx or dy) and 0 <= x < width and 0 <= y < height and field.get(cambc.Vec2(x, y)) <= reach:
                            count += 1
                            break
                    else:
                        continue
                    break
            return count

        walking = camps_within(blocked)
        dashing = camps_within(blocked & ~thin)
        print(f"draft: camps within {reach} turns: walking {walking}, with dashes {dashing}", flush=True)
        return dashing >= config.DASH_MAP_RATIO * max(walking, 1) or walking == 0
    except Exception as error:
        print(f"draft: dash map check failed: {error!r}", flush=True)
        return False


def _map_info():
    """MapInfo from the engine: whole-map bitboards and four fields, about 6M fuel, so only on
    the first draft turn (25M). Mirrors tools/build_draft_dataset.map_info."""
    Bitboard, DistanceField, Vec2 = cambc.Bitboard, cambc.DistanceField, cambc.Vec2
    info = draft_features.MapInfo()
    W, H = game_map.width(), game_map.height()
    info.width, info.height = W, H
    info.team_size = cambc.draft.team_size()
    walls, holes, bushes, altars = game_map.walls(), game_map.holes(), game_map.bushes(), game_map.altars()
    info.walls, info.holes, info.bushes = walls.count(), holes.count(), bushes.count()
    groups = game_map.camp_groups()
    info.n_groups = len(groups)
    camp_tiles = Bitboard()
    camp_list = []
    for group in groups:
        info.total_gems += group.gem_value()
        info.total_gold += group.gold_value()
        for camp in group.camps():
            pos = camp.spawn_position()
            camp_tiles.set(pos, True)
            camp_list.append(pos)
            kind = camp._type
            if 0 <= kind < draft_features.N_KINDS:
                info.kind_counts[kind] += 1
    ours, theirs = game_map.spawn_region(Team.ALLY), game_map.spawn_region(Team.ENEMY)
    blocked = walls | holes | altars | camp_tiles | theirs
    field = DistanceField()
    field.compute(blocked, ours, Bitboard(), 61)

    def camps_within(fld, limit):
        n = 0
        for pos in camp_list:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx or dy:
                        x, y = pos.x + dx, pos.y + dy
                        if 0 <= x < W and 0 <= y < H and fld.get(Vec2(x, y)) <= limit:
                            n += 1
                            break
                else:
                    continue
                break
        return n
    info.camps_within_30 = camps_within(field, 30)
    info.camps_within_60 = camps_within(field, 60)
    if holes.is_empty():
        info.camps_within_60_dash = info.camps_within_60
    else:
        walk = ~blocked
        thin = Bitboard()
        for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
            a = walk
            b = walk
            if dx:
                a = a.shift_right(dx)
                b = b.shift_right(-dx)
            if dy:
                a = a.shift_up(dy)
                b = b.shift_up(-dy)
            thin |= holes & a & b
        if thin.is_empty():
            info.camps_within_60_dash = info.camps_within_60
        else:
            dash_field = DistanceField()
            dash_field.compute(blocked & ~thin, ours, Bitboard(), 61)
            info.camps_within_60_dash = camps_within(dash_field, 60)
    # Spawn to spawn and spawn to the nearest altar: a field from our spawn with the enemy
    # spawn and the altars left passable as goals, read next to them.
    full = DistanceField()
    full.compute(walls | holes | altars | camp_tiles, ours, Bitboard())
    best = 65535
    for tile in theirs.positions():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                x, y = tile.x + dx, tile.y + dy
                if 0 <= x < W and 0 <= y < H:
                    v = full.get(Vec2(x, y))
                    if v < best:
                        best = v
    info.spawn_turns = 0 if best >= 65535 else best
    best = 65535
    for tile in altars.positions():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx or dy:
                    x, y = tile.x + dx, tile.y + dy
                    if 0 <= x < W and 0 <= y < H:
                        v = full.get(Vec2(x, y))
                        if v < best:
                            best = v
    info.altar_turns = 0 if best >= 65535 else best
    return info


def _map_features():
    """The draft head's map part: from the plan on later turns, from the engine on the first."""
    try:
        previous = memory_codec.decode_plan(bytes(cambc.DraftMemoryBuffer()))
        if previous["team_size"] and previous.get("map_features"):
            return previous["map_features"]
    except Exception:
        pass
    if _cambc.fuel_remaining() < config.DRAFT_HEAD_FUEL:
        return None
    try:
        feats = draft_features.map_part(_map_info())
        print("draftlog pick=0 map=%s" % "".join("%02x" % v for v in feats), flush=True)
        return feats
    except Exception as error:
        print(f"draft: map features failed: {error!r}", flush=True)
        return None


def _net_pick(available, map_feats, chosen):
    """The draft head's pick among the available, allowed champions, or None."""
    net = weights.DRAFT_HEAD if config.USE_NN_DRAFT else None
    if net is None or map_feats is None:
        return None
    try:
        x = draft_features.build(map_feats, list(chosen), len(chosen))
        scores = net.forward(x)
        allowed_names = set(config.DRAFT_ALLOWED)
        mask = [p in available and any(getattr(Draft, n, -1) == p for n in allowed_names) for p in range(config.N_DRAFT_PICKS)]
        if sum(mask) < 1:
            return None
        if nn.margin(scores, mask) < getattr(weights, "DRAFT_HEAD_MARGIN", 0):
            return None
        return nn.masked_argmax(scores, mask)
    except Exception as error:
        print(f"draft: head failed: {error!r}", flush=True)
        return None


def _choose(available, small_map, dash_map=False):
    """Scripted pick: priority order, nudged by map size. Replace with a network later."""
    order = _priority()
    if dash_map:
        front = [getattr(Draft, name) for name in config.DASH_DRAFT_PRIORITY]
        order = front + [pick for pick in order if pick not in front]
    if small_map:
        # Small maps mean fights: prefer the tanks and stun-immune Marcella a little earlier.
        for name in ("MARCELLA", "RYTH"):
            pick = getattr(Draft, name)
            if pick in order:
                order.remove(pick)
                order.insert(1, pick)
    for pick in order:
        if pick in available:
            return pick
    return available[0]


def _body_stats(picks):
    stats = []
    for pick in picks:
        if pick == Draft.CASSI_AND_ISSAC:
            stats.append(_STAT_CODE[config.BUILD_STAT["CASSI"]])
            stats.append(_STAT_CODE[config.BUILD_STAT["ISSAC"]])
        else:
            name = next(n for n in config.BUILD_STAT if getattr(Draft, n, None) == pick)
            stats.append(_STAT_CODE[config.BUILD_STAT[name]])
    return stats


def play():
    chosen = cambc.draft.current_draft(Team.ALLY)
    available = [pick for pick in range(config.N_DRAFT_PICKS) if pick not in chosen]
    if not available:
        return
    small_map = _map_is_small()
    dash_map = _dash_map()
    map_feats = _map_features()

    # Lock in a pick first, then spend the remaining fuel on the plan.
    pick = _net_pick(available, map_feats, chosen)
    source = "head"
    if pick is None:
        pick = _choose(available, small_map, dash_map)
        source = "list"
    cambc.draft.pick(pick)

    picks = list(chosen) + [pick]
    team_size = cambc.draft.team_size()
    aggression = 170 if small_map else 90
    plan = memory_codec.encode_plan(team_size, aggression, small_map, _body_stats(picks), dash_map, map_feats)
    cambc.draft_memory.submit(plan)
    print(f"draft: round {len(picks)}/{team_size} picked {Draft.to_string(pick) if hasattr(Draft, 'to_string') else pick} ({source})",
          flush=True)
