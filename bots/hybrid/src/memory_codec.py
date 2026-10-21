"""Team memory layouts.

Shared memory (8 bytes, read/write all match):
    bytes 0-5   one claim byte per ally body, indexed by the body's position in cambc.champions(ALLY)
                bits 7-6: status (STATUS_*), bits 5-0: claimed camp group index + 1 (0 = none)
    byte 6      low byte of the round an enemy was last seen by anyone (0 = never)
    byte 7      break-out signal: bit 7 set, bits 6-0 the round (mod 128) a gated body decided the
                team leaves spawn together (0 = none)

Draft memory (1,024 bytes, written in the draft, read-only afterwards):
    b"HY1" magic, team size, aggression 0-255, map-size class, then per-body shard stat.
"""
import cambc

import config

STATUS_IDLE, STATUS_FARM, STATUS_CAPTURE, STATUS_BUSY = range(4)   # BUSY = tribute, recall, dead
MAX_BODIES = 6
NO_GROUP = -1
_MAGIC = b"HY1"


def encode_claim(status, group_index):
    group = 0 if group_index is None or group_index < 0 or group_index > 62 else group_index + 1
    return ((status & 3) << 6) | group


def decode_claim(value):
    return value >> 6, (value & 63) - 1


def read_claims():
    """List of (status, group_index) per body slot. Costs about 9K fuel."""
    data = bytearray(MAX_BODIES)
    cambc.shared_memory.read(0, data)
    return [decode_claim(v) for v in data]


def write_claim(slot, status, group_index):
    if 0 <= slot < MAX_BODIES:
        cambc.shared_memory[slot] = encode_claim(status, group_index)


def write_enemy_seen(round_number):
    cambc.shared_memory[6] = round_number & 0xFF


def read_enemy_seen():
    return cambc.shared_memory[6]


def write_breakout(round_number):
    cambc.shared_memory[7] = 0x80 | (round_number & 0x7F)


def breakout_age(round_number):
    """Rounds since the last break-out signal, or None when there is none."""
    value = cambc.shared_memory[7]
    if not value & 0x80:
        return None
    return (round_number - (value & 0x7F)) & 0x7F


# ---- Draft memory -------------------------------------------------------------

MAP_FEATURES_AT = 6 + MAX_BODIES    # byte offset of the draft head's map features in the plan
N_MAP_FEATURES = 22


PLANNED_PICKS_AT = MAP_FEATURES_AT + N_MAP_FEATURES   # the draft's planned picks (pick + 1 each, 0 = none)


def encode_plan(team_size, aggression, small_map, stats, dash_map=False, map_features=None, planned_picks=None):
    body = bytearray(_MAGIC)
    body.append(team_size & 0xFF)
    body.append(max(0, min(255, aggression)))
    body.append((1 if small_map else 0) | (2 if dash_map else 0))
    stats = list(stats)[:MAX_BODIES]
    for stat in stats:
        body.append(stat & 0xFF)
    body.extend(bytes(MAX_BODIES - len(stats)))
    if map_features:
        body.extend(bytes(max(0, min(255, int(v))) for v in list(map_features)[:N_MAP_FEATURES]))
    if planned_picks:
        body.extend(bytes(MAP_FEATURES_AT + N_MAP_FEATURES - len(body)))
        body.extend(bytes((p + 1) & 0xFF for p in list(planned_picks)[:MAX_BODIES]))
    return bytes(body)


def decode_plan(raw):
    """Returns a dict, with defaults when the memory is empty or from another bot version."""
    plan = {"team_size": 0, "aggression": 128, "small_map": False, "dash_map": False, "stats": [], "map_features": None, "planned_picks": []}
    if len(raw) >= 6 and raw[:3] == _MAGIC:
        plan["team_size"] = raw[3]
        plan["aggression"] = raw[4]
        plan["small_map"] = bool(raw[5] & 1)
        plan["dash_map"] = bool(raw[5] & 2)
        plan["stats"] = [v for v in raw[6:6 + MAX_BODIES]][:plan["team_size"] + 1] if plan["team_size"] else list(raw[6:6 + MAX_BODIES])
        if len(raw) >= MAP_FEATURES_AT + N_MAP_FEATURES:
            feats = list(raw[MAP_FEATURES_AT:MAP_FEATURES_AT + N_MAP_FEATURES])
            plan["map_features"] = feats if any(feats) else None
        plan["planned_picks"] = [v - 1 for v in raw[PLANNED_PICKS_AT:PLANNED_PICKS_AT + MAX_BODIES] if v]
    return plan


def default_plan():
    return decode_plan(b"")


__all__ = ["STATUS_IDLE", "STATUS_FARM", "STATUS_CAPTURE", "STATUS_BUSY", "read_claims", "write_claim",
           "write_enemy_seen", "read_enemy_seen", "write_breakout", "breakout_age", "encode_plan", "decode_plan",
           "default_plan", "config"]
