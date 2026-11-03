"""The draft's map model, carried in draft memory (1,024 bytes) to every body.

The draft's first turn has 100x fuel and sees the whole map, so the expensive geometry is done
there once: walking distances from our spawn, from the enemy spawn and between every pair of camp
groups, and each group's nearest altar. In the game a body then prices any job with a table read
instead of building a field per candidate (the old per-turn re-ranking cost 0.5-1.1M a turn).

Layout:
    b"S2", n groups, n altars, k (0 = the full pair table, else k nearest neighbours per group)
    per group: turns from our spawn, their spawn minus ours + 128, nearest altar index, its turns
    full table: for a < b, turns(a, b) in row order; or per group k x (neighbour index, turns)
Turns are capped at 254; 255 means unreachable.
"""

MAGIC = b"S2"
FAR = 255


def cap(turns, unreachable):
    return FAR if turns is None or turns >= unreachable else min(254, int(turns))


def encode(spawn, side, altar, altar_turns, pairs, budget=1024):
    """spawn, side, altar, altar_turns: per group; pairs: n x n list of turns (FAR = unreachable)."""
    n = len(spawn)
    head = bytearray(MAGIC)
    full = n * (n - 1) // 2
    room = budget - 6 - 4 * n
    k = 0 if full <= room else max(1, min(n - 1, room // (2 * n)))
    head.extend((n, 0, k))
    for i in range(n):
        head.extend((spawn[i], max(0, min(255, side[i] + 128)), altar[i], altar_turns[i]))
    if k == 0:
        for a in range(n):
            for b in range(a + 1, n):
                head.append(pairs[a][b])
    else:
        for a in range(n):
            near = sorted((pairs[a][b], b) for b in range(n) if b != a)[:k]
            for turns, b in near:
                head.extend((b, turns))
    return bytes(head)


class Plan:
    """Decoded model. turns(a, b) falls back to None when the table does not hold the pair."""

    def __init__(self, raw):
        self.ok = len(raw) >= 5 and bytes(raw[:2]) == MAGIC
        self.n = raw[2] if self.ok else 0
        self.k = raw[4] if self.ok else 0
        self.spawn, self.side, self.altar, self.altar_turns = [], [], [], []
        self._table = {}
        if not self.ok:
            return
        at = 5
        for _ in range(self.n):
            s, side, a, at_turns = raw[at:at + 4]
            self.spawn.append(s)
            self.side.append(side - 128)
            self.altar.append(a)
            self.altar_turns.append(at_turns)
            at += 4
        n = self.n
        if self.k == 0:
            for a in range(n):
                for b in range(a + 1, n):
                    self._table[(a, b)] = raw[at]
                    at += 1
        else:
            for a in range(n):
                for _ in range(self.k):
                    b, turns = raw[at], raw[at + 1]
                    at += 2
                    key = (a, b) if a < b else (b, a)
                    self._table[key] = turns

    def turns(self, a, b):
        if a == b:
            return 0
        t = self._table.get((a, b) if a < b else (b, a))
        return None if t is None or t == FAR else t
