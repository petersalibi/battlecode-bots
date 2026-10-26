"""Shared memory (8 bytes): what each body is doing and where it is, and the last enemy sighting.

    bytes 0-4  one per body (slot = position in cambc.champions(ALLY)): its claim or its position,
               as byte 7 says. Claim: bits 7-6 the job kind (JOB_*), bits 5-0 group + 1 (0 = none).
               Position: bits 7-4 cell x, bits 3-0 cell y on a 16x16 grid.
    byte 5     sighting: the cell of the enemy nearest the body that saw it
    byte 6     bits 5-0 the round (mod 64) of the sighting, bits 7-6 enemies seen (1, 2, 3+; 0 = none)
    byte 7     bits 4-0: bit k set = byte k holds a claim

A body publishes its claim on the turn it changes (not twice running), every CLAIM_REFRESH rounds,
and while dead; its position otherwise. Readers keep both. Measured (phase 2, 80 games): every
belief at most a round old was in the ally's cell then or a turn before.
"""
import cambc

import config

IDLE, FARM, HELP, AWAY = range(4)          # AWAY: banking, going home, dead
BODIES = 5
CELLS = 16
SIGHT, SIGHT_ROUND, KINDS = 5, 6, 7


def encode(kind, group):
    return ((kind & 3) << 6) | (group + 1 if 0 <= group <= 62 else 0)


_DECODED = [(v >> 6, (v & 63) - 1) for v in range(256)]


class Board:
    def __init__(self, width, height, slot):
        self.slot = slot
        self.claims = [(IDLE, -1)] * BODIES
        self.cells = [None] * BODIES               # (x, y) at the centre of the body's last cell
        self.live = [False] * BODIES               # the byte held a position at our last read
        self.cell_round = [-1] * BODIES
        self.seen = [False] * BODIES               # a claim has been read from this body
        self.sighting = None                       # ((x, y), count, round)
        self._cx = [x * CELLS // width for x in range(width)]
        self._cy = [y * CELLS // height for y in range(height)]
        self._mx = [((2 * c + 1) * width) // (2 * CELLS) for c in range(CELLS)]
        self._my = [((2 * c + 1) * height) // (2 * CELLS) for c in range(CELLS)]
        self._data = bytearray(8)
        self._last = bytearray(8)
        self._read_round = -1
        self._sent = -1
        self._sent_round = -1000
        self._claimed_last = False

    def cell_of(self, x, y):
        return (self._cx[x] << 4) | self._cy[y]

    def centre(self, v):
        return self._mx[v >> 4], self._my[v & 15]

    def age(self, k, rnd):
        return 0 if self.live[k] else rnd - self.cell_round[k]

    def read(self, rnd):
        data, last = self._data, self._last
        cambc.shared_memory.read(0, data)
        previous, self._read_round = self._read_round, rnd
        if data == last:
            return
        kinds = data[KINDS]
        changed = kinds ^ last[KINDS]
        for k in range(BODIES):
            v = data[k]
            if v == last[k] and not changed >> k & 1:
                continue
            if kinds >> k & 1:
                self.claims[k] = _DECODED[v]
                self.seen[k] = True
                if self.live[k]:
                    self.live[k] = False
                    self.cell_round[k] = previous
            elif self.seen[k]:
                self.cells[k] = self.centre(v)
                self.live[k] = True
        if data[SIGHT] != last[SIGHT] or data[SIGHT_ROUND] != last[SIGHT_ROUND]:
            count = data[SIGHT_ROUND] >> 6
            if count:
                seen = rnd - ((rnd - (data[SIGHT_ROUND] & 63)) & 63)
                self.sighting = (self.centre(data[SIGHT]), count, seen)
        last[:] = data

    def publish(self, rnd, kind, group, pos, dead=False):
        value = encode(kind, group)
        self.claims[self.slot] = _DECODED[value]
        claim = (dead or self._sent < 0 or rnd - self._sent_round >= config.CLAIM_REFRESH
                 or (value != self._sent and not self._claimed_last))
        self._claimed_last = claim
        slot = self.slot
        if slot >= BODIES:
            return
        data = self._data
        if claim:
            self._sent, self._sent_round = value, rnd
            byte, kinds = value, data[KINDS] | (1 << slot)
        else:
            byte, kinds = self.cell_of(pos.x, pos.y), data[KINDS] & ~(1 << slot)
        if byte != data[slot]:
            cambc.shared_memory[slot] = data[slot] = byte
        self._last[slot] = byte
        if kinds != data[KINDS]:
            cambc.shared_memory[KINDS] = data[KINDS] = kinds
        self._last[KINDS] = (self._last[KINDS] & ~(1 << slot)) | (kinds & (1 << slot))

    def report(self, rnd, pos, count):
        """We see count enemies, the nearest at pos."""
        count = 3 if count > 3 else count
        s = self.sighting
        if s is not None and s[2] == rnd and s[1] > count:
            return
        data = self._data
        cell, stamp = self.cell_of(pos.x, pos.y), (count << 6) | (rnd & 63)
        if cell != data[SIGHT]:
            cambc.shared_memory[SIGHT] = data[SIGHT] = cell
        if stamp != data[SIGHT_ROUND]:
            cambc.shared_memory[SIGHT_ROUND] = data[SIGHT_ROUND] = stamp
        self.sighting = (self.centre(cell), count, rnd)
        self._last[SIGHT], self._last[SIGHT_ROUND] = cell, stamp
