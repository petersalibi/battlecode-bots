"""Legal, sensible options for each decision. The scorers in decide.py only rank these.

A Turn object computes each candidate list lazily, so a decision that never asks about camps
never pays for the from-me distance field.
"""
import cambc
from cambc import vision

import config
import memory_codec
import world as W

Team = cambc.Team


class CampCandidate:
    __slots__ = ("group", "travel", "clear_turns", "damage", "wait", "claimed", "score", "bodies")

    def __init__(self, group, travel, clear_turns, damage, wait, claimed):
        self.group = group
        self.travel = travel
        self.clear_turns = clear_turns
        self.damage = damage
        self.wait = wait
        self.claimed = claimed
        self.score = 0.0
        self.bodies = 1 + claimed     # champions the plan assumes on the group, set by the scorer


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
            if slot != w.slot and group_index >= 0 and status in (memory_codec.STATUS_FARM, memory_codec.STATUS_CAPTURE):
                claimed_groups[group_index] = claimed_groups.get(group_index, 0) + 1
        reach = w.attack_range
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
            clear_turns = int(g.total_health / (w.dps * (1 + claimed))) + 1
            damage = g.total_strength * clear_turns / (1 + claimed)
            out.append(CampCandidate(g, travel, clear_turns, damage, wait, claimed))
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
                for g in fresh:
                    if g.anchor is None or g.avoid_until > w.round:
                        continue
                    travel = 0 if g.tiles.is_set(w.pos) else field.get(g.anchor) + 1
                    # An ally already on it will finish before we arrive; more bodies do not speed it
                    # up, and a one-tile region has no room for a second body anyway.
                    if travel <= 8 and (travel == 0 or g.index not in being_captured):
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
            if not locked:
                # The static field already gives the nearest altar's distance; find which one.
                turns = w.turns_to_altar
                if turns != W.UNREACHABLE:
                    altar = min(w.altars, key=lambda a: a.chebyshev_distance(w.pos))
                    best = (altar, turns)
            else:
                field = self.nav.from_me()
                best_turns = W.UNREACHABLE
                for altar in w.altars:
                    if (altar.x, altar.y) in locked:
                        continue
                    turns = min((field.get(t) for t in self._around(altar)), default=W.UNREACHABLE)
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
