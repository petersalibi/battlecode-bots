"""One evaluation a turn: where to stand, then what to hit and cast. No modes.

Tiles (stand still, the free neighbours, dash landings when a dash is ready) are scored as

    progress towards the job x PROGRESS_WEIGHT
    - damage that could land there next turn / our effective health x THREAT_WEIGHT
    + allies within ALLY_NEAR while threatened
    + what we could hit from there this turn

Damage sources: visible enemies (reach + 1, a soft share two tiles further), awake camps outside
the job's group for which we would be the nearest champion in reach (a tie counts half), and the
shared sighting when no enemy is in sight, its reach growing and its weight fading with age. A
source that adds the same to every candidate does not change the choice and is not scored (a
tile costs ~13K fuel to score); with none left the field's own next step is taken.
"""
import cambc
from cambc import vision

import config
import market as M
from world import REWARD

ItemType = cambc.ItemType
UNREACHABLE = 60000
CAPTURE_STAY = 6.0          # standing on a capture under way is worth this many steps
ATTACK_JOB = 1.0            # a hit on the job's camp from a tile
ATTACK_ENEMY = 1.5          # a hit on an enemy champion we mean to fight
ATTACK_KILL = 4.0           # a hit that can finish one


class Tactics:
    def __init__(self, w, paths, market):
        self.w, self.paths, self.market = w, paths, market
        self.ward_round = -100
        self.ward_pos = None
        self.trace = ""

    # ---- the turn ----------------------------------------------------------------------
    def run(self):
        w, job = self.w, self.market.job
        self.trace = ""
        self.attacked = False
        self.cast = False
        weight = config.HOME_THREAT_WEIGHT if job.kind == M.HOME else config.THREAT_WEIGHT
        field, stay, target = self._objective(job)
        self._threats(job, weight)
        if w.can_move:
            self._move(field, stay, target)
        self._act(job, target)
        self._ward()

    # ---- objective ------------------------------------------------------------------------
    def _objective(self, job):
        """(field to walk, value of standing where we are, the camp group to hit or None)."""
        w, p = self.w, self.paths
        field, stay, target = self._objective_field(job)
        if (field is not None and p.get(field, w.pos) >= UNREACHABLE and not w.at_spawn
                and not w.blocked.is_set(w.pos)):         # a camp tile (a capture) reads as a wall
            # Our own field cannot reach the target from here: never a candidate again for us.
            if job.kind == M.FARM and not job.group.tiles.is_set(w.pos):
                self.market.unreachable[job.group.index] = w.round
                self.market.job = None
            elif job.kind == M.BANK:
                self.market.bad_altars[(job.altar.x, job.altar.y)] = w.round
                self.market.job = None
        if job.kind == M.FARM and job.bodies > 1 and target is not None and self._hold(job):
            self.trace += " hold"
            return None, 0.0, None                    # wait for the party outside camp reach
        return field, stay, target

    def _hold(self, job):
        """The first of a party waits outside the group's reach until enough allies are near it,
        the group is already awake (someone is on it), or PARTY_HOLD_TURNS have passed."""
        w, g = self.w, job.group
        if w.round - job.since >= config.PARTY_HOLD_TURNS:
            return False
        d = min(max(abs(t.x - w.x), abs(t.y - w.y)) for t in g.tile_list)
        if d > g.reach + 1:
            return False                              # not there yet: keep walking
        if w.awake is not None:
            for camp, gi, cx, cy, hp in w.camps:
                if gi == g.index and w.awake.is_set(cambc.Vec2(cx, cy)):
                    return False
        near = 0
        for ax, ay, hp, dmg, ehp in w.allies:
            if max(abs(ax - g.cx), abs(ay - g.cy)) <= g.reach + 3:
                near += 1
        if not w.allies:
            b = w.board
            for k in range(min(w.n_bodies, len(b.cells))):
                c = b.cells[k]
                if (k != w.slot and c is not None and b.age(k, w.round) <= 3
                        and max(abs(c[0] - g.cx), abs(c[1] - g.cy)) <= g.reach + 4):
                    near += 1
        return near < job.bodies - 1

    def _objective_field(self, job):
        w, p = self.w, self.paths
        if job.kind == M.FARM:
            g = job.group
            if max(abs(g.cx - w.x), abs(g.cy - w.y)) <= 3:
                self.market.last_group = g
            if g.state == REWARD:
                if g.tiles.is_set(w.pos):
                    return None, CAPTURE_STAY, None
                return p.onto_group(g), 0.0, None
            return p.to_group(g), 0.0, g
        if job.kind == M.BANK:
            if self._altar_contested(job.altar):
                # Enemies hold the altar and the tribute would take more than a turn under their
                # hits (damage does not stop it, a death loses the bag): back off, and after
                # ALTAR_WAIT turns of this pick another altar.
                job.wait += 1
                if job.wait > config.ALTAR_WAIT:
                    self.market.bad_altars[(job.altar.x, job.altar.y)] = w.round - config.UNREACHABLE_FORGET + config.ALTAR_AVOID
                    self.market.job = None
                return w.spawn_field, 0.0, None
            return p.to_altar(job.altar), 0.0, None
        return w.spawn_field, 0.0, None

    def _altar_contested(self, altar):
        w = self.w
        if (w.gems + 9) // 10 <= 1:
            return False                              # a one-tick tribute is done before they act twice
        if max(abs(altar.x - w.x), abs(altar.y - w.y)) > 7:
            return False
        for e in w.enemies:
            if not e.on_spawn and max(abs(e.x - altar.x), abs(e.y - altar.y)) <= e.reach + 2:
                return w.power_balance() < config.FIGHT_BALANCE
        return False

    # ---- threat ---------------------------------------------------------------------------
    def _threats(self, job, weight):
        w = self.w
        self.cost = weight / max(1.0, w.ehp)
        soft_share = config.SOFT_SHARE
        sources = []
        for e in w.enemies[:config.MAX_SOURCES]:
            if e.on_spawn and e.dist > 1:
                continue
            reach = e.reach + 1
            sources.append((e.x, e.y, reach, reach + config.SOFT_RANGE, e.dmg, e.dmg * soft_share))
        if not w.enemies and w.board.sighting is not None:
            (x, y), count, seen = w.board.sighting
            age = w.round - seen
            if age <= config.SIGHTING_FRESH and max(abs(x - w.x), abs(y - w.y)) <= config.SIGHTING_RANGE:
                dmg = w.dmg * count * (1.0 - age / (config.SIGHTING_FRESH + 1))
                sources.append((x, y, 2 + age, 2 + age + config.SOFT_RANGE, dmg, dmg * soft_share))
        self.sources = sources
        allies = [(ax, ay) for ax, ay, hp, dmg, ehp in w.allies]
        if not allies:
            b = w.board
            for k in range(min(w.n_bodies, len(b.cells))):
                if k != w.slot and b.cells[k] is not None and b.age(k, w.round) <= 2 and b.claims[k][0] != 3:
                    allies.append(b.cells[k])
        self.ally_pts = allies
        camps = []
        if w.awake is not None and not w.awake.is_empty():
            skip = job.group.index if job.kind == M.FARM else -1
            others = allies + [(e.x, e.y) for e in w.enemies]
            for camp, gi, cx, cy, hp in w.camps:
                if gi == skip:
                    continue
                if not w.awake.is_set(cambc.Vec2(cx, cy)):
                    continue
                g = w.groups[gi]
                nearest = g.reach + 1
                for ox, oy in others:
                    d = max(abs(cx - ox), abs(cy - oy))
                    if d < nearest:
                        nearest = d
                camps.append((cx, cy, g.reach, nearest, g.strength / max(1, len(g.camps))))
            camps.sort(key=lambda c: max(abs(c[0] - w.x), abs(c[1] - w.y)))
            del camps[config.MAX_CAMPS:]
        self.camps = camps

    def _damage(self, x, y, sources, camps):
        total = 0.0
        for tx, ty, reach, soft, dmg, soft_dmg in sources:
            d = max(abs(tx - x), abs(ty - y))
            if d <= reach:
                total += dmg
            elif d <= soft:
                total += soft_dmg
        for cx, cy, reach, nearest, dmg in camps:
            d = max(abs(cx - x), abs(cy - y))
            if d <= reach:
                if d < nearest:
                    total += dmg
                elif d == nearest:
                    total += 0.5 * dmg
        return total

    # ---- where to stand ----------------------------------------------------------------------
    def _move(self, field, stay, target):
        w, p = self.w, self.paths
        if field is None:
            return                                   # standing on our capture
        here = p.get(field, w.pos)
        dash = self._dash() if w.can_cast else None
        span = 3 if dash is not None else 1
        # Only sources whose edge passes within reach of the candidates change the choice.
        sources = [s for s in self.sources
                   if s[2] - span <= max(abs(s[0] - w.x), abs(s[1] - w.y)) <= s[3] + span]
        camps = [c for c in self.camps
                 if not (max(abs(c[0] - w.x), abs(c[1] - w.y)) - span > c[2])]
        hits = self._hit_points(target)
        if not sources and not camps and not hits and stay == 0.0:
            self._descend(field, here, dash)
            return
        threatened = bool(sources or camps)
        allies = self.ally_pts if threatened else []
        cost = self.cost
        blocked = p.blocked()
        last = w.last_pos
        lx, ly = (last.x, last.y) if last is not None else (-1, -1)
        cands = [(t, False) for t in blocked.reachable_tiles(w.pos, 1).positions()]
        if dash is not None:
            cands += [(t, True) for t in (dash.ability.valid_tiles() & ~blocked).positions()]
        best, best_dash, best_score = None, False, None
        for tile, is_dash in cands:
            x, y = tile.x, tile.y
            here_tile = x == w.x and y == w.y
            v = here if here_tile else p.get(field, tile)
            if v >= UNREACHABLE and not here_tile:
                continue
            s = config.PROGRESS_WEIGHT * ((here - v) if here < UNREACHABLE else 0)
            if here_tile:
                s += stay
            s -= cost * self._damage(x, y, sources, camps)
            if allies:
                n = 0
                for ax, ay in allies:
                    if max(abs(ax - x), abs(ay - y)) <= config.ALLY_NEAR:
                        n += 1
                s += config.ALLY_WEIGHT * min(n, config.ALLY_CAP)
            for hx, hy, reach, value in hits:
                if max(abs(hx - x), abs(hy - y)) <= reach and (here_tile or not w.ranged):
                    s += value
                    break
            if is_dash:
                s -= 0.5                              # keep the dash unless it buys something
            if x == lx and y == ly:
                s -= config.BACKTRACK
            if best_score is None or s > best_score:
                best, best_dash, best_score = tile, is_dash, s
        if best is None or (best.x == w.x and best.y == w.y):
            return
        if best_dash:
            self._use_dash(dash, best)
        else:
            p.move(best)

    def _descend(self, field, here, dash):
        """No threat or target near: the field's own next step (a dash when it gains two)."""
        w, p = self.w, self.paths
        if here == 0 or here >= UNREACHABLE:
            return
        if dash is not None:
            blocked = p.blocked()
            best, best_v = None, here - 1          # a dash that gains two tiles (crosses a hole)
            for t in (dash.ability.valid_tiles() & ~blocked).positions():
                v = p.get(field, t)
                if v < best_v:
                    best, best_v = t, v
            if best is not None and self._use_dash(dash, best):
                return
        blocked = p.blocked()
        for d in field.next_moves_from(w.pos):
            t = w.pos + d
            if not blocked.is_set(t) and p.move(t):
                return
        # Every best step is taken: any free neighbour that still gets nearer.
        best, best_v = None, here
        for t in blocked.reachable_tiles(w.pos, 1).positions():
            v = p.get(field, t)
            if v < best_v:
                best, best_v = t, v
        if best is not None:
            p.move(best)

    def _dash(self):
        w = self.w
        for a in w.abilities:
            if a.dash and a.ready():
                return a
        return None

    def _use_dash(self, dash, tile):
        w = self.w
        try:
            if dash.ability.use(tile):
                w.can_cast = False
                self.cast = True
                if self.paths._blocked is not None:
                    self.paths._blocked.set(w.pos, False)
                    self.paths._blocked.set(tile, True)
                w.last_pos, w.pos = w.pos, tile
                w.x, w.y = tile.x, tile.y
                w.at_spawn = w.spawn_ally.is_set(tile)
                return True
        except Exception:
            pass
        return False

    def _hit_points(self, target):
        """(x, y, reach, value) of things worth being in reach of this turn."""
        w = self.w
        out = []
        if not w.can_attack:
            return out
        r = w.attack_range
        balance = w.power_balance() if w.enemies else 99.0
        for e in w.enemies:
            if e.on_spawn:
                continue
            if e.hp <= w.dmg * 1.2:
                out.append((e.x, e.y, r, ATTACK_KILL))
            elif balance >= config.FIGHT_BALANCE or e.hp < config.KILL_HEALTH * e.max_hp:
                out.append((e.x, e.y, r, ATTACK_ENEMY))
        if target is not None:
            for camp, gi, cx, cy, hp in w.camps:
                if gi == target.index:
                    out.append((cx, cy, r, ATTACK_JOB))
        return out

    # ---- what to do from here ----------------------------------------------------------------
    def _act(self, job, target):
        w = self.w
        if job.kind == M.HOME:
            self._home()
        elif job.kind == M.BANK:
            if (max(abs(job.altar.x - w.x), abs(job.altar.y - w.y)) <= w.submit_range and w.gems > 0
                    and not self._altar_contested(job.altar)):
                try:
                    w.me.submit_gems(job.altar)
                    self.trace += " tribute"
                    return
                except Exception:
                    pass
        self._casts(target)
        self._attack(target)
        if self.attacked and w.can_cast:
            for a in w.abilities:
                if a.reset and a.ready():
                    try:
                        if a.ability.use(None):
                            w.can_cast = False
                            w.can_attack = True
                            self._attack(target)
                    except Exception:
                        pass
                    break

    def _home(self):
        w = self.w
        if w.at_spawn:
            self._shop()
            return
        f = w.spawn_field
        turns = self.paths.get(f, w.pos) if f is not None else UNREACHABLE
        safe = w.damage_taken <= 0 and not any(e.dist <= 5 and not e.on_spawn for e in w.enemies)
        if turns > config.RECALL_MIN_TURNS and safe and not w.moved:
            try:
                w.me.recall()
                self.trace += " recall"
            except Exception:
                pass

    def _attack(self, target):
        w = self.w
        if not w.can_attack:
            return
        r = w.attack_range
        best, best_key = None, None
        balance = w.power_balance() if w.enemies else 99.0
        for e in w.enemies:
            if e.on_spawn or max(abs(e.x - w.x), abs(e.y - w.y)) > r:
                continue
            kill = e.hp <= w.dmg * 1.05
            fight = balance >= config.FIGHT_BALANCE or e.hp < config.KILL_HEALTH * e.max_hp or w.damage_taken > 0
            key = (3 if kill else 2 if fight else 1, -e.hp)
            if best_key is None or key > best_key:
                best, best_key = e.champion, key
        if best_key is None or best_key[0] < 2:
            # The job's camp before a champion we would rather not provoke.
            camp_best = None
            for camp, gi, cx, cy, hp in w.camps:
                if max(abs(cx - w.x), abs(cy - w.y)) > r:
                    continue
                awake = w.awake is not None and w.awake.is_set(cambc.Vec2(cx, cy))
                mine = target is not None and gi == target.index
                if not mine and not awake:
                    continue                          # never wake a camp we are not farming
                key = (1 if mine else 0, -hp)
                if camp_best is None or key > camp_best[0]:
                    camp_best = (key, camp)
            if camp_best is not None:
                best = camp_best[1]
        if best is None:
            return
        try:
            w.me.attack(best)
            w.can_attack = False
            self.attacked = True
        except Exception:
            pass

    def _casts(self, target):
        """Shield when threatened, heal a hurt ally, stun a chaser, nuke a weak enemy or the job's camp."""
        w = self.w
        if not w.can_cast:
            return
        threatened = w.damage_taken > 0 or any(e.dist <= e.reach + 1 and not e.on_spawn for e in w.enemies)
        for a in w.abilities:
            if not a.ready() or a.dash or a.reset:
                continue
            if a.exclusive and w.moved:
                continue
            try:
                if a.shield and threatened:
                    if a.ability.use(None):
                        return self._cast_done()
                elif a.heal:
                    best = None
                    for c in vision.champions(cambc.Team.ALLY):
                        if int(c) == w.handle:
                            continue
                        p = c.position()
                        if max(abs(p.x - w.x), abs(p.y - w.y)) <= a.range:
                            miss = c.max_health() - c.health()
                            if miss > a.mult * w.focus * 0.7 and (best is None or miss > best[0]):
                                best = (miss, c)
                    if best is not None and a.ability.use(best[1]):
                        return self._cast_done()
                elif a.enemies or a.ground:
                    tgt = self._spell_target(a, target, threatened)
                    if tgt is not None and a.ability.use(tgt):
                        return self._cast_done()
            except Exception:
                continue

    def _spell_target(self, a, target, threatened):
        w = self.w
        r = a.range
        balance = w.power_balance() if w.enemies else 99.0
        best = None
        for e in w.enemies:
            if e.on_spawn or max(abs(e.x - w.x), abs(e.y - w.y)) > r:
                continue
            if a.stun and not a.damage:
                if threatened and (best is None or e.dist < best[0]):
                    best = (e.dist, e)
            elif balance >= config.FIGHT_BALANCE or e.hp < config.KILL_HEALTH * e.max_hp or threatened:
                if best is None or e.hp < best[0]:
                    best = (e.hp, e)
        if best is not None:
            e = best[1]
            return e.pos if (a.ground and not a.enemies) else e.champion
        if a.camps and a.damage and target is not None:
            for camp, gi, cx, cy, hp in w.camps:
                if gi == target.index and max(abs(cx - w.x), abs(cy - w.y)) <= r and hp > w.dmg:
                    return cambc.Vec2(cx, cy) if (a.ground and not a.camps) else camp
        return None

    def _cast_done(self):
        self.w.can_cast = False
        self.cast = True
        return True

    def _ward(self):
        """A ward when the charge is back, every 20 rounds, five tiles from the last, off spawn
        (v15: band wins 5 -> 8)."""
        w = self.w
        if w.at_spawn or w.round - self.ward_round < 20:
            return
        if self.ward_pos is not None and max(abs(self.ward_pos[0] - w.x), abs(self.ward_pos[1] - w.y)) < 5:
            return
        try:
            if w.me.ward_cooldown() > 0:
                return
            if w.me.place_ward(w.pos):
                self.ward_round = w.round
                self.ward_pos = (w.x, w.y)
        except Exception:
            pass

    # ---- shop ------------------------------------------------------------------------------
    def _shop(self):
        shop(self.w)


def shop(w):
    """Buy the champion's build in order (kits.BUILDS): the first unfinished entry, as far as gold goes."""
    import kits
    order = kits.BUILDS.get(w.kind)
    if not order or w.gold < 30:
        return
    try:
        counts = w.me.item_count()
    except Exception:
        return
    want = {}
    for family, stat, shards in order:
        key = (family, stat)
        want[key] = want.get(key, 0) + shards
        shard = getattr(ItemType, "%s_SHARD_OF_%s" % (family, stat), None)
        emblem = getattr(ItemType, "%s_EMBLEM_OF_%s" % (family, stat), None)
        if shard is None:
            continue
        owned = counts[int(shard)] + (5 * counts[int(emblem)] if emblem is not None else 0)
        need = want[key] - owned
        if need <= 0:
            continue
        n = min(need, w.gold // shard.cost())
        if n <= 0:
            return
        try:
            if w.me.can_buy(shard, n):
                w.me.buy(shard, n)
                w.gold -= n * shard.cost()
        except Exception:
            pass
        return
