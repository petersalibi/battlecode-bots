"""Team memory layouts.

Shared memory (8 bytes, read/write all match):
    bytes 0-4   one byte per ally body, indexed by the body's position in cambc.champions(ALLY).
                The byte carries either the body's claim or its position, as byte 7 says:
                claim     bits 7-6: status (STATUS_*), bits 5-0: claimed camp group index + 1 (0 = none)
                position  bits 7-4: cell x, bits 3-0: cell y, on a 16x16 grid over the map
                A body publishes its claim on the turn it changes (a dead body every turn, and every
                CLAIM_REFRESH rounds regardless) and its position on every other turn, so readers keep
                both in a Board. A claim of STATUS_IDLE with a group is a call for help: the body is
                holding at a camp it cannot clear alone, waiting for allies (actions.farm).
    byte 5      enemy sighting: the cell of the enemy nearest the body that saw it
    byte 6      bits 5-0 the round (mod 64) of the sighting, bits 7-6 how many enemies were in sight
                (1, 2, 3 or more; 0 = no sighting yet)
    byte 7      bits 4-0: bit k set = byte k holds a claim, clear = a position. Bit 7: bytes 5-6
                are a focus call (the enemy a fighting ally is attacking), not a plain sighting.
                Bits 6-5 unused.
    A body's first publication is always a claim, so readers ignore its byte until they have seen
    one (the zeroed memory would read as cell 0,0).

Draft memory (1,024 bytes, written in the draft, read-only afterwards):
    b"HY1" magic, team size, aggression 0-255, map-size class, then per-body shard stat.
"""
import cambc

import config

STATUS_IDLE, STATUS_FARM, STATUS_CAPTURE, STATUS_BUSY = range(4)   # BUSY = tribute, recall, dead
MAX_BODIES = 6          # claims per team in Board.claims (and the draft plan's per-body slots)
SHARED_BODIES = 5       # bodies with a byte in shared memory: teams field 3 to 5
NO_GROUP = -1
_MAGIC = b"HY1"

CELLS = 16              # position grid per axis
CLAIM_REFRESH = 32      # rounds after which an unchanged claim is published again (a reader may miss a turn)
SIGHTING_BYTE, SIGHTING_ROUND_BYTE, KIND_BYTE = 5, 6, 7
FOCUS_BIT = 0x80


def encode_claim(status, group_index):
    group = 0 if group_index is None or group_index < 0 or group_index > 62 else group_index + 1
    return ((status & 3) << 6) | group


def decode_claim(value):
    return value >> 6, (value & 63) - 1


_CLAIMS = [decode_claim(v) for v in range(256)]   # every byte decoded once, at build time
NO_CLAIM = _CLAIMS[0]


