"""Draft phase: pick champions and write the opening plan into draft memory.

The first draft turn has 10x fuel and sees the whole map. A safe pick is locked in before any
analysis, because a missed pick is chosen at random.
"""
import cambc
from cambc import game_map

import config
import memory_codec

Team = cambc.Team
Draft = cambc.ChampionDraft

# Shard stat per draft pick; Cassi & Issac are two bodies (focus caster, strength melee).
_STAT_CODE = {"STRENGTH": 0, "FOCUS": 1, "HEALTH": 2}


def _priority():
    return [getattr(Draft, name) for name in config.DRAFT_PRIORITY]


def _map_is_small():
    return game_map.width() * game_map.height() < config.SMALL_MAP_TILES


def _choose(available, small_map):
    """Scripted pick: priority order, nudged by map size. Replace with a network later."""
    order = _priority()
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

    # Lock in a pick first, then spend the remaining fuel on the plan.
    pick = _choose(available, small_map)
    cambc.draft.pick(pick)

    picks = list(chosen) + [pick]
    team_size = cambc.draft.team_size()
    aggression = 170 if small_map else 90
    plan = memory_codec.encode_plan(team_size, aggression, small_map, _body_stats(picks))
    cambc.draft_memory.submit(plan)
    print(f"draft: round {len(picks)}/{team_size} picked {Draft.to_string(pick) if hasattr(Draft, 'to_string') else pick}",
          flush=True)
