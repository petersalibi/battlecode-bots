"""Scripted execution of the chosen mode: movement, attacks, abilities, channels and shopping.

Everything here is exact and cheap in the SDK's native helpers, so nothing is learned.
"""
import cambc

import config
import escape_features
import fuel
import nn
import weights
import fuel
import memory_codec
import world as W
from config import FARM, CAPTURE, TRIBUTE, RECALL, FIGHT, FLEE

Bitboard = cambc.Bitboard
ItemType = cambc.ItemType
Team = cambc.Team


class Actions:
    def __init__(self, world, nav, decider):
        self.w = world
        self.nav = nav
        self.d = decider
        self.status = memory_codec.STATUS_IDLE
        self.claim_group = -1

    def run(self, mode, turn):
        self.status = memory_codec.STATUS_IDLE
        self.claim_group = -1
        self.trace = ""
        self.acted = False          # attacked or cast this turn
        self.waiting = False        # standing still on purpose (capturing, holding for allies)
        self.squad_gave_up = False  # farm: the squad give-up retarget ran this turn (at most once)
        handler = (self.farm, self.capture, self.tribute, self.recall, self.fight, self.flee)[mode]
        handler(turn)

    # ---- helpers ---------------------------------------------------------------------
    def _ready(self, rec):
        if not self.w.can_cast or (rec.exclusive and self.w.moved):
            return False
        try:
            return rec.ability.cooldown() == 0
        except Exception:
            return False

    def _cast(self, rec, target):
        """Cast on a unit or tile. Returns True when it fired."""
        w = self.w
        try:
            if not rec.ability.use(target):
                return False
        except Exception:
            return False
        w.can_cast = False
        self.acted = True
        if rec.exclusive:
            w.cast_exclusive = True
        return True

    def _attack(self, unit, pos):
        w = self.w
        if not w.can_attack or w.pos.chebyshev_distance(pos) > w.attack_range:
            return False
        try:
            w.me.attack(unit)
        except Exception:
            return False
        w.can_attack = False
        self.acted = True
        return True

    def _attack_with_reset(self, unit, pos):
        """Basic attack, then an attack reset plus a second attack when one is ready."""
        if not self._attack(unit, pos):
            return False
        for rec in self.w.abilities:
            if rec.is_reset and self._ready(rec) and self._cast(rec, None):
                self.w.can_attack = True
                self._attack(unit, pos)
                break
        return True

    def _cast_offensive(self, unit, pos, against_camp):
        """Use damage, stun or area abilities on a target. Area spells land on the target's tile."""
        w = self.w
        for rec in sorted(w.abilities, key=lambda r: -r.modifier):
            if not (rec.is_damage or rec.is_aoe or rec.is_cc):
                continue
            unit_ok = rec.camps if against_camp else rec.enemies
            if not (unit_ok or rec.ground):
                continue
            if rec.range and w.pos.chebyshev_distance(pos) > rec.range:
                continue
            if not self._ready(rec):
                continue
            target = unit if unit_ok else pos
            if self._cast(rec, target):
                return True
        return False

    def _cast_shield(self):
        for rec in self.w.abilities:
            if rec.is_shield and self._ready(rec):
                return self._cast(rec, None)
        return False

    def _heal_ally(self):
        w = self.w
        for rec in w.abilities:
            if not rec.is_heal or not self._ready(rec):
                continue
            for champion in cambc.vision.champions(Team.ALLY):
                if int(champion) == w.handle:
                    continue
                try:
                    pos = champion.position()
                    if w.pos.chebyshev_distance(pos) <= rec.range and champion.health() < 0.6 * champion.max_health():
                        return self._cast(rec, champion)
                except Exception:
                    continue
        return False

    def _approach(self, g, radius):
        """Step towards the group. Returns True if we moved or dashed."""
        w = self.w
        field = self.approach_field(g, radius)
        if config.LIVE_APPROACH and field.get(w.pos) == W.UNREACHABLE and self._greedy_to_camp(g, radius):
            # Standing on a dead camp tile: the static field's walk-off and the live approach pull
            # opposite ways (yellow brick road, ranked: three bodies paced two tiles for 1,000+
            # rounds beside a living gem cluster). A greedy step towards a living camp in sight
            # decides first here; it costs eight distance checks, not a field (a live field on
            # every such turn exhausted ambush and big world: 100x100 maps).
            return True
        if self.nav.step_along(field):
            self.nav.try_dash(field)
            return True
        here = field.get(w.pos)
        if here > 1 and here != W.UNREACHABLE and self.nav.try_dash(field):
            return True
        if not config.LIVE_APPROACH:
            return False
        return self._live_approach(g, radius)

    def _greedy_to_camp(self, g, radius):
        """One step onto the free neighbour nearest a living camp of the group in sight, when that
        brings us closer than we stand (dead camp tiles are free; the static field calls them walls)."""
        w = self.w
        if not w.can_move or w.cast_exclusive:
            return False
        alive = [pos for camp, gi, pos, hp in w.visible_camps if gi == g.index and hp > 0]
        if not alive:
            return False
        here = min(w.pos.chebyshev_distance(p) for p in alive)
        if here <= radius:
            return False                                        # already in reach: nothing to do here
        best, best_d = None, here
        for tile in self.nav.blocked().reachable_tiles(w.pos, 1).positions():
            d = min(tile.chebyshev_distance(p) for p in alive)
            if d < best_d:
                best, best_d = tile, d
        if best is None:
            return False
        if self.nav._move(best):
            self.trace += " greedy"
            return True
        return False

    def _live_approach(self, g, radius):
        w = self.w
        # On the static ring, cut off from it, or walled in: the cached field treats every camp
        # tile as a wall, but dead camps are walkable and the living ones can sit inside a cluster
        # of dead ones (packs, gem fields: 72% of all ten-turn stalls) or three tiles away behind
        # a wall, out of sight (corridors, ranged bodies: 16%). Path over live terrain (terrain
        # plus the camps standing now) to the ring around the living camps in sight, or, with none
        # in sight, next to the group's tiles we cannot see yet: a neighbour is never out of sight.
        alive = [pos for camp, gi, pos, hp in w.visible_camps if gi == g.index and hp > 0]
        if alive:
            centres, r = alive, radius
        else:
            centres = [tile for tile in g.tile_list if not w.visible.is_set(tile)]
            r = 1
            if not centres:
                return False                        # every tile in sight and nothing alive: cleared
        goals = Bitboard()
        tmp = Bitboard()
        for pos in centres:
            tmp.clear()
            tmp.draw_chebyshev(pos, r)
            goals |= tmp
        goals &= ~(w.terrain_blocked | cambc.vision.camp_tiles())
        if goals.is_empty():
            return False
        dyn = self.nav.live_field(goals, config.LIVE_APPROACH_RANGE)
        if self.nav.step_along(dyn):
            self.trace += " live-approach"
            return True
        return False

    def approach_field(self, g, radius):
        """Cached field whose goals are the walkable tiles within radius of the group, minus its spawn tiles."""
        key = ("approach", g.index, radius)
        field = self.nav.fields.get(key)
        if field is None:
            goals = self.nav.ring(key, g.tile_list, radius) & ~g.tiles
            field = self.nav.field_to(key, goals)
        return field

    def _camps_in_range(self, group_index=None):
        w = self.w
        out = []
        for camp, gi, pos, hp in w.visible_camps:
            if group_index is not None and gi != group_index:
                continue
            if hp > 0 and w.pos.chebyshev_distance(pos) <= w.attack_range:
                out.append((camp, gi, pos, hp))
        out.sort(key=lambda c: c[3])
        return out

    # ---- farm ----------------------------------------------------------------------
    def farm(self, turn):
        w, d = self.w, self.d
        self.status = memory_codec.STATUS_FARM
        if d.last_source == "regroup" and d.regroup_pos is not None:
            # Opening: an enemy just came into sight and our nearest ally is a few tiles off. Step
            # to it (a local field, 12 tiles out); hit back if something is in reach.
            goals = Bitboard()
            goals.draw_chebyshev(d.regroup_pos, 1)
            goals &= ~w.static_blocked
            if not goals.is_empty():
                self.nav.step_along(self.nav.local_field(goals, 12))
            enemy = w.nearest_enemy
            if enemy is not None and enemy.dist <= w.attack_range:
                self._attack_with_reset(enemy.champion, enemy.pos)
            self.trace = "regroup"
            return
        if config.SQUADS and not w.is_leader and w.leader_claim is not None \
                and w.leader_claim[0] == memory_codec.STATUS_CAPTURE and 0 <= w.leader_claim[1] < len(w.groups):
            # The leader is capturing: wait beside the region (hitting anything in reach) rather than
            # walk off to a camp of our own; the leader's next claim takes the squad on together.
            cg = w.groups[w.leader_claim[1]]
            if cg.anchor is not None and w.pos.chebyshev_distance(cg.anchor) <= 2 * config.HELP_WAIT_RANGE \
                    and d.capture_wait < config.SQUAD_CAPTURE_WAIT:
                d.capture_wait += 1
                d.squad_stats["capture_wait"] = d.squad_stats.get("capture_wait", 0) + 1
                enemy = w.nearest_enemy
                if enemy is not None and enemy.dist <= w.attack_range:
                    self._attack_with_reset(enemy.champion, enemy.pos)
                self.waiting = True
                self.trace = "capture-wait"
                return
        else:
            d.capture_wait = 0
        g = d.target_group
        engaged = g is not None and bool(self._camps_in_range(g.index))
        # Stale when the group was cleared or captured since we chose it (a group chosen while
        # respawning stays valid), or periodically while we are still walking.
        stale = g is None or (g.state != d.target_state and g.state in (W.REWARD, W.RESPAWNING)) or (
            not engaged and w.round - d.target_round >= config.RETARGET_EVERY)
        if config.SQUADS and not stale and not engaged and not w.is_leader and w.leader_claim is not None:
            # The leader moved on to another group since our last retarget: re-rank once now.
            status, group = w.leader_claim
            if status in (memory_codec.STATUS_FARM, memory_codec.STATUS_IDLE) and group != d.leader_group_seen \
                    and (g is None or g.index != group):
                stale = True
        if stale and g is not None and d.retarget_peak and fuel.remaining() < d.retarget_peak + config.RETARGET_DEFER_MARGIN:
            # Not enough fuel left this turn for a re-rank as costly as the worst one so far
            # (the selector and the re-rank together ran big-world turns out of fuel): keep the
            # old target one more turn; next turn starts with a full budget.
            stale = False
            self.trace = "retarget deferred"
        if stale:
            before = fuel.remaining()
            best = d.choose_camp(turn, w)
            g = best.group if best is not None else None
            d.target_group = g
            d.target_state = g.state if g is not None else None
            d.target_bodies = best.bodies if best is not None else 1
            d.target_round = w.round
            cost = before - fuel.remaining()
            if cost > d.retarget_peak:
                d.retarget_peak = cost      # informational: the network's reserve covers acting via act_peak
            if config.PROFILE_EVERY and config.LOG_DECISIONS:
                print("r%d %s: retarget cost %d" % (w.round, w.kind_name, cost), flush=True)
        elif d.camp_review_round == w.round and not engaged:
            before = fuel.remaining()
            best = d.review_camp(turn, w)
            d.review_cost = before - fuel.remaining()
            if best is not None and (g is None or best.group.index != g.index):
                g = best.group
                d.target_group, d.target_state, d.target_bodies = g, g.state, best.bodies
        if g is None:
            # Nothing worth farming: bank gems if we have any, otherwise wait near our spawn.
            if w.gems > 0 and turn.altar() is not None:
                return self.tribute(turn)
            if not w.at_spawn:
                self.nav.step_along(w.spawn_field)
            return
        self.claim_group = g.index
        # Farming alone with a stronger enemy close: ask for a partner instead of leaving the camp
        # (the same call as a hold; a lone body that avoided contested camps handed them over).
        if (config.COOP and config.PAIR_CALL and w.enemies and w.nearest_enemy.dist <= config.CONTEST_RANGE
                and w.power_balance() < config.PAIR_CALL_BALANCE and w.allies_near(w.pos, config.HELP_WAIT_RANGE) == 0
                and g.anchor is not None and w.pos.chebyshev_distance(g.anchor) <= config.CONTEST_NEAR):
            self.status = memory_codec.STATUS_IDLE

        # An enemy in reach that is hitting us gets the basic attack; the camp still gets abilities.
        if w.damage_taken > 0 and w.hp_ratio > config.FLEE_HEALTH:
            enemy = w.nearest_enemy
            if enemy is not None and enemy.dist <= w.attack_range:
                self._attack_with_reset(enemy.champion, enemy.pos)

        # Already in range of a camp of this group: fight it without moving.
        targets = self._camps_in_range(g.index)
        if not targets and w.damage_taken > 0:
            targets = self._camps_in_range()                        # something awake is hitting us
        self.trace = "vis=%d grp=%d inrange=%d" % (
            len(w.visible_camps), sum(1 for c in w.visible_camps if c[1] == g.index), len(targets))
        # A camp we cannot clear alone: wait beside it, asleep, until enough allies have arrived.
        w.want_allies = d.target_bodies > 1
        if targets and w.want_allies and w.damage_taken <= 0:
            present = 1 + w.allies_near(g.tile_list[0], config.HELP_WAIT_RANGE)
            needed = d.target_bodies
            if config.SQUADS and len(w.squad) > 1:
                # The squad plan: wait for the mates that can come. With none available (dead,
                # recalling, banking) a soloable camp is farmed now and a bigger one given up.
                needed = min(needed, 1 + w.squad_available)
                if w.soloable(g):
                    needed = min(needed, 1 + config.SQUAD_WAIT_FOR)   # one mate beside us is the pair we want
                if needed <= 1 and not w.soloable(g) and not self.squad_gave_up:
                    # Once per turn, with the candidate list rebuilt so the avoided group is out of
                    # it (recursing on the cached list ran turns out of fuel in the first sweep).
                    self.squad_gave_up = True
                    g.avoid_until = w.round + config.HOLD_AVOID_ROUNDS
                    d.target_group = None
                    d.camp_cache = None
                    d.hold_since = -1
                    turn._camps = None
                    return self.farm(turn)
            if present < needed:
                if d.hold_since < 0:
                    d.hold_since = w.round
                elif (w.round - d.hold_since > config.HOLD_MAX_TURNS and not (config.COOP and w.ally_claims(g.index)))                         or (config.EARLY_HOLD_MAX and w.round <= config.EARLY_ROUNDS and w.round - d.hold_since > config.EARLY_HOLD_MAX):
                    # Nobody came (and nobody is on the way). Leave this group alone for a while.
                    g.avoid_until = w.round + config.HOLD_AVOID_ROUNDS
                    d.target_group = None
                    d.hold_since = -1
                    return self.farm(turn)
                elif w.round - d.hold_since > config.HOLD_MAX_TURNS * 2:
                    g.avoid_until = w.round + config.HOLD_AVOID_ROUNDS
                    d.target_group = None
                    d.hold_since = -1
                    return self.farm(turn)
                if config.COOP:
                    self.status = memory_codec.STATUS_IDLE          # the call for help on the claims board
                self.trace += " holding %d/%d" % (present, d.target_bodies)
                d.squad_stats["hold_turns"] = d.squad_stats.get("hold_turns", 0) + 1
                self.waiting = True
                return
        d.hold_since = -1
        if targets:
            camp, gi, pos, hp = targets[0]
            self._cast_offensive(camp, pos, against_camp=True)
            self._attack_with_reset(camp, pos)
            if w.damage_taken > 0:
                self._cast_shield()
            return

        if config.SQUADS and d.squad_hold_round == w.round:
            # The leader is retreating from this group and nothing is in sight yet: stand here.
            self.waiting = True
            self.trace += " squad-hold"
            return
        # Walk in. Melee stands next to a camp tile. Ranged stops at attack range, except at harmless
        # camps, where standing adjacent means the capture starts the turn the last camp dies.
        # Never stand on the spawn tiles themselves: an occupied spawn tile delays the respawn.
        self._approach(g, w.attack_range if (w.is_ranged and not g.harmless) else 1)
        if not w.is_ranged:
            targets = self._camps_in_range(g.index)
            if targets:
                camp, gi, pos, hp = targets[0]
                self._attack_with_reset(camp, pos)
                self._cast_offensive(camp, pos, against_camp=True)
        if not w.moved and not self.acted:
            # Could not step towards the group: on the camp clusters (concentrated wealth,
            # islands) another group's camps wall the target off, and bodies queued there idle
            # for ten turns at a time. Hit whatever camp is in range: it pays, and it opens the way.
            blockers = self._camps_in_range()
            if blockers:
                camp, gi, pos, hp = blockers[0]
                self.trace += " clearing g%d" % gi
                self._attack_with_reset(camp, pos)
                self._cast_offensive(camp, pos, against_camp=True)

    # ---- capture -------------------------------------------------------------------
    def capture(self, turn):
        w = self.w
        captures = turn.captures()
        if not captures:
            return self.farm(turn)
        g, travel = captures[0]
        self.status = memory_codec.STATUS_CAPTURE
        self.claim_group = g.index
        d = self.d
        if d.capture_group is not g:
            d.capture_group = g
            d.capture_since = w.round
            d.capture_on_since = -1
            d.capture_blind_logged = False
        if not g.tiles.is_set(w.pos):
            d.capture_on_since = -1
        if g.tiles.is_set(w.pos):
            # Standing on the region: stay, and hit anything hostile in reach. An enemy on the
            # region resets the timer every round, so a standoff never ends on its own (corridors
            # on the ladder: 1,800 rounds face to face, 0 gems): fight it when we are not weaker
            # and it is close, otherwise give the region up for a while after a few turns.
            self.waiting = True
            if d.capture_on_since < 0:
                d.capture_on_since = w.round
            enemy = self.d.choose_fight_target(turn, w)
            contested = any(g.tiles.is_set(e.pos) for e in w.enemies)
            if not contested and w.round - d.capture_on_since > config.CAPTURE_BLIND_TURNS:
                # No reward after three timers' worth of standing here and nobody in sight: an
                # enemy holds a tile of the region we cannot see. One body (the lowest slot
                # claiming the region) keeps standing, which denies them the reward for as long
                # as they deny us (yellow brick road's 600-gold rows: leaving both lost to v35
                # twice, holding them with two bodies won on the ladder); every other body here
                # leaves it alone for a while.
                holder = False
                if config.CAPTURE_BLIND_HOLD:
                    holder = True
                    for slot, (status, group) in enumerate(w.claims):
                        if slot != w.slot and slot < w.slot and status == memory_codec.STATUS_CAPTURE and group == g.index:
                            holder = False                      # a lower slot already holds it
                            break
                if not holder:
                    print("r%d %s: capture of group %d blind-contested after %d turns at %d,%d" % (
                        w.round, w.kind_name, g.index, w.round - d.capture_on_since, w.pos.x, w.pos.y), flush=True)
                    g.avoid_until = w.round + config.CAPTURE_STANDOFF_AVOID
                    d.capture_group = None
                    d.capture_on_since = -1
                    d.camp_cache = None
                    self.waiting = False
                    return self.farm(turn)
                if not d.capture_blind_logged:
                    d.capture_blind_logged = True
                    print("r%d %s: holding blind-contested group %d at %d,%d" % (
                        w.round, w.kind_name, g.index, w.pos.x, w.pos.y), flush=True)
            if contested and config.CAPTURE_PARTY:
                memory_codec.write_contested(g.index, w.round)       # the call for a party capture
            if enemy is not None and enemy.dist <= w.attack_range:
                self._cast_offensive(enemy.champion, enemy.pos, against_camp=False)
                self._attack_with_reset(enemy.champion, enemy.pos)
            elif contested and enemy is not None and enemy.dist <= config.FIGHT_RANGE                     and w.power_balance() >= config.CAPTURE_FIGHT_BALANCE and w.hp_ratio >= config.FIGHT_MIN_HEALTH:
                self.waiting = False
                return self.fight(turn)
            backed = config.CAPTURE_PARTY and w.allies_near(g.anchor, config.HELP_WAIT_RANGE) > 0
            standoff = config.CAPTURE_STANDOFF_TURNS * (2 if backed else 1)
            if contested and w.round - d.capture_since > standoff:
                g.avoid_until = w.round + config.CAPTURE_STANDOFF_AVOID
                d.capture_group = None
                d.camp_cache = None
                self.waiting = False
                return self.farm(turn)
            self.ward_here()
            return
        # Joining a party capture: hit an enemy standing on the region on the way in (a one-tile
        # region has room for one body, and the enemy on it is what keeps the timer at zero).
        if config.CAPTURE_PARTY and w.enemies:
            for e in w.enemies:
                if e.dist <= w.attack_range and g.tiles.is_set(e.pos):
                    self._cast_offensive(e.champion, e.pos, against_camp=False)
                    self._attack_with_reset(e.champion, e.pos)
                    break
        # Prefer a free tile of the region. Camps are dead, so only units can block.
        field = self.nav.field_to(("camp", g.index), g.tiles)
        self.nav.step_along(field)

    # ---- tribute -------------------------------------------------------------------
    def tribute(self, turn):
        w = self.w
        self.status = memory_codec.STATUS_BUSY
        altar = turn.altar()
        if altar is None or w.gems <= 0:
            return self.farm(turn)
        altar_pos, turns = altar
        if w.pos.chebyshev_distance(altar_pos) <= w.submit_range:
            try:
                w.me.submit_gems(altar_pos)
                return
            except Exception:
                pass
        key = ("altar", altar_pos.x, altar_pos.y)
        goals = self.nav.ring(key, [altar_pos], w.submit_range)
        field = self.nav.field_to(key, goals)
        self.nav.step_along(field)
        self.nav.try_dash(field)
        if w.pos.chebyshev_distance(altar_pos) <= w.submit_range and not w.cast_exclusive:
            try:
                w.me.submit_gems(altar_pos)
            except Exception:
                pass

    # ---- recall --------------------------------------------------------------------
    def recall(self, turn):
        w = self.w
        self.status = memory_codec.STATUS_BUSY
        if w.at_spawn:
            self.shop()
            if config.SPAWN_STRIKE and w.enemies:
                # Held on our spawn (it heals, and enemies cannot step on it): band games had 13% of
                # our contact turns here with an enemy within 3 and no attack at all. Hit what is in
                # reach before anything else; the step away below then only applies when nothing is.
                target = self.d.choose_fight_target(turn, w)
                if target is not None and target.dist <= w.attack_range:
                    self._cast_offensive(target.champion, target.pos, against_camp=False)
                    self._attack_with_reset(target.champion, target.pos)
                    return
            if w.enemies and config.SPAWN_GATE:
                tile = w.spawn_tile_away([e.pos for e in w.enemies])
                if tile is not None and not self.nav.blocked().is_set(tile):
                    self.nav._move(tile)
            return
        if w.turns_to_spawn <= config.RECALL_MIN_SPAWN_TURNS:
            self.nav.step_along(w.spawn_field)
            self.nav.try_dash(w.spawn_field)
            return
        if w.enemy_within(config.RECALL_SAFE_ENEMY_TURNS):
            # Too dangerous to channel here: put distance between us first.
            self.nav.step_away([e.pos for e in w.enemies], w.spawn_field)
            return
        try:
            w.me.recall()
        except Exception:
            self.nav.step_along(w.spawn_field)

    # ---- fight ---------------------------------------------------------------------
    def fight(self, turn):
        w = self.w
        target = self.d.choose_fight_target(turn, w)
        if target is None:
            return self.farm(turn)
        self.status = memory_codec.STATUS_FARM
        if config.SQUADS and self.d.target_group is not None:
            self.claim_group = self.d.target_group.index           # the squad's plan survives the fight
        in_range = target.dist <= w.attack_range
        if in_range:
            if config.KITE and w.is_ranged:
                self._kite(target)                                  # a dash does not spend the attack
            self._cast_offensive(target.champion, target.pos, against_camp=False)
            self._attack_with_reset(target.champion, target.pos)
            if w.damage_taken > 0:
                self._cast_shield()
            self._heal_ally()
            return
        if config.CAST_BEFORE_STEP:
            # A spell that reaches the target now beats a step towards it: Ryth's and Marcella's
            # range-4 spells exclude walking, and casting after the step never fired (band games:
            # they cast on 12% of contact turns, the band's on 21-23%). An exclusive cast stops the
            # step below; a compatible one still lets us walk.
            self._cast_offensive(target.champion, target.pos, against_camp=False)
        # Close the distance. Ranged champions stop at their attack range. The target moves, so this
        # is a small uncached field around it rather than a whole-map field per position.
        goals = Bitboard()
        if w.is_ranged:
            goals.draw_chebyshev(target.pos, w.attack_range)
            goals &= ~w.static_blocked
            goals.set(target.pos, False)
        else:
            goals.set(target.pos)
        if config.FIGHT_LIVE_FIELD:
            field = self.nav.live_field(goals, config.FIGHT_RANGE * 2 + 2)
        else:
            field = self.nav.local_field(goals, config.FIGHT_RANGE * 2 + 2)
        self.nav.step_along(field)
        self.nav.try_dash(field)
        if w.pos.chebyshev_distance(target.pos) <= w.attack_range:
            self._attack_with_reset(target.champion, target.pos)
            self._cast_offensive(target.champion, target.pos, against_camp=False)
        self._heal_ally()

    def _kite(self, target):
        """Ranged body with a melee enemy beside it: dash to a tile that keeps the target in attack
        range and puts KITE_MIN_GAP or more between us and every melee enemy, then attack as usual.
        The chaser spends its next turn closing instead of hitting. Returns True when it fired."""
        w = self.w
        if not w.can_cast or w.cast_exclusive:
            return False
        melee = []
        for e in w.enemies:
            if e.dist <= 1:
                try:
                    reach = e.champion.attack_range()
                except Exception:
                    reach = 1
                if reach <= 1:
                    melee.append(e.pos)
        if not melee:
            return False
        for rec in w.abilities:
            if not rec.is_dash or not self._ready(rec):
                continue
            try:
                landings = rec.ability.valid_tiles() & ~self.nav.blocked()
                best, best_key = None, None
                for tile in landings.positions():
                    if tile.chebyshev_distance(target.pos) > w.attack_range:
                        continue
                    gap = min(tile.chebyshev_distance(m) for m in melee)
                    if gap < config.KITE_MIN_GAP:
                        continue
                    key = (gap, 1 if w.bushes.is_set(tile) else 0, -tile.chebyshev_distance(target.pos))
                    if best_key is None or key > best_key:
                        best, best_key = tile, key
                if best is None:
                    return False
                if rec.ability.use(best):
                    w.can_cast = False
                    self.nav.occupy(w.pos)
                    w.pos = best
                    w.at_spawn = w.spawn_ally.is_set(best)
                    w.in_bush = w.bushes.is_set(best)
                    self.nav.blocked().set(best, True)
                    self.trace += " kite"
                    return True
            except Exception:
                return False
        return False

    # ---- flee ----------------------------------------------------------------------
    def flee(self, turn):
        w = self.w
        self.status = memory_codec.STATUS_BUSY
        self._cast_shield()
        if not w.enemies:
            # Fleeing a camp: get out of its reach first (camps never chase and sleep after two
            # quiet rounds), then recall once nothing is hitting us.
            camps = [pos for camp, gi, pos, hp in w.visible_camps
                     if hp > 0 and pos.chebyshev_distance(w.pos) <= w.groups[gi].attack_range + 1]
            if camps and w.damage_taken > 0:
                g = self.d.target_group
                if g is not None and self.d.last_source != "explore":
                    # Do not walk straight back into it after healing a little. (An explored flee
                    # in a self-play data run must not leave this 60-round mark behind.)
                    g.avoid_until = w.round + config.CAMP_FLEE_AVOID_ROUNDS
                    self.d.target_group = None
                    self.d.camp_cache = None
                if not self.nav.step_away(camps, w.spawn_field):
                    self.nav.step_along(w.spawn_field)
                self.nav.try_dash(w.spawn_field)
                return
            if w.hp_ratio < config.RECALL_HEALTH and w.damage_taken <= 0 and not w.at_spawn:
                try:
                    w.me.recall()
                    return
                except Exception:
                    pass
            self.nav.step_along(w.spawn_field)
            return
        threats = [e.pos for e in w.enemies]
        if config.SQUADS and self.d.target_group is not None:
            self.claim_group = self.d.target_group.index           # BUSY + group: the squad's retreat signal
        # A stun on the closest pursuer buys a turn.
        nearest = w.nearest_enemy
        for rec in w.abilities:
            if rec.is_cc and rec.enemies and nearest.dist <= rec.range and self._ready(rec) and not rec.exclusive:
                self._cast(rec, nearest.champion)
                break
        if config.PARTING_SHOT and not w.is_ranged and w.can_attack:
            # Melee may attack and then move in the same turn: hit the weakest adjacent enemy first,
            # then step away. The old strike-back fired after the step, when the chaser was already
            # two tiles off (band dumps: 245 hits in 4,455 such turns).
            shot, shot_hp = None, None
            for e in w.enemies:
                if e.dist <= 1 and (shot_hp is None or e.ehp < shot_hp):
                    shot, shot_hp = e, e.ehp
            if shot is not None and self._attack_with_reset(shot.champion, shot.pos):
                self.trace += " shot"
        dashed = config.FLEE_DASH_AWAY and self.nav.dash_away(threats, w.spawn_field)
        moved = self._escape_step(threats)
        if moved is None:
            moved = self.nav.step_away(threats, w.spawn_field)
        if dashed or self.nav.try_dash(w.spawn_field):
            return
        if not moved and nearest.dist <= w.attack_range:
            self._attack_with_reset(nearest.champion, nearest.pos)
        elif moved and config.FLEE_STRIKE_BACK and not w.is_ranged:
            # Melee walks and attacks in the same turn: hit a chaser still in reach after the step.
            pos = w.me.position()
            for e in w.enemies:
                if e.pos.chebyshev_distance(pos) <= w.attack_range:
                    self._attack_with_reset(e.champion, e.pos)
                    break

    def _escape_step(self, threats):
        """The escape head's step: the candidate tile (stand still or a free neighbour) with the
        highest survival score, when the head is on, affordable and sure. Returns True/False for
        moved, or None when the script should decide."""
        w, d = self.w, self.d
        net = weights.ESCAPE_HEAD if config.USE_NN_ESCAPE else None
        if net is None or not w.can_move or w.cast_exclusive or w.round <= config.NN_SKIP_ROUNDS:
            return None
        if not fuel.can_afford(config.ESCAPE_FUEL, d.camp_reserve(w)):
            return None
        try:
            blocked = self.nav.blocked()
            cands = [(tile, stay) for tile, stay in escape_features.candidates(w) if stay or not blocked.is_set(tile)]
            if len(cands) < 2:
                return None
            if len(cands) > config.ESCAPE_TOP + 1:
                # Standing still plus the neighbours furthest from the nearest threat.
                stay = cands[0]
                steps = sorted(cands[1:], key=lambda c: -min(c[0].chebyshev_distance(t) for t in threats))
                cands = [stay] + steps[:config.ESCAPE_TOP]
            state, rows = escape_features.build(w, d, cands)
            scores = net.score(state, rows)
            ok = [True] * len(cands)
            if nn.margin(scores, ok) < getattr(weights, "ESCAPE_HEAD_MARGIN", 0):
                return None
            pick = nn.masked_argmax(scores, ok)
            if pick is None:
                return None
            d.escape_source = "nn"
            tile, stay = cands[pick]
            if stay:
                return False
            return self.nav._move(tile)
        except Exception:
            return None

    # ---- shop and ward -------------------------------------------------------------
    def shop(self):
        """Spend gold on shards of our build, in order. Works on spawn and while dead."""
        w = self.w
        if w.gold < config.SHARD_COST:
            return
        if config.USE_BUILD_ORDER and w.kind_name in config.BUILD_ORDER:
            return self._shop_order(config.BUILD_ORDER[w.kind_name])
        me = w.me
        stat = self._build_stat()
        try:
            counts = me.item_count()
        except Exception:
            return
        bought = False
        remaining_families = 0
        for family in config.SHOP_FAMILIES:
            shard = getattr(ItemType, f"{family}_SHARD_OF_{stat}", None)
            emblem = getattr(ItemType, f"{family}_EMBLEM_OF_{stat}", None)
            if shard is None:
                continue
            if emblem is not None and counts[int(emblem)] > 0:
                continue                                              # this family is complete
            remaining_families += 1
            have = counts[int(shard)]
            need = max(0, shard.combine_count() - have) if emblem is not None else 1
            if need <= 0:
                continue
            affordable = w.gold // shard.cost()
            count = min(need, affordable)
            if count <= 0:
                break
            try:
                if me.can_buy(shard, count):
                    me.buy(shard, count)
                    w.gold -= count * shard.cost()
                    bought = True
            except Exception:
                break
            if w.gold < shard.cost():
                break
        # Nothing left to buy: stop recalling home to shop.
        w.shop_exhausted = remaining_families == 0
        if bought:
            self._after_shopping()

    def _shop_order(self, order):
        """Walk the build in order: each entry wants its shards on top of the same family and stat
        bought by earlier entries (a shard pair already combined counts five per emblem). Buys
        what is affordable for the first unfinished entry and stops there, so the order holds."""
        w = self.w
        me = w.me
        try:
            counts = me.item_count()
        except Exception:
            return
        want = {}
        bought = False
        for family, stat, shards in order:
            key = (family, stat)
            want[key] = want.get(key, 0) + shards
            shard = getattr(ItemType, f"{family}_SHARD_OF_{stat}", None)
            if shard is None:
                continue
            emblem = getattr(ItemType, f"{family}_EMBLEM_OF_{stat}", None)
            owned = counts[int(shard)] + (5 * counts[int(emblem)] if emblem is not None else 0)
            need = want[key] - owned
            if need <= 0:
                continue
            count = min(need, w.gold // shard.cost())
            if count <= 0:
                break
            try:
                if me.can_buy(shard, count):
                    me.buy(shard, count)
                    w.gold -= count * shard.cost()
                    bought = True
            except Exception:
                pass
            break
        w.shop_exhausted = False
        if bought:
            self._after_shopping()

    def _after_shopping(self):
        w = self.w
        me = w.me
        w.events.add("stats_dirty")
        try:
            w.max_hp = me.max_health()
            w.hp = me.health()
            w.hp_ratio = w.hp / w.max_hp if w.max_hp else 0.0
            w.strength = me.strength()
            w.focus = me.focus()
            w.dps = w._estimate_dps()
        except Exception:
            pass

    def _build_stat(self):
        stats = self.w.plan.get("stats") or []
        code = stats[self.w.slot] if self.w.slot < len(stats) else None
        if code is not None and 0 <= code <= 2:
            return ("STRENGTH", "FOCUS", "HEALTH")[code]
        return config.BUILD_STAT.get(self.w.kind_name, "STRENGTH")

    def ward_often(self):
        """Vision: every other team places about 42 wards in the first 300 rounds, we placed 6.5
        (only while capturing). Drop one wherever we are once the charge is back and we have
        moved WARD_OFTEN_SPACING tiles from the last one, outside our spawn."""
        w = self.w
        if w.at_spawn or w.round - w.ward_round < config.WARD_OFTEN_EVERY:
            return False
        try:
            pos = w.me.position()
            if w.ward_pos is not None and pos.chebyshev_distance(w.ward_pos) < config.WARD_OFTEN_SPACING:
                return False
            if w.me.ward_cooldown() > 0:
                return False
            if w.me.place_ward(pos):
                w.ward_round = w.round
                w.ward_pos = pos
                return True
        except Exception:
            pass
        return False

    def ward_here(self):
        """Drop our ward where we stand, at most every WARD_REPLACE_EVERY rounds."""
        w = self.w
        if w.round - w.ward_round < config.WARD_REPLACE_EVERY:
            return False
        try:
            if w.me.ward_cooldown() > 0:
                return False
            if w.me.place_ward(w.pos):
                w.ward_round = w.round
                w.ward_pos = w.pos
                return True
        except Exception:
            pass
        return False
