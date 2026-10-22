"""Legal, sensible options for each decision. The scorers in decide.py only rank these.

A Turn object computes each candidate list lazily, so a decision that never asks about camps
never pays for the from-me distance field.
"""
import cambc
from cambc import vision

import config
import fuel
import memory_codec
import world as W

Team = cambc.Team


class CampCandidate:
    __slots__ = ("group", "travel", "clear_turns", "damage", "wait", "claimed", "score", "bodies", "helping", "squad_bodies")

    def __init__(self, group, travel, clear_turns, damage, wait, claimed, squad_bodies=1):
        self.group = group
        self.travel = travel
        self.clear_turns = clear_turns
        self.damage = damage
        self.wait = wait
        self.claimed = claimed
        self.score = 0.0
        self.squad_bodies = squad_bodies   # bodies the squad brings (the leader plans for its squad)
        self.bodies = max(1 + claimed, squad_bodies)   # champions the plan assumes on the group, set by the scorer
        self.helping = False          # set by the scorer when this is an answer to an ally's call


RECOMPUTE_EVENTS = {"camp_cleared", "capture_finished", "gems_gained", "gems_lost", "enemy_appeared", "big_damage"}


class Turn:
    def __init__(self, world, nav, decider):
        self.w = world
        self.nav = nav
        self.d = decider
        self._camps = None
        self._captures = None
        self._altar = None
        self._fight = None
        self.fresh_camps = False

    # ---- camps ---------------------------------------------------------------------
    def camps(self, fresh=False):
        """Camp candidates. Reused from recent turns unless fresh is requested or something happened."""
        if self._camps is None or (fresh and not self.fresh_camps):
            d, w = self.d, self.w
            cached = d.camp_cache
            if (not fresh and cached is not None and w.round - d.camp_cache_round < config.CAMP_CACHE_TURNS
                    and not (w.events & RECOMPUTE_EVENTS)):
                self._camps = cached
            else:
                self._camps = self._camp_candidates()
                self.fresh_camps = True
                d.camp_cache = self._camps
                d.camp_cache_round = w.round
        return self._camps

    def _camp_candidates(self):
        w = self.w
        round_number = w.round
        # Tiles beyond MAX_CAMP_TURNS stay unreached, which makes the per-group test one field read.
        field = self.nav.from_me(config.MAX_CAMP_TURNS + w.attack_range)
        get = field.get
        claimed_groups = {}
        for slot, (status, group_index) in enumerate(w.claims):
            if slot != w.slot and group_index >= 0 and status in (memory_codec.STATUS_FARM, memory_codec.STATUS_CAPTURE, memory_codec.STATUS_IDLE):
                claimed_groups[group_index] = claimed_groups.get(group_index, 0) + 1
        reach = w.attack_range
        # The squad leader plans for its squad: the clear is shared, so is the monsters' attention.
        squad_n = 1 + w.squad_available if config.SQUADS and w.is_leader else 1
        recent_reward = round_number - 30
        latest_ok = round_number + config.MAX_CAMP_TURNS + config.RESPAWN_WAIT_TURNS
        # Pass one: one field read per group, keep the nearest few. Pass two does the arithmetic.
        nearest = []
        for g in w.groups:
            state = g.state
            if not g.reachable or g.anchor is None or g.avoid_until > round_number:
                continue
            if state == W.REWARD and g.seen_round >= recent_reward:
                continue                                      # already cleared, capture instead
            if state == W.RESPAWNING and g.respawn_round > latest_ok:
                continue                                      # back too late to matter
            travel = get(g.anchor)                            # camp tiles are walls in the field
            if travel != W.UNREACHABLE:
                nearest.append((travel, g.index))
        nearest.sort()
        out = []
        for travel, index in nearest[:config.MAX_CAMP_CANDIDATES + 4]:
            g = w.groups[index]
            travel = max(0, travel - (reach - 1))             # the anchor is already adjacent
            wait = g.respawn_round - round_number - travel if g.state == W.RESPAWNING else 0
            if wait > config.RESPAWN_WAIT_TURNS:
                continue
            if wait < 0:
                wait = 0
            claimed = claimed_groups.get(g.index, 0)
            # Allies already on the group share the work and the monsters' attention.
            bodies = max(1 + claimed, squad_n)
            clear_turns = int(g.health_left / (w.dps * bodies)) + 1
            damage = g.total_strength * clear_turns / bodies
            out.append(CampCandidate(g, travel, clear_turns, damage, wait, claimed, squad_n))
            if len(out) >= config.MAX_CAMP_CANDIDATES:
                break
        return out

    # ---- captures ------------------------------------------------------------------
    def captures(self):
        """Cleared groups whose reward can be captured, nearest first (as (group, travel) pairs)."""
        if self._captures is None:
            w = self.w
            out = []
            fresh = [g for g in w.groups
                     if g.state == W.REWARD and g.seen_round >= w.round - config.CAPTURE_MEMORY_ROUNDS]
            if fresh:
                being_captured = {group_index for slot, (status, group_index) in enumerate(w.claims)
                                  if slot != w.slot and status == memory_codec.STATUS_CAPTURE}
                field = self.nav.from_me(config.MAX_CAMP_TURNS)
                # A capture an ally is holding against an enemy on the region (byte 6, fresh):
                # joining it is the only way the timer ever runs out (corridors: won fights, no gems).
                party = None
                if config.CAPTURE_PARTY and w.contested is not None and w.contested[1] <= config.CAPTURE_PARTY_FRESH:
                    party = w.contested[0]
                for g in fresh:
                    if g.anchor is None or g.avoid_until > w.round:
                        continue
                    travel = 0 if g.tiles.is_set(w.pos) else field.get(g.anchor) + 1
                    # An ally already on it will finish before we arrive; more bodies do not speed it
                    # up, and a one-tile region has no room for a second body anyway, unless the
                    # ally is held up by an enemy on the region.
                    if g.index == party and g.index in being_captured:
                        if travel <= config.CAPTURE_SUPPORT_TRAVEL:
                            out.append((g, travel))
                    elif travel <= 8 and (travel == 0 or g.index not in being_captured):
                        out.append((g, travel))
                out.sort(key=lambda item: item[1])
            self._captures = out
        return self._captures

    # ---- altars --------------------------------------------------------------------
    def altar(self):
        """(altar position, walking turns) for the nearest altar without an enemy lock, or None."""
        if self._altar is None:
            w = self.w
            locked = set()
            try:
                for lock in vision.altar_locks(Team.ENEMY):
                    locked.add((lock.position().x, lock.position().y))
            except Exception:
                pass
            best = None
            if not locked and w.turns_to_altar == W.UNREACHABLE:
                pass                                          # no altar can be walked to from here
            elif not locked and len(w.altars) == 1:
                best = (w.altars[0], w.turns_to_altar)
            else:
                # Nearest altar by walking distance, never by straight-line distance: on islands the
                # closest altar as the crow flies can be on another island, and a body sent there
                # stands still for the rest of the game. One cached field per altar, one read each.
                # A field costs up to ~750K to build, so at most what fuel allows per turn; an altar
                # whose field is not built yet is simply not offered this turn.
                best_turns = W.UNREACHABLE
                field_cost = config.FIELD_COST_PER_TILE * w.width * w.height
                built = False
                for altar in w.altars:
                    if (altar.x, altar.y) in locked:
                        continue
                    key = ("altar", altar.x, altar.y)
                    field = self.nav.fields.get(key)
                    if field is None:
                        # One new field per turn at most (lanes has six altars), and only with
                        # enough left for the rest of the turn.
                        if built or not fuel.can_afford(field_cost, config.FIELD_BUILD_RESERVE):
                            continue
                        field = self.nav.field_to(key, self.nav.ring(key, [altar], w.submit_range))
                        built = True
                    turns = field.get(w.pos)
                    if turns < best_turns:
                        best, best_turns = (altar, turns), turns
            self._altar = best if best is not None else False
        return self._altar or None

    def _around(self, altar):
        r = self.w.submit_range
        tiles = []
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                tile = cambc.Vec2(altar.x + dx, altar.y + dy)
                if 0 <= tile.x < self.w.width and 0 <= tile.y < self.w.height and not self.w.static_blocked.is_set(tile):
                    tiles.append(tile)
        return tiles

    # ---- fights --------------------------------------------------------------------
    def fight_targets(self):
        if self._fight is None:
            self._fight = [e for e in self.w.enemies if e.dist <= config.FIGHT_RANGE]
        return self._fight
