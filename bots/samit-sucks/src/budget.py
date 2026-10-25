"""The turn's fuel plan, from the game state alone.

Optional work (the networks, mostly) runs when an estimate of everything else the turn may hold
leaves room for it. The estimate reads only the map and what is in sight, never the fuel left, so
the same state always makes the same choice however much the surrounding code costs. The constants
are the upper ends (about p99) of 208K profiled turns of v42 (config, "Fuel plan").
"""
import config

TURN_FUEL = 2_500_000


def turn_estimate(w):
    """Fuel the turn's mandatory work may take: refresh, the script's decision, and acting, which
    can build a whole-map field or re-rank every camp group."""
    refresh = (config.EST_REFRESH + config.EST_REFRESH_ENEMY * len(w.enemies)
               + config.EST_REFRESH_CAMP * len(w.visible_camps))
    act = max(config.FIELD_COST_PER_TILE * w.width * w.height, config.EST_RETARGET_GROUP * len(w.groups))
    return refresh + config.EST_DECIDE + act


def fits(w, cost):
    """True when cost more fuel fits beside the turn's mandatory work."""
    return turn_estimate(w) + cost <= TURN_FUEL - config.EST_MARGIN


def first_turn_fits(n_groups, n_camps, area):
    """Whether the second half of setup (spawn field, roster, abilities) fits in the first turn
    after the camp groups are built. Decided from the map's counts, so a map always splits setup
    the same way: yellow brick road, boss battle, road of riches and islands finish on round 2."""
    groups = config.INIT_BASE + config.INIT_PER_GROUP * n_groups + config.INIT_PER_CAMP * n_camps
    rest = config.INIT_REST + config.FIELD_COST_PER_TILE * area
    return groups + rest + config.EST_MARGIN <= config.FIRST_TURN_FUEL

