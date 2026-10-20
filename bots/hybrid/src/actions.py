"""Scripted execution of the chosen mode: movement, attacks, abilities, channels and shopping.

Everything here is exact and cheap in the SDK's native helpers, so nothing is learned.
"""
import cambc

import config
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
        if self.nav.step_along(field):
            self.nav.try_dash(field)
            return True
        here = field.get(w.pos)
        if here > 1 and here != W.UNREACHABLE:
            return self.nav.try_dash(field)
        # On the static ring, or cut off from it, with nothing in range: some of the group's camps are
        # dead, so path to the ones still standing (a dynamic field, computed only in this case).
        alive = [pos for camp, gi, pos, hp in w.visible_camps if gi == g.index and hp > 0]
        if not alive:
            return False
        goals = Bitboard()
        tmp = Bitboard()
        for pos in alive:
            tmp.clear()
            tmp.draw_chebyshev(pos, radius)
            goals |= tmp
        goals &= ~w.static_blocked
        for pos in alive:
            goals.set(pos, False)
        if goals.is_empty():
            return False
        origin = Bitboard()
        origin.set(w.pos)
        dyn = cambc.DistanceField()
        dyn.compute_to(w.static_blocked, goals, origin, Bitboard())
        return self.nav.step_along(dyn)

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
        g = d.target_group
        engaged = g is not None and bool(self._camps_in_range(g.index))
        # Stale when the group was cleared or captured since we chose it (a group chosen while
        # respawning stays valid), or periodically while we are still walking.
        stale = g is None or (g.state != d.target_state and g.state in (W.REWARD, W.RESPAWNING)) or (
            not engaged and w.round - d.target_round >= config.RETARGET_EVERY)
        if stale:
            before = fuel.remaining() if config.PROFILE_EVERY else 0
            best = d.choose_camp(turn, w)
            g = best.group if best is not None else None
            d.target_group = g
            d.target_state = g.state if g is not None else None
            d.target_bodies = best.bodies if best is not None else 1
            d.target_round = w.round
            if config.PROFILE_EVERY and config.LOG_DECISIONS:
                print("r%d %s: retarget cost %d" % (w.round, w.kind_name, before - fuel.remaining()), flush=True)
        if g is None:
            # Nothing worth farming: bank gems if we have any, otherwise wait near our spawn.
            if w.gems > 0 and turn.altar() is not None:
                return self.tribute(turn)
            if not w.at_spawn:
                self.nav.step_along(w.spawn_field)
            return
        self.claim_group = g.index

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
            if present < d.target_bodies:
                if d.hold_since < 0:
                    d.hold_since = w.round
                elif w.round - d.hold_since > config.HOLD_MAX_TURNS:
                    # Nobody came. Leave this group alone for a while and pick something else.
                    g.avoid_until = w.round + config.HOLD_AVOID_ROUNDS
                    d.target_group = None
                    d.hold_since = -1
                    return self.farm(turn)
                self.trace += " holding %d/%d" % (present, d.target_bodies)
                return
        d.hold_since = -1
        if targets:
            camp, gi, pos, hp = targets[0]
            self._cast_offensive(camp, pos, against_camp=True)
            self._attack_with_reset(camp, pos)
            if w.damage_taken > 0:
                self._cast_shield()
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

    # ---- capture -------------------------------------------------------------------
    def capture(self, turn):
        w = self.w
        captures = turn.captures()
        if not captures:
            return self.farm(turn)
        g, travel = captures[0]
        self.status = memory_codec.STATUS_CAPTURE
        self.claim_group = g.index
        self.d.capture_group = g
        if g.tiles.is_set(w.pos):
            # Standing on the region: stay, and hit anything hostile in reach.
            enemy = self.d.choose_fight_target(turn, w)
            if enemy is not None and enemy.dist <= w.attack_range:
                self._cast_offensive(enemy.champion, enemy.pos, against_camp=False)
                self._attack_with_reset(enemy.champion, enemy.pos)
            self.ward_here()
            return
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
        in_range = target.dist <= w.attack_range
        if in_range:
            self._cast_offensive(target.champion, target.pos, against_camp=False)
            self._attack_with_reset(target.champion, target.pos)
            if w.damage_taken > 0:
                self._cast_shield()
            self._heal_ally()
            return
        # Close the distance. Ranged champions stop at their attack range.
        if w.is_ranged:
            goals = self.nav.ring(("enemy", target.handle, target.pos.x, target.pos.y), [target.pos], w.attack_range)
            field = self.nav.field_to(("enemy", target.handle, target.pos.x, target.pos.y), goals)
        else:
            goals = Bitboard()
            goals.set(target.pos)
            field = self.nav.field_to(("enemy", target.handle, target.pos.x, target.pos.y), goals)
        self.nav.step_along(field)
        self.nav.try_dash(field)
        if w.pos.chebyshev_distance(target.pos) <= w.attack_range:
            self._attack_with_reset(target.champion, target.pos)
            self._cast_offensive(target.champion, target.pos, against_camp=False)
        self._heal_ally()

    # ---- flee ----------------------------------------------------------------------
    def flee(self, turn):
        w = self.w
        self.status = memory_codec.STATUS_BUSY
        self._cast_shield()
        if not w.enemies:
            if w.hp_ratio < config.RECALL_HEALTH and w.damage_taken <= 0 and not w.at_spawn:
                try:
                    w.me.recall()
                    return
                except Exception:
                    pass
            self.nav.step_along(w.spawn_field)
            return
        threats = [e.pos for e in w.enemies]
        # A stun on the closest pursuer buys a turn.
        nearest = w.nearest_enemy
        for rec in w.abilities:
            if rec.is_cc and rec.enemies and nearest.dist <= rec.range and self._ready(rec) and not rec.exclusive:
                self._cast(rec, nearest.champion)
                break
        moved = self.nav.step_away(threats, w.spawn_field)
        if self.nav.try_dash(w.spawn_field):
            return
        if not moved and nearest.dist <= w.attack_range:
            self._attack_with_reset(nearest.champion, nearest.pos)

    # ---- shop and ward -------------------------------------------------------------
    def shop(self):
        """Spend gold on shards of our build stat, family by family. Works on spawn and while dead."""
        w = self.w
        if w.gold < config.SHARD_COST:
            return
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
                return True
        except Exception:
            pass
        return False