class Board:
    """One body's picture of the shared memory, kept across turns: every ally's latest claim and
    position (a byte carries only one of them at a time) and the latest enemy sighting. Most turns
    the memory is as we last left it, so reading and publishing cost an engine call and a compare."""

    def __init__(self, width, height, slot):
        self.slot = slot
        self.claims = [NO_CLAIM] * MAX_BODIES
        self.seen = [False] * SHARED_BODIES        # a claim from this body has been read: its byte is live
        self.cells = [None] * SHARED_BODIES        # (x, y) tile at the centre of the body's last cell
        self.live = [False] * SHARED_BODIES        # the body's byte held its position at our last read
        self.cell_round = [-1] * SHARED_BODIES     # the last round we read that byte as a position
        self.sighting = None                       # ((x, y), enemies 1-3, round seen)
        self.focus = None                          # ((x, y) cell centre, round) of the last focus call
        # Cell of a tile and the centre tile of a cell, per axis, built once.
        self._cell_x = [x * CELLS // width for x in range(width)]
        self._cell_y = [y * CELLS // height for y in range(height)]
        self._centre_x = [((2 * c + 1) * width) // (2 * CELLS) for c in range(CELLS)]
        self._centre_y = [((2 * c + 1) * height) // (2 * CELLS) for c in range(CELLS)]
        self._data = bytearray(8)                  # the memory as read this turn (and then as we write it)
        self._last = bytearray(8)                  # the memory as decoded last time
        self._read_round = -1
        # What this body last published: its claim byte and when.
        self._claim_sent = -1
        self._claim_round = -1000
        self._claim_last_turn = False

    def centre(self, value):
        """The tile at the centre of a position byte's cell."""
        return self._centre_x[value >> 4], self._centre_y[value & 15]

    def cell_of(self, x, y):
        return (self._cell_x[x] << 4) | self._cell_y[y]

    def age(self, k, round_number):
        """Rounds since body k's position was last read as current (0 = its byte holds it now)."""
        return 0 if self.live[k] else round_number - self.cell_round[k]

    def read(self, round_number):
        """Once per turn, before publish: one engine read, decoded only when it changed."""
        data, last = self._data, self._last
        cambc.shared_memory.read(0, data)
        previous_read, self._read_round = self._read_round, round_number
        if data == last:
            return
        kinds = data[KIND_BYTE]
        changed = kinds ^ last[KIND_BYTE]
        for k in range(SHARED_BODIES):
            value = data[k]
            if value == last[k] and not changed >> k & 1:
                continue
            if kinds >> k & 1:
                self.claims[k] = _CLAIMS[value]
                self.seen[k] = True
                if self.live[k]:
                    self.live[k] = False
                    self.cell_round[k] = previous_read
            elif self.seen[k]:
                self.cells[k] = (self._centre_x[value >> 4], self._centre_y[value & 15])
                self.live[k] = True
        if data[SIGHTING_BYTE] != last[SIGHTING_BYTE] or data[SIGHTING_ROUND_BYTE] != last[SIGHTING_ROUND_BYTE] \
                or (kinds ^ last[KIND_BYTE]) & FOCUS_BIT:
            count = data[SIGHTING_ROUND_BYTE] >> 6
            if count:
                seen_round = round_number - ((round_number - (data[SIGHTING_ROUND_BYTE] & 63)) & 63)
                self.sighting = (self.centre(data[SIGHTING_BYTE]), count, seen_round)
                if kinds & FOCUS_BIT:
                    self.focus = (self.sighting[0], seen_round)
        last[:] = data

    def publish(self, round_number, status, group_index, pos, dead=False):
        """End of turn: our claim when it changed (not twice running, so the position still goes
        out), when it is due a refresh, or when we are dead; otherwise our position. Only bytes
        that change are written (nobody else writes between our read and this)."""
        value = encode_claim(status, group_index)
        claim = (dead or self._claim_sent < 0 or round_number - self._claim_round >= CLAIM_REFRESH
                 or (value != self._claim_sent and not self._claim_last_turn))
        self._claim_last_turn = claim
        self.claims[self.slot] = _CLAIMS[value]
        slot = self.slot
        if slot >= SHARED_BODIES:
            return
        data = self._data
        if claim:
            self._claim_sent = value
            self._claim_round = round_number
            byte, kinds = value, data[KIND_BYTE] | (1 << slot)
        else:
            byte, kinds = (self._cell_x[pos.x] << 4) | self._cell_y[pos.y], data[KIND_BYTE] & ~(1 << slot)
        # Our own bytes go into _last as well: next turn's read must not decode what we wrote.
        if byte != data[slot]:
            cambc.shared_memory[slot] = data[slot] = byte
        self._last[slot] = byte
        if kinds != data[KIND_BYTE]:
            cambc.shared_memory[KIND_BYTE] = data[KIND_BYTE] = kinds
        last = self._last
        last[KIND_BYTE] = (last[KIND_BYTE] & ~(1 << slot)) | (kinds & (1 << slot))

    def focus_cell(self, round_number):
        """The cell value of a focus call made this round or last, else -1."""
        if self.focus is None or round_number - self.focus[1] > 1:
            return -1
        x, y = self.focus[0]
        return (self._cell_x[x] << 4) | self._cell_y[y]

    def report_enemies(self, round_number, pos, count, focus=False):
        """We see `count` enemies, the nearest at pos: post it unless an ally posted a bigger
        sighting this round already, or a focus call this round or last. focus: pos is the enemy
        we are attacking, and allies in the fight should attack it too."""
        count = 3 if count > 3 else count
        sighting = self.sighting
        if not focus:
            if sighting is not None and sighting[2] == round_number and sighting[1] > count:
                return
            if self.focus is not None and round_number - self.focus[1] <= 1:
                return
        data, memory = self._data, cambc.shared_memory
        kinds = (data[KIND_BYTE] | FOCUS_BIT) if focus else (data[KIND_BYTE] & ~FOCUS_BIT)
        if kinds != data[KIND_BYTE]:
            memory[KIND_BYTE] = data[KIND_BYTE] = kinds
            self._last[KIND_BYTE] = (self._last[KIND_BYTE] & ~FOCUS_BIT) | (kinds & FOCUS_BIT)
        cell, stamp = (self._cell_x[pos.x] << 4) | self._cell_y[pos.y], (count << 6) | (round_number & 63)
        if cell != data[SIGHTING_BYTE]:
            memory[SIGHTING_BYTE] = data[SIGHTING_BYTE] = cell
        if stamp != data[SIGHTING_ROUND_BYTE]:
            memory[SIGHTING_ROUND_BYTE] = data[SIGHTING_ROUND_BYTE] = stamp
        self.sighting = (self.centre(cell), count, round_number)   # ours, as readers will decode it
        if focus:
            self.focus = (self.sighting[0], round_number)
        self._last[SIGHTING_BYTE], self._last[SIGHTING_ROUND_BYTE] = cell, stamp


def write_contested(group_index, round_number):   # kept for callers (all behind switches that are off);
    pass                                           # byte 6 now holds the enemy sighting


def read_contested(round_number):
    return None


def write_enemy_seen(round_number):           # kept for callers; byte 5 now holds the enemy sighting
    pass


def read_enemy_seen():
    return 0


def write_breakout(round_number):             # kept for callers (BREAKOUT_SIGNAL is off); byte 7 is the kind mask
    pass


def breakout_age(round_number):
    return None


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


__all__ = ["STATUS_IDLE", "STATUS_FARM", "STATUS_CAPTURE", "STATUS_BUSY", "Board",
           "write_enemy_seen", "read_enemy_seen", "write_contested", "read_contested", "write_breakout", "breakout_age",
           "encode_plan", "decode_plan",
           "default_plan", "config"]
