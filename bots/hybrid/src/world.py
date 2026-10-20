"""Cached world model, one instance per champion body.

Static facts (map, camp groups, distance fields to altars and spawn) are built on the first turn,
which has double fuel. refresh() updates the per-turn view with as few engine calls as possible,
and every remembered fact carries the round it was seen. Decisions and features read plain
attributes from here rather than calling the SDK.
"""
import _cambc
import cambc
from cambc import game_map, vision

import config
import memory_codec

_fuel = _cambc.fuel_remaining

Team = cambc.Team
Bitboard = cambc.Bitboard
DistanceField = cambc.DistanceField
ChampionState = cambc.ChampionState

UNREACHABLE = 65535
# Camp group state codes.
UNKNOWN, ALIVE, REWARD, RESPAWNING = 0, 1, 2, 3
ABILITY_COOLDOWN = 8
NEAR_GROUP_RANGE = 9      # only groups this close (Chebyshev) can have a spawn tile in vision
ALLY_REFRESH_EVERY = 4    # rounds between ally scans when no enemy is in sight
_camp_stats = {}          # camp kind -> CampStats, filled on the first turn


class Group:
    __slots__ = ("index", "group", "handle", "gems", "gold", "respawn", "tiles", "tile_list", "camp_handles",
                 "n_camps", "total_health", "total_strength", "harmless", "state", "timer", "seen_round",
                 "respawn_round", "value", "near", "kinds", "reachable", "avoid_until")

    def __init__(self, index, group):
        self.index = index
        self.group = group
        self.handle = int(group)
        self.gems = group.gem_value()
        self.gold = group.gold_value()
        self.respawn = group.max_respawn_timer()
        self.tiles = group.spawn_tiles()
        self.tile_list = self.tiles.positions()
        camps = group.camps()
        self.n_camps = len(camps)
        self.camp_handles = [int(c) for c in camps]
        self.total_health = 0.0
        self.total_strength = 0.0
        self.kinds = []
        for camp in camps:
            kind = camp._type                    # Camp.kind() builds an enum each call; the raw int is free
            stats = _camp_stats.get(kind)
            if stats is None:
                stats = _camp_stats[kind] = camp.base_stats()
            self.total_health += stats.base_health
            self.total_strength += stats.base_strength
            self.kinds.append(kind)
        self.harmless = self.total_strength == 0
        self.near = None          # built by World.finish_init on the second turn
        self.reachable = True
        self.avoid_until = -1     # round until which this body ignores the group (gave up waiting)
        self.state = UNKNOWN
        self.timer = 0
        self.seen_round = -1
        self.respawn_round = -1        # round the group is expected back, when known
        self.value = self.gems * config.GEM_VALUE + self.gold * config.GOLD_VALUE

    def build_near(self):
        """Tiles from which one of our spawn tiles could be in vision: one draw around the first tile,
        widened by the group's extent (groups are a few cardinally connected tiles)."""
        first = self.tile_list[0]
        extent = 0
        for tile in self.tile_list:
            d = first.chebyshev_distance(tile)
            if d > extent:
                extent = d
        self.near = Bitboard()
        self.near.draw_chebyshev(first, NEAR_GROUP_RANGE + extent)

    def available_at(self, round_number):
        """Best estimate of the round the group's camps are alive again (<= round_number means now)."""
        if self.state == RESPAWNING or self.respawn_round > round_number:
            return self.respawn_round
        return round_number


class Enemy:
    __slots__ = ("champion", "handle", "pos", "hp", "max_hp", "strength", "gems", "dist")

    def __init__(self, champion, my_pos):
        self.champion = champion
        self.handle = int(champion)
        self.pos = champion.position()
        self.hp = champion.health()
        self.max_hp = champion.max_health()
        self.strength = champion.strength()
        self.dist = my_pos.chebyshev_distance(self.pos)
        try:
            self.gems = champion.gem_count()
        except Exception:
            self.gems = 0


