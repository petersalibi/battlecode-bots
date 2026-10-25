"""Fuel guards. Each call to remaining() costs roughly 1-2K fuel, so never call it inside loops."""
import _cambc

import config

remaining = _cambc.fuel_remaining


def can_afford(cost, reserve=config.FUEL_RESERVE):
    """True when cost more fuel can be spent and still leave the reserve."""
    return remaining() - cost > reserve