class AbilityRec:
    __slots__ = ("slot", "ability", "info", "range", "modifier", "exclusive", "camps", "enemies", "allies",
                 "ground", "is_reset", "is_dash", "is_shield", "is_heal", "is_cc", "is_damage", "is_aoe")

    def __init__(self, slot, ability):
        self.slot = slot
        self.ability = ability
        info = ability.info()
        self.info = info
        self.range = int(getattr(info, "range", 0) or 0)
        self.modifier = float(getattr(info, "modifier", 0.0) or 0.0)
        self.exclusive = bool(getattr(info, "exclusive_with_movement", False))
        self.camps = ability.can_target_camps()
        self.enemies = ability.can_target_enemies()
        self.allies = ability.can_target_allies()
        self.ground = ability.can_target_ground()
        self.is_reset = ability.is_attack_reset()
        self.is_dash = ability.is_dash()
        self.is_shield = ability.is_shield()
        self.is_heal = ability.is_heal()
        self.is_cc = ability.is_cc()
        self.is_damage = ability.is_damage()
        self.is_aoe = ability.is_aoe_damage()


class World:
    def __init__(self):
        self.ready = False
        self.round = 0
        self.prev_hp = None
        self.enemy_memory = {}         # handle -> (pos, round, hp_ratio)
        self.last_enemy_round = -10_000
        self.last_enemy_pos = None
        self.enemies_killed = []       # rounds at which an enemy was seen to die
        self.events = set()
        self.prev_gems = 0
        self.ward_round = -10_000
        self.moved = False
        self.cast_exclusive = False
        self._banked = (0, 0)
        self._banked_round = -1
        self.want_allies = False       # set by actions when waiting for help at a camp
        self.shop_exhausted = False    # every shard family complete; gold has nothing to buy
        self.refresh_profile = None

    # ---- first turn --------------------------------------------------------------
    def init_static(self):
        marks = [_cambc.fuel_remaining()]
        me = cambc.me()
        self.me = me
        self.handle = int(me)
        self.width = game_map.width()
        self.height = game_map.height()
        self.walls = game_map.walls()
        self.holes = game_map.holes()
        self.bushes = game_map.bushes()
        self.altar_tiles = game_map.altars()
        self.spawn_ally = game_map.spawn_region(Team.ALLY)
        self.spawn_enemy = game_map.spawn_region(Team.ENEMY)
        self.static_blocked = self.walls | self.holes | self.altar_tiles | self.spawn_enemy
        self.altars = self.altar_tiles.positions()
        self.submit_range = cambc.constants.SUBMIT_GEMS_RANGE
        self.recall_time = cambc.constants.RECALL_TIME
        self.max_rounds = cambc.constants.MAX_ROUNDS
        self.winning_gems = cambc.constants.WINNING_GEMS

        # Tiles from which an altar can be tributed at.
        goals = Bitboard()
        ring = Bitboard()
        for altar in self.altars:
            ring.clear()
            ring.draw_chebyshev(altar, self.submit_range)
            goals |= ring
        goals &= ~self.static_blocked
        self.altar_goals = goals

        # Static distance fields: turns to the nearest altar approach tile and to our spawn.
        self.altar_field = DistanceField()
        self.altar_field.compute(self.static_blocked, goals, Bitboard())
        self.spawn_field = DistanceField()
        self.spawn_field.compute(self.static_blocked, self.spawn_ally, Bitboard())
        self._enemy_spawn_field = None       # built on first use (features only)
        marks.append(_cambc.fuel_remaining())

        # Camp groups: about 600K fuel, so exactly once.
        raw_groups = game_map.camp_groups()
        marks.append(_cambc.fuel_remaining())
        self.groups = [Group(i, g) for i, g in enumerate(raw_groups)]
        marks.append(_cambc.fuel_remaining())
        self.group_of_camp = {}
        self.camp_at_tile = {}                   # (x, y) -> (Camp, group index); filled by finish_init
        for g in self.groups:
            for handle in g.camp_handles:
                self.group_of_camp[handle] = g.index
        self.near_ready = False
        if config.LOG_GROUPS:
            spawn_get = self.spawn_field.get
            rows = ["g%d k%s at %d,%d gems=%d gold=%d hp=%d str=%d d=%d%s" % (
                g.index, g.kinds, g.tile_list[0].x, g.tile_list[0].y, g.gems, g.gold, int(g.total_health),
                int(g.total_strength), spawn_get(g.tile_list[0]), "" if g.reachable else " UNREACHABLE")
                for g in self.groups]
            print("groups:\n  " + "\n  ".join(rows), flush=True)

        # Who am I on the team?
        allies = cambc.champions(Team.ALLY)
        self.n_bodies = len(allies)
        self.slot = 0
        for index, champion in enumerate(allies):
            if int(champion) == self.handle:
                self.slot = index
                break

        self.kind = int(me.champion_name())
        self.kind_name = config.CHAMPION_NAMES[self.kind] if 0 <= self.kind < len(config.CHAMPION_NAMES) else str(self.kind)
        self.attack_range = me.attack_range()
        self.is_ranged = self.attack_range > 1
        self.abilities = []
        for slot, ability in enumerate(me.abilities()):
            try:
                if ability.info() is not None:
                    self.abilities.append(AbilityRec(slot, ability))
            except Exception:
                pass
        self.plan = memory_codec.decode_plan(bytes(cambc.DraftMemoryBuffer()))
        self.aggression = self.plan["aggression"]
        self.max_hp = me.max_health()
        self.strength = me.strength()
        self.focus = me.focus()
        self.dps = self._estimate_dps()
        marks.append(_cambc.fuel_remaining())
        self.init_profile = [marks[i] - marks[i + 1] for i in range(len(marks) - 1)]   # fields, groups, Group(), rest
        self.ready = True

    def _estimate_dps(self):
        """Sustained damage per turn against a camp, basic attacks plus abilities over their cooldown."""
        dps = self.strength
        for rec in self.abilities:
            if (rec.is_damage or rec.is_aoe or rec.is_cc) and rec.camps and rec.modifier > 0:
                dps += self.focus * rec.modifier / ABILITY_COOLDOWN
            elif rec.is_reset:
                dps += self.strength / ABILITY_COOLDOWN
        return max(dps, 1.0)

    def finish_init(self):
        """Second-turn setup: work that did not fit in the first turn's budget on big maps."""
        spawn_get = self.spawn_field.get
        for g in self.groups:
            if g.near is None:
                g.build_near()
            for camp in g.group.camps():
                pos = camp.spawn_position()
                self.camp_at_tile[(pos.x, pos.y)] = (camp, g.index)
            # Groups we cannot walk to from our spawn (holes, walls) are never candidates.
            g.reachable = spawn_get(g.tile_list[0]) != UNREACHABLE
        self.near_ready = True

    def first_target(self, claims):
        """Cheap first-turn target: nearest reachable group from spawn that pays gems, that we could
        clear alone, and that no ally has claimed yet (claims are written as the round proceeds)."""
        taken = {group_index for slot, (status, group_index) in enumerate(claims)
                 if slot != self.slot and group_index >= 0}
        budget = self.max_hp * (1 - config.HEALTH_RESERVE)
        best, best_d = None, UNREACHABLE
        get = self.spawn_field.get
        for g in self.groups:
            if g.gems <= 0 or g.index in taken:
                continue
            if not g.harmless and g.total_strength * (int(g.total_health / self.dps) + 1) >= budget:
                continue
            d = get(g.tile_list[0])
            if d < best_d:
                best, best_d = g, d
        return best

    # ---- every turn --------------------------------------------------------------
    def refresh(self):
        me = self.me
        self.events = set()
        self.moved = False
        self.cast_exclusive = False
        self.round = cambc.round_number()
        state = me.state()
        self.alive = ChampionState.ALIVE in state
        self.gold = me.gold_count()
        if not self.alive:
            self.hp = 0.0
            self.hp_ratio = 0.0
            self.gems = 0
            self.can_move = self.can_attack = self.can_cast = False
            self.channeling = False
            self.enemies = []
            self.allies = []
            self.visible_camps = []
            self.nearest_enemy = None
            self.prev_hp = None
            self.damage_taken = 0.0
            self.claims = memory_codec.read_claims()
            return

        self.pos = me.position()
        self.hp = me.health()
        self.max_hp = me.max_health()
        self.hp_ratio = self.hp / self.max_hp if self.max_hp > 0 else 0.0
        self.gems = me.gem_count()
        self.damage_taken = max(0.0, self.prev_hp - self.hp) if self.prev_hp is not None else 0.0
        if self.damage_taken > 0.15 * self.max_hp:
            self.events.add("big_damage")
        self.prev_hp = self.hp
        if self.gems > self.prev_gems:
            self.events.add("gems_gained")
        elif self.gems < self.prev_gems:
            self.events.add("gems_lost")
        self.prev_gems = self.gems

        self.can_move = ChampionState.CAN_MOVE in state
        self.can_attack = ChampionState.CAN_ATTACK in state
        self.can_cast = ChampionState.CAN_CAST in state
        self.channeling = bool(state & (ChampionState.RECALLING | ChampionState.SUBMITTING_GEMS
                                        | ChampionState.TELEPORTING))
        self.at_spawn = self.spawn_ally.is_set(self.pos)
        self.in_bush = self.bushes.is_set(self.pos)
        self.turns_to_altar = self.altar_field.get(self.pos)
        self.turns_to_spawn = self.spawn_field.get(self.pos)

        # Stats change only after shopping, which happens on spawn or while dead.
        if self.at_spawn:
            self.strength = me.strength()
            self.focus = me.focus()
            self.dps = self._estimate_dps()

        profiling = config.PROFILE_EVERY > 0
        marks = [_fuel()] if profiling else None
        self.visible = vision.visible_tiles()
        self._refresh_enemies()
        if profiling:
            marks.append(_fuel())
        if self.enemies or self.want_allies or self.round % ALLY_REFRESH_EVERY == 0 or not hasattr(self, "allies"):
            self._refresh_allies()
        else:
            self.ally_power = self.hp * self.strength
        if profiling:
            marks.append(_fuel())
        self._refresh_camps()
        if profiling:
            marks.append(_fuel())
        self.claims = memory_codec.read_claims()
        if profiling:
            marks.append(_fuel())
            self.refresh_profile = [marks[i] - marks[i + 1] for i in range(len(marks) - 1)]

    def _refresh_enemies(self):
        had_enemy = self.last_enemy_round == self.round - 1
        self.enemies = []
        for champion in vision.champions(Team.ENEMY):
            try:
                self.enemies.append(Enemy(champion, self.pos))
            except Exception:
                continue
        self.nearest_enemy = None
        self.enemy_power = 0.0
        for enemy in self.enemies:
            self.enemy_power += enemy.hp * enemy.strength
            if self.nearest_enemy is None or enemy.dist < self.nearest_enemy.dist:
                self.nearest_enemy = enemy
            self.enemy_memory[enemy.handle] = (enemy.pos, self.round, enemy.hp / enemy.max_hp if enemy.max_hp else 0.0)
        if self.enemies:
            if not had_enemy:
                self.events.add("enemy_appeared")
            self.last_enemy_round = self.round
            self.last_enemy_pos = self.nearest_enemy.pos
            memory_codec.write_enemy_seen(self.round)
        # Enemies remembered at low health that vanished next to us probably died.
        for handle, (pos, seen, ratio) in list(self.enemy_memory.items()):
            if seen == self.round - 1 and ratio < 0.12 and pos.chebyshev_distance(self.pos) <= 2 \
                    and all(e.handle != handle for e in self.enemies):
                self.enemies_killed.append(self.round)
                del self.enemy_memory[handle]

    def _refresh_allies(self):
        self.allies = []
        self.ally_power = self.hp * self.strength
        for champion in vision.champions(Team.ALLY):
            if int(champion) == self.handle:
                continue
            try:
                pos = champion.position()
                hp = champion.health()
                strength = champion.strength()
            except Exception:
                continue
            self.allies.append((pos, hp, strength, pos.chebyshev_distance(self.pos)))
            if pos.chebyshev_distance(self.pos) <= config.FIGHT_RANGE:
                self.ally_power += hp * strength

    def _refresh_camps(self):
        # Visible camps: (camp, group index, position, health). vision.camps() rebuilds every camp
        # group object on each call (600K+ fuel), so visible camp tiles are mapped through our own
        # static table instead.
        self.visible_camps = []
        lookup = self.camp_at_tile
        for pos in vision.camp_tiles().positions():
            entry = lookup.get((pos.x, pos.y))
            if entry is None:
                continue
            camp, gi = entry
            try:
                self.visible_camps.append((camp, gi, pos, camp.health()))
            except Exception:
                continue
        # Group states, only for groups that can have a spawn tile in vision.
        pos = self.pos
        for g in self.groups:
            if g.near is not None:
                if not g.near.is_set(pos):
                    continue
            if (g.tiles & self.visible).is_empty():
                continue
            try:
                state = g.group.state()
            except Exception:
                continue
            old = g.state
            if isinstance(state, game_map.CampGroupRespawning):
                g.state = RESPAWNING
                g.timer = state.timer
                g.respawn_round = self.round + state.timer
            elif isinstance(state, game_map.CampGroupRewardAvailable):
                g.state = REWARD
                g.timer = 0
            else:
                g.state = ALIVE
                g.timer = 0
                g.respawn_round = -1
            g.seen_round = self.round
            if old != g.state:
                if g.state == REWARD:
                    self.events.add("camp_cleared")
                elif old == REWARD and g.state == RESPAWNING:
                    self.events.add("capture_finished")

    # ---- derived -----------------------------------------------------------------
    def team_gems_banked(self):
        if self._banked_round != self.round:
            self._banked = (cambc.global_submitted_gems(Team.ALLY), cambc.global_submitted_gems(Team.ENEMY))
            self._banked_round = self.round
        return self._banked[0]

    def enemy_gems_banked(self):
        self.team_gems_banked()
        return self._banked[1]

    def shards_bought(self):
        try:
            return sum(self.me.item_count())
        except Exception:
            return 0

    def turns_to_enemy_spawn(self):
        if self._enemy_spawn_field is None:
            self._enemy_spawn_field = DistanceField()
            self._enemy_spawn_field.compute(self.static_blocked & ~self.spawn_enemy, self.spawn_enemy, Bitboard())
        return self._enemy_spawn_field.get(self.pos)

    def ensure_feature_state(self, decider):
        """Fill the extra per-turn facts only the networks need (a few engine calls)."""
        me = self.me
        self.cooldowns = [0, 0]
        for rec in self.abilities:
            try:
                self.cooldowns[rec.slot] = rec.ability.cooldown()
            except Exception:
                pass
        state = me.state()
        self.recalling = ChampionState.RECALLING in state
        self.tributing = ChampionState.SUBMITTING_GEMS in state
        self.teleporting = ChampionState.TELEPORTING in state
        try:
            self.stunned = bool(me.restrictions() & cambc.BuffRestrictions.MOVE)
        except Exception:
            self.stunned = False
        try:
            self.channel_time = me.remaining_channel_time() if self.channeling else 0
        except Exception:
            self.channel_time = 0
        self.has_altar_lock = self.has_tp_crystal = False
        try:
            for consumable in me.consumables():
                if consumable.count() > 0:
                    if int(consumable.kind()) == int(cambc.ConsumableType.ALTAR_LOCK):
                        self.has_altar_lock = True
                    else:
                        self.has_tp_crystal = True
        except Exception:
            pass
        self.capture_rounds_left = max(0, config.CAPTURE_TURNS - decider.mode_turns) if decider.mode == config.CAPTURE else 0
        incoming = 0.0
        for camp, gi, pos, hp in self.visible_camps:
            if hp > 0 and pos.chebyshev_distance(self.pos) <= 3:
                g = self.groups[gi]
                incoming += g.total_strength / max(g.n_camps, 1)
        self.camp_damage_incoming = incoming

    def allies_near(self, tile, distance):
        """Visible allies within distance of a tile (from the last ally scan)."""
        return sum(1 for pos, hp, strength, d in self.allies if pos.chebyshev_distance(tile) <= distance)

    def power_balance(self):
        """Our local power over theirs; large when no enemy is visible."""
        if self.enemy_power <= 0:
            return 99.0
        return self.ally_power / self.enemy_power

    def enemy_within(self, distance):
        return self.nearest_enemy is not None and self.nearest_enemy.dist <= distance

    def rounds_left(self):
        return self.max_rounds - self.round

    def group_of_visible_camp(self, camp):
        return self.group_of_camp.get(int(camp))

    def note_moved(self, destination):
        self.moved = True
        self.pos = destination
        if self.is_ranged:
            self.can_attack = False
        self.at_spawn = self.spawn_ally.is_set(destination)
        self.in_bush = self.bushes.is_set(destination)
