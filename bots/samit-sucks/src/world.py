"""Cached world model, one instance per champion body.

Static facts (map, camp groups, distance fields to altars and spawn) are built on the first turn,
which has double fuel, and on maps with many camps the turn after (budget.first_turn_fits).
refresh() updates the per-turn view with as few engine calls as possible,
and every remembered fact carries the round it was seen. Decisions and features read plain
attributes from here rather than calling the SDK.
"""
import _cambc
import cambc
from cambc import game_map, vision

import budget
import config
import memory_codec

_fuel = _cambc.fuel_remaining          # setup profile only (logged, never branched on)

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
                 "respawn_round", "value", "near", "kinds", "reachable", "avoid_until", "anchor",
                 "attack_range", "camp_static", "health_left", "camp_damage")

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
        self.attack_range = 3                    # the longest reach among the group's monsters
        self.kinds = []
        for camp in camps:
            kind = camp._type                    # Camp.kind() builds an enum each call; the raw int is free
            stats = _camp_stats.get(kind)
            if stats is None:
                stats = _camp_stats[kind] = camp.base_stats()
            self.total_health += stats.base_health
            self.total_strength += stats.base_strength
            reach = int(getattr(stats, "base_attack_range", 3) or 3)
            if reach > self.attack_range:
                self.attack_range = reach
            self.kinds.append(kind)
        self.harmless = self.total_strength == 0
        self.near = None          # built by World.finish_init on the second turn
        self.anchor = None        # walkable tile beside the group nearest our spawn (finish_init)
        self.reachable = True
        self.avoid_until = -1     # round until which this body ignores the group (gave up waiting)
        self.state = UNKNOWN
        self.timer = 0
        self.seen_round = -1
        self.respawn_round = -1        # round the group is expected back, when known
        self.value = self.gems * config.GEM_VALUE + self.gold * config.GOLD_VALUE
        self.camp_static = None   # camp_features caches the group's fixed inputs here
        self.health_left = self.total_health   # base health minus the damage seen on its camps
        self.camp_damage = {}     # camp tile -> damage seen since the group last respawned

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


_kind_of = {}   # champion handle -> name (a champion's type never changes)


def champion_power(champion, hp, strength):
    """(damage a turn, health with shields) from the champion's kit (config.CHAMPION_KIT)."""
    handle = int(champion)
    name = _kind_of.get(handle)
    if name is None:
        try:
            k = int(champion.champion_name())
            name = config.CHAMPION_NAMES[k] if 0 <= k < len(config.CHAMPION_NAMES) else ""
        except Exception:
            name = ""
        _kind_of[handle] = name
    kit = config.CHAMPION_KIT.get(name)
    if kit is None:
        return strength, hp
    s_mult, f_mult, shield = kit
    if f_mult or shield:
        try:
            focus = champion.focus()
        except Exception:
            focus = 0.0
    else:
        focus = 0.0
    return strength * s_mult + focus * f_mult, hp + focus * shield


class Enemy:
    __slots__ = ("champion", "handle", "pos", "hp", "max_hp", "strength", "gems", "dist", "dmg", "ehp", "on_spawn", "reach")

    def __init__(self, champion, my_pos):
        self.champion = champion
        self.handle = int(champion)
        self.pos = champion.position()
        self.hp = champion.health()
        self.max_hp = champion.max_health()
        self.strength = champion.strength()
        self.dmg = self.strength                 # damage a turn with abilities counted (set below)
        self.ehp = self.hp                       # health with shields counted
        if config.CHAMPION_POWER:
            self.dmg, self.ehp = champion_power(champion, self.hp, self.strength)
        elif config.FOCUS_POWER_WEIGHT:
            try:
                self.dmg = self.strength + config.FOCUS_POWER_WEIGHT * champion.focus()
            except Exception:
                pass
        self.dist = my_pos.chebyshev_distance(self.pos)
        self.on_spawn = False                    # standing on its own spawn (set by World._refresh_enemies)
        try:
            self.reach = max(1, champion.attack_range())   # 1 melee, 3 ranged (contact features, threat tests)
        except Exception:
            self.reach = 1
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
        self._spawn_centers = None
        self.squad = [0]               # slots in our squad (config.SQUADS; _init_squads)
        self.leader = 0                # the squad's leader this turn: its lowest slot alive and not busy
        self.is_leader = True
        self.leader_claim = None       # (status, group) the leader claims: the squad's plan
        self.squad_available = 0       # squad mates alive and not busy (dead, recalling, banking)
        self.squad_claim_groups = set()   # groups squad mates claim (farm, hold, capture)
        self.leader_busy_group = -1    # the group the leader has been retreating from, and for how many rounds running
        self.leader_busy_rounds = 0
        self.middle_share = 0.0        # share of the camp groups on the centre line (_init_squads)
        self.contested = None          # byte 6: (group index, rounds old) an enemy was last seen at
        self.help_calls = set()
        self.claim_counts = {}
        self.spawn_field_stale = False     # navigation.seal_holes: rebuild the spawn field when affordable
        self.waiting_dash = False          # set by the navigator when a hole crossing waits on the dash cooldown
        self.last_enemy_round = -10_000
        self.last_enemy_pos = None
        self.enemies_killed = []       # rounds at which an enemy was seen to die
        self.distant_enemies = []      # in sight but unreachable and out of reach (config.THREAT_DETOUR)
        self.altar_locks = {}          # (x, y) -> round last seen, for enemy locks on altars; cleared when the altar
                                       # is seen bare (a lock lasts until broken, so out of sight it is still there)
        self.visible_locks = {}        # (x, y) -> AltarLock snapshot this turn
        self._threat_walk = None       # that test's walking field, reused while we stand still
        self._threat_walk_at = None
        self._threat_walk_round = -99
        self.events = set()
        self.prev_gems = 0
        self.ward_round = -10_000
        self.recalling = False         # in a recall channel (refresh)
        self.ward_pos = None           # where our last ward went (actions.ward_often spaces them)
        self.moved = False
        self.cast_exclusive = False
        self._banked = (0, 0)
        self._banked_round = -1
        self.want_allies = False       # set by actions when waiting for help at a camp
        self.shop_exhausted = False    # every shard family complete; gold has nothing to buy
        self.refresh_profile = None
        self.heavy = False
        self.fields_waited = 0         # new big fields held to the next turn (navigation.field_to), logged
        self.groups = None             # built by init_static; init_rest finishes setup (ready)
        self.init_profile = []

    # ---- first turn --------------------------------------------------------------
    def init_static(self):
        """Setup up to the camp groups, then the rest too when the map's counts say it fits the
        first turn; otherwise init_rest runs on the next turn."""
        marks = [_fuel()]
        me = cambc.me()
        self.me = me
        self.handle = int(me)
        self.pos = me.position()               # refresh() updates it; finish_init may run first
        self.width = game_map.width()
        self.height = game_map.height()
        self.walls = game_map.walls()
        self.holes = game_map.holes()
        self.bushes = game_map.bushes()
        self.altar_tiles = game_map.altars()
        self.spawn_ally = game_map.spawn_region(Team.ALLY)
        self.spawn_enemy = game_map.spawn_region(Team.ENEMY)
        # Terrain only: what a single step must avoid (units are added per turn by the navigator).
        self.terrain_blocked = self.walls | self.holes | self.altar_tiles | self.spawn_enemy
        self.altars = self.altar_tiles.positions()
        self.submit_range = cambc.constants.SUBMIT_GEMS_RANGE
        self.recall_time = cambc.constants.RECALL_TIME
        self.max_rounds = cambc.constants.MAX_ROUNDS
        self.winning_gems = cambc.constants.WINNING_GEMS
        marks.append(_fuel())

        # Camp groups: about 600K fuel, so exactly once.
        raw_groups = game_map.camp_groups()
        marks.append(_fuel())
        self.groups = [Group(i, g) for i, g in enumerate(raw_groups)]
        # Camps stand on their spawn tiles whenever they are alive, which is most of the time, so
        # every cached route treats them as walls. Otherwise the cheapest path to an altar or to the
        # next camp runs straight through a monster and the body stands still forever (collision,
        # rows). Captures walk onto a group's own tiles through fields whose goals are those tiles.
        camp_tiles = Bitboard()
        for g in self.groups:
            camp_tiles |= g.tiles
        self.camp_tiles = camp_tiles
        self.static_blocked = self.terrain_blocked | camp_tiles
        # A dash (Issac, Vesper Vale, Vendetta, Ren, Heart of Ruin: 2 tiles, cooldown 8) crosses a
        # hole. A hole with walkable tiles on opposite sides is "thin": for a champion with a dash
        # every cached field treats it as passable, so routes cross hole lines that wall whole
        # maps off for walkers (01starvation: 180 turns round, 40 across). Stepping still never
        # enters a hole; the navigator dashes over it when the route goes through one.
        self.thin_holes = Bitboard()
        self.has_dash = False
        try:
            self.has_dash = any(a.is_dash() for a in me.abilities())
        except Exception:
            pass
        # Bitboard shifts, about 130K fuel. The first turn is near its budget on the biggest maps
        # (islands: 46 groups, 878 holes), so this always waits for finish_init's leftover fuel,
        # which then rebuilds the spawn field. Never positions() here: a list of 878 Vec2 costs
        # 250K on its own.
        self.thin_pending = config.DASH_ROUTES and self.has_dash and not self.holes.is_empty()
        marks.append(_fuel())
        self.init_profile = [marks[i] - marks[i + 1] for i in range(len(marks) - 1)]   # map, groups, boards
        if budget.first_turn_fits(len(self.groups), sum(g.n_camps for g in self.groups), self.width * self.height):
            self.init_rest()

    def init_rest(self):
        """The second half of setup: spawn field, roster, abilities, the draft plan."""
        mark = _fuel()
        me = self.me
        # Static distance field to our spawn. A 100x100 field costs ~750K, and the first turn's
        # budget (about 4.4M) is already mostly spent on the camp groups on big maps, so the altar
        # field and the per-group anchors are built by finish_init, a fixed step a turn.
        self.spawn_field = DistanceField()
        self.spawn_field.compute(self.static_blocked, self.spawn_ally, Bitboard())
        self.altar_field = None
        self.fields_changed = False            # bot.py clears the navigator's cached fields when set
        self.altar_goals = None
        self._enemy_spawn_field = None       # built on first use (features only)
        self.group_of_camp = {}
        self.camp_at_tile = {}                   # (x, y) -> (Camp, group index); filled by finish_init
        for g in self.groups:
            for handle in g.camp_handles:
                self.group_of_camp[handle] = g.index
        self.near_ready = False
        if config.LOG_GROUPS:
            rows = ["g%d k%s at %d,%d gems=%d gold=%d hp=%d str=%d" % (
                g.index, g.kinds, g.tile_list[0].x, g.tile_list[0].y, g.gems, g.gold, int(g.total_health),
                int(g.total_strength))
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
        self.ally_champions = list(allies)
        self._init_squads()

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
        self.init_profile.append(mark - _fuel())                                      # rest
        self.ready = True

    def _init_squads(self):
        """Fixed squads by slot (config.SQUADS): SQUAD_SIZE bodies each, the remainder joining the
        last one (5 bodies: 2 + 3), or everybody in one squad when most camp groups sit on the
        centre line (corridors: all twelve groups there, 0/7 against the band with each body
        fighting the middle alone). Every body computes the same split, so nothing is shared.
        The centre-line count waits for finish_init's leftover fuel (about 3K per group: on the
        first turn it ran islands, boss battle and yellow brick road out of fuel)."""
        self.squad = [self.slot]
        self.middle_share = 0.0
        self.middle_pending = config.SQUADS and self.n_bodies >= 2
        if not config.SQUADS or self.n_bodies < 2:
            return
        n = self.n_bodies
        size = max(1, config.SQUAD_SIZE)
        start = (self.slot // size) * size
        if start + 2 * size > n:
            start = min(start, max(0, (n // size - 1) * size))
            end = n
        else:
            end = start + size
        self.squad = list(range(start, end))

    def _middle_share(self):
        """Share of the camp groups about as far from their spawn as from ours; with most of them
        there the whole team plays as one squad."""
        self.middle_pending = False
        ours, theirs = self.spawn_centers()
        middle = 0
        for g in self.groups:
            tile = g.tile_list[0]
            if abs(tile.chebyshev_distance(ours) - tile.chebyshev_distance(theirs)) <= config.SQUAD_MIDDLE_TOLERANCE:
                middle += 1
        self.middle_share = middle / max(len(self.groups), 1)
        if self.middle_share >= config.SQUAD_ALL_MIDDLE:
            self.squad = list(range(self.n_bodies))

    def _refresh_squad(self):
        """Who leads the squad this turn (its lowest slot alive and not busy: a claim of BUSY with
        no group is a dead, recalling or banking body) and what it claims, which is the squad's
        plan: FARM or IDLE with a group = go there (IDLE = holding for the rest of us), CAPTURE
        = capturing it, BUSY with a group = retreating from it."""
        self.leader = self.slot
        self.is_leader = True
        self.leader_claim = None
        self.squad_available = 0
        if len(self.squad) < 2:
            return
        for slot in self.squad:
            if slot == self.slot or slot >= len(self.claims):
                continue
            status, group = self.claims[slot]
            if status == memory_codec.STATUS_BUSY and group < 0:
                continue
            try:
                if not self.ally_champions[slot].is_alive():
                    continue
            except Exception:
                continue
            self.squad_available += 1
            if slot < self.leader:
                self.leader = slot
                self.leader_claim = (status, group)
        self.is_leader = self.leader == self.slot
        claim = self.leader_claim
        if claim is not None and claim[0] == memory_codec.STATUS_BUSY and claim[1] >= 0:
            self.leader_busy_rounds = self.leader_busy_rounds + 1 if claim[1] == self.leader_busy_group else 1
            self.leader_busy_group = claim[1]
        else:
            self.leader_busy_group = -1
            self.leader_busy_rounds = 0

    def _estimate_dps(self):
        """Sustained damage per turn against a camp, basic attacks plus abilities over their cooldown."""
        dps = self.strength
        for rec in self.abilities:
            if (rec.is_damage or rec.is_aoe or rec.is_cc) and rec.camps and rec.modifier > 0:
                dps += self.focus * rec.modifier / ABILITY_COOLDOWN
            elif rec.is_reset:
                dps += self.strength / ABILITY_COOLDOWN
        return max(dps, 1.0)

    def _find_thin_holes(self):
        """Holes with walkable tiles on opposite sides (a legal two-step dash path), via shifts
        along the four axes. Removes them from static_blocked so every field built afterwards
        routes across them."""
        self.thin_pending = False
        walk = ~self.static_blocked
        for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
            a = walk
            b = walk
            if dx:
                a = a.shift_right(dx)
                b = b.shift_right(-dx)
            if dy:
                a = a.shift_up(dy)
                b = b.shift_up(-dy)
            self.thin_holes |= self.holes & a & b
        if not self.thin_holes.is_empty():
            self.static_blocked = self.static_blocked & ~self.thin_holes
            return True
        return False

    def setup_alone(self):
        """Setup gets whole turns, without acting, on maps whose whole-map field is too big to
        build beside a turn's acting (big world ran rounds 2 to 6 out of fuel). The map's size
        decides, so these are the same rounds on every run."""
        return config.FIELD_COST_PER_TILE * self.width * self.height > config.INIT_INLINE_MAX

    def finish_init(self):
        """Setup that does not fit the first turn, one fixed step at the end of each turn until done:
        the thin holes (and the spawn field rebuilt across them), then the centre-line share and the
        altar field, then INIT_ANCHORS_PER_TURN group anchors a turn, nearest first. The steps never
        depend on the fuel left, so a map always finishes setup on the same round."""
        if self.thin_pending:
            if self._find_thin_holes():
                # The spawn field and any anchors were built with the holes as walls.
                self.spawn_field = DistanceField()
                self.spawn_field.compute(self.static_blocked, self.spawn_ally, Bitboard())
                for g in self.groups:
                    g.anchor = None
                    g.reachable = True
                self.fields_changed = True
            return
        if self.altar_field is None:
            if self.middle_pending:
                self._middle_share()
            # Tiles from which an altar can be tributed at, and the field of turns to the nearest.
            goals = Bitboard()
            ring = Bitboard()
            for altar in self.altars:
                ring.clear()
                ring.draw_chebyshev(altar, self.submit_range)
                goals |= ring
            goals &= ~self.static_blocked
            self.altar_goals = goals
            field = DistanceField()
            field.compute(self.static_blocked, goals, Bitboard())
            self.altar_field = field
            self.turns_to_altar = field.get(self.pos)
            return
        spawn_get = self.spawn_field.get
        ring = Bitboard()
        tmp = Bitboard()
        here = self.pos
        # Nearest groups first, so the ones we will farm soonest are candidates soonest.
        built = 0
        for g in sorted(self.groups, key=lambda g: here.chebyshev_distance(g.tile_list[0])):
            if g.anchor is not None:
                continue
            if built == (config.INIT_ANCHORS_ALONE if self.setup_alone() else config.INIT_ANCHORS_PER_TURN):
                return
            built += 1
            if g.near is None:
                g.build_near()
            for camp in g.group.camps():
                pos = camp.spawn_position()
                stats = _camp_stats.get(camp._type)
                self.camp_at_tile[(pos.x, pos.y)] = (camp, g.index, stats.base_health if stats is not None else 0.0)
            # The anchor is the walkable tile beside the group nearest our spawn. Camp tiles are
            # walls in every field, so distances to a group are read at its anchor.
            ring.clear()
            for tile in g.tile_list:
                tmp.clear()
                tmp.draw_chebyshev(tile, 1)
                ring |= tmp
            ring &= ~self.static_blocked
            best, best_d = None, UNREACHABLE
            for tile in ring.positions():
                d = spawn_get(tile)
                if d < best_d:
                    best, best_d = tile, d
            g.anchor = best if best is not None else g.tile_list[0]
            # Groups we cannot walk to from our spawn (holes, walls, other camps) are never candidates.
            g.reachable = best is not None
        self.near_ready = True

    def first_target(self, claims):
        """Cheap first-turn target: nearest reachable group from spawn that pays gems, that we could
        clear alone, and that no ally has claimed yet (claims are written as the round proceeds)."""
        if config.SQUADS and not self.is_leader and self.leader_claim is not None:
            # The leader (a lower slot, so it usually acted earlier this round) has claimed: go there.
            status, group = self.leader_claim
            if status != memory_codec.STATUS_BUSY and 0 <= group < len(self.groups) and self.groups[group].gems > 0:
                return self.groups[group]
        taken = {group_index for slot, (status, group_index) in enumerate(claims)
                 if slot != self.slot and group_index >= 0}
        budget = self.max_hp * (1 - config.HEALTH_RESERVE)
        best, best_d = None, UNREACHABLE
        here = self.pos
        for g in self.groups:
            if g.gems <= 0 or g.index in taken:
                continue
            if not g.harmless and g.total_strength * (int(g.total_health / self.dps) + 1) >= budget:
                continue
            # Straight-line distance: anchors and fields are not built yet, and the regular camp
            # ranking takes over within RETARGET_EVERY rounds.
            d = here.chebyshev_distance(g.tile_list[0])
            if d < best_d:
                best, best_d = g, d
        return best

    # ---- every turn --------------------------------------------------------------
    def refresh(self):
        me = self.me
        if self.spawn_field_stale:
            self.spawn_field_stale = False
            field = DistanceField()
            field.compute(self.static_blocked, self.spawn_ally, Bitboard())
            self.spawn_field = field
        self.events = set()
        self.heavy = False             # a heavy job ran this turn: a camp-list rebuild or a big new field
        self.moved = False
        self.waiting_dash = False
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
            self.recalling = False
            self.enemies = []
            self.allies = []
            self.visible_camps = []
            self.camp_damage_incoming = 0.0
            self.nearest_enemy = None
            self.prev_hp = None
            self.damage_taken = 0.0
            self.claims = memory_codec.read_claims()
            return

        self.last_pos = getattr(self, "pos", None)
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
        self.recalling = bool(state & ChampionState.RECALLING)
        self.at_spawn = self.spawn_ally.is_set(self.pos)
        self.in_bush = self.bushes.is_set(self.pos)
        self.turns_to_altar = self.altar_field.get(self.pos) if self.altar_field is not None else UNREACHABLE
        self.turns_to_spawn = self.spawn_field.get(self.pos)

        # Stats change only after shopping, which happens on spawn or while dead.
        if self.at_spawn:
            self.strength = me.strength()
            self.focus = me.focus()
            self.dps = self._estimate_dps()

        profiling = config.PROFILE_EVERY > 0
        marks = [_fuel()] if profiling else None
        self.visible = vision.visible_tiles()
        self._refresh_altar_locks()
        self._refresh_enemies()
        if profiling:
            marks.append(_fuel())
        if self.enemies or self.want_allies or self.round % ALLY_REFRESH_EVERY == 0 or not hasattr(self, "allies"):
            self._refresh_allies()
        else:
            self.ally_power = self.hp * self.strength
            self.ally_power_eff = self.my_ehp() * self.my_dmg()
        if profiling:
            marks.append(_fuel())
        self._refresh_camps()
        if profiling:
            marks.append(_fuel())
        self.claims = memory_codec.read_claims()
        self.contested = memory_codec.read_contested(self.round)
        self._index_claims()
        if config.SQUADS:
            self._refresh_squad()
        if profiling:
            marks.append(_fuel())
            self.refresh_profile = [marks[i] - marks[i + 1] for i in range(len(marks) - 1)]

    def _refresh_altar_locks(self):
        self.visible_locks = {}
        try:
            for lock in vision.altar_locks(Team.ENEMY):
                p = lock.position()
                self.visible_locks[(p.x, p.y)] = lock
        except Exception:
            pass
        for altar in self.altars:
            key = (altar.x, altar.y)
            if key in self.visible_locks:
                self.altar_locks[key] = self.round
            elif key in self.altar_locks and self.visible.is_set(altar):
                del self.altar_locks[key]            # seen bare: broken since

    def _refresh_enemies(self):
        had_enemy = self.last_enemy_round == self.round - 1
        self.enemies = []
        self.distant_enemies = []
        for champion in vision.champions(Team.ENEMY):
            try:
                self.enemies.append(Enemy(champion, self.pos))
            except Exception:
                continue
        if self.enemies and config.SPAWN_SITTERS_DISTANT:
            # An enemy on its own spawn heals every turn and cannot be reached (reverse spiral: the
            # two spawns touch at the centre; our four bodies brawled across the line for 2,000
            # rounds, 14 gems, while the opponent farmed the outer ring). Not a target, and not a
            # threat unless it is beside us; it becomes one the moment it steps off.
            keep = []
            for e in self.enemies:
                e.on_spawn = self.spawn_enemy.is_set(e.pos)
                if e.on_spawn and e.dist > 1:
                    self.distant_enemies.append(e)
                else:
                    keep.append(e)
            self.enemies = keep
        if self.enemies and config.THREAT_DETOUR:
            # An enemy behind a wall is in sight but no threat until it can walk to us or we stand in
            # its reach. Two tests: the walk home differs from ours by more than the straight line
            # plus the detour (two field lookups), or, on small maps and only when a flee rule could
            # fire, a walking field from here explored THREAT_FIELD_RANGE out (a whole-board field
            # costs ~80 fuel a tile, so never on 100x100 maps in contact).
            far = [e for e in self.enemies if e.dist > 2]
            if far:
                try:
                    keep = []
                    walk = None
                    my_home = self.spawn_field.get(self.pos)
                    want_field = (self.width * self.height <= config.THREAT_FIELD_MAX_AREA
                                  and (len(self.enemies) >= 2 or self.hp_ratio < config.FLEE_HEALTH))
                    for e in self.enemies:
                        if e.dist <= 2:
                            keep.append(e)
                            continue
                        try:
                            reach = max(1, e.champion.attack_range())
                        except Exception:
                            reach = 1
                        if e.dist <= reach + 1:
                            keep.append(e)
                            continue
                        their_home = self.spawn_field.get(e.pos)
                        if my_home is not None and their_home is not None and my_home != UNREACHABLE \
                                and their_home != UNREACHABLE and abs(my_home - their_home) > e.dist + config.THREAT_DETOUR:
                            self.distant_enemies.append(e)   # the walk between us is at least that long
                            continue
                        if not want_field:
                            keep.append(e)
                            continue
                        if walk is None:
                            if self._threat_walk is not None and self._threat_walk_at == (self.pos.x, self.pos.y) \
                                    and self.round - self._threat_walk_round <= config.THREAT_FIELD_REUSE:
                                walk = self._threat_walk
                            else:
                                roots = Bitboard()
                                roots.set(self.pos)
                                walls = self.static_blocked
                                if walls.is_set(self.pos):
                                    walls = walls & ~roots
                                walk = DistanceField()
                                walk.compute(walls, roots, Bitboard(), config.THREAT_FIELD_RANGE)
                                self._threat_walk, self._threat_walk_at, self._threat_walk_round = walk, (self.pos.x, self.pos.y), self.round
                        steps = walk.get(e.pos)
                        if steps != UNREACHABLE and steps <= e.dist + config.THREAT_DETOUR:
                            keep.append(e)
                        else:
                            self.distant_enemies.append(e)
                    self.enemies = keep
                except Exception:
                    self.distant_enemies = []
        self.nearest_enemy = None
        self.enemy_power = 0.0
        self.enemy_power_eff = 0.0
        for enemy in self.enemies:
            self.enemy_power += enemy.hp * enemy.strength
            self.enemy_power_eff += enemy.ehp * enemy.dmg
            if self.nearest_enemy is None or enemy.dist < self.nearest_enemy.dist:
                self.nearest_enemy = enemy
            self.enemy_memory[enemy.handle] = (enemy.pos, self.round, enemy.hp / enemy.max_hp if enemy.max_hp else 0.0)
        if self.enemies:
            if not had_enemy:
                self.events.add("enemy_appeared")
            self.last_enemy_round = self.round
            self.last_enemy_pos = self.nearest_enemy.pos
            pass
        # Enemies remembered at low health that vanished next to us probably died.
        for handle, (pos, seen, ratio) in list(self.enemy_memory.items()):
            if seen == self.round - 1 and ratio < 0.12 and pos.chebyshev_distance(self.pos) <= 2 \
                    and all(e.handle != handle for e in self.enemies):
                self.enemies_killed.append(self.round)
                del self.enemy_memory[handle]

    def my_dmg(self):
        if config.CHAMPION_POWER:
            s_mult, f_mult, shield = config.CHAMPION_KIT.get(self.kind_name, (1.0, 0.0, 0.0))
            return self.strength * s_mult + self.focus * f_mult
        return self.strength + config.FOCUS_POWER_WEIGHT * self.focus

    def my_ehp(self):
        if config.CHAMPION_POWER:
            return self.hp + self.focus * config.CHAMPION_KIT.get(self.kind_name, (1.0, 0.0, 0.0))[2]
        return self.hp

    def _refresh_allies(self):
        self.allies = []
        self.ally_power = self.hp * self.strength
        self.ally_power_eff = self.my_ehp() * self.my_dmg()
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
                dmg, ehp = strength, hp
                if config.CHAMPION_POWER:
                    dmg, ehp = champion_power(champion, hp, strength)
                elif config.FOCUS_POWER_WEIGHT:
                    try:
                        dmg = strength + config.FOCUS_POWER_WEIGHT * champion.focus()
                    except Exception:
                        pass
                self.ally_power_eff += ehp * dmg

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
            camp, gi, base = entry
            try:
                hp = camp.health()
            except Exception:
                continue
            self.visible_camps.append((camp, gi, pos, hp))
            # Camps do not heal until they respawn: a boss worn down on one trip stays worn down.
            # (One comparison for an untouched camp: big maps show 20+ camps a turn.)
            if 0 < hp < base:
                g = self.groups[gi]
                key = (pos.x, pos.y)
                lost = base - hp
                if lost > g.camp_damage.get(key, 0.0):
                    g.camp_damage[key] = lost
                    g.health_left = max(1.0, g.total_health - sum(g.camp_damage.values()))
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
            if g.state != ALIVE and g.camp_damage:
                g.camp_damage = {}                 # cleared or respawning: back to full next time
                g.health_left = g.total_health
            if old != g.state:
                if g.state == REWARD:
                    self.events.add("camp_cleared")
                elif old == REWARD and g.state == RESPAWNING:
                    self.events.add("capture_finished")
        # Damage per turn from living camps that have us in reach (3 for wolves, 4 for elks and
        # golems, 5 for monoliths). They only hit when awake, so pair this with damage_taken.
        incoming = 0.0
        for camp, gi, pos, hp in self.visible_camps:
            if hp > 0 and pos.chebyshev_distance(self.pos) <= self.groups[gi].attack_range:
                g = self.groups[gi]
                incoming += g.total_strength / max(g.n_camps, 1)
        self.camp_damage_incoming = incoming

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

    def spawn_centers(self):
        """Mean tile of each spawn region (ours, theirs), built once: which half of the map a camp
        is on, at two distance calls per candidate instead of one per spawn tile."""
        if self._spawn_centers is None:
            centers = []
            for region in (self.spawn_ally, self.spawn_enemy):
                tiles = region.positions()
                n = max(len(tiles), 1)
                centers.append(cambc.Vec2(sum(t.x for t in tiles) // n, sum(t.y for t in tiles) // n))
            self._spawn_centers = tuple(centers)
        return self._spawn_centers

    def enemy_spawn_field(self):
        """Walking turns to the enemy spawn, built on first use."""
        if self._enemy_spawn_field is None:
            self._enemy_spawn_field = DistanceField()
            self._enemy_spawn_field.compute(self.static_blocked & ~self.spawn_enemy, self.spawn_enemy, Bitboard())
        return self._enemy_spawn_field

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

    def _index_claims(self):
        """Once per turn: the groups allies are holding at for help, and how many allies name
        each group (score_camp asks per candidate, so this must not loop over the board there)."""
        self.help_calls = set()
        self.claim_counts = {}
        self.squad_claim_groups = set()
        squads = config.SQUADS and len(self.squad) > 1
        for slot, (status, claimed) in enumerate(self.claims):
            if slot != self.slot and claimed >= 0:
                if status == memory_codec.STATUS_BUSY:
                    continue                          # retreating from the group: not on it
                self.claim_counts[claimed] = self.claim_counts.get(claimed, 0) + 1
                in_squad = slot in self.squad
                if squads and in_squad:
                    self.squad_claim_groups.add(claimed)
                if status == memory_codec.STATUS_IDLE and not self.help_calls and (in_squad or not squads):
                    self.help_calls.add(claimed)      # one live call per team: the lowest slot's (our squad's, with squads)

    def refresh_action_flags(self):
        """Re-read what we may do this turn (after cancelling a channel)."""
        state = self.me.state()
        self.can_move = ChampionState.CAN_MOVE in state
        self.can_attack = ChampionState.CAN_ATTACK in state
        self.can_cast = ChampionState.CAN_CAST in state
        self.channeling = bool(state & (ChampionState.RECALLING | ChampionState.SUBMITTING_GEMS
                                        | ChampionState.TELEPORTING))
        self.recalling = bool(state & ChampionState.RECALLING)

    def help_called(self, group_index):
        return group_index in self.help_calls

    def ally_claims(self, group_index):
        return self.claim_counts.get(group_index, 0)

    def squad_claims(self, group_index):
        return group_index in self.squad_claim_groups

    def soloable(self, g):
        """Could this body clear the group alone with HEALTH_RESERVE to spare?"""
        if g.harmless:
            return True
        budget = max(self.hp - config.HEALTH_RESERVE * self.max_hp, 1.0)
        return g.total_strength * (int(g.health_left / self.dps) + 1) < budget

    def allies_near(self, tile, distance):
        """Visible allies within distance of a tile (from the last ally scan)."""
        return sum(1 for pos, hp, strength, d in self.allies if pos.chebyshev_distance(tile) <= distance)

    def power_balance(self):
        """Our local power over theirs; large when no enemy is visible. Power = health x damage a
        turn, abilities counted through focus (FOCUS_POWER_WEIGHT): the band's casters (Heart of
        Ruin at 688 focus and 52 strength) were rated at a fraction of their damage."""
        if not config.FOCUS_POWER_WEIGHT and not config.CHAMPION_POWER:
            return self.power_balance_raw()
        if self.enemy_power_eff <= 0:
            return 99.0
        return config.POWER_CALIBRATION * self.ally_power_eff / self.enemy_power_eff

    def power_balance_raw(self):
        """Strength-only balance: the selector's feature (it was trained on this)."""
        if self.enemy_power <= 0:
            return 99.0
        return config.NN_BALANCE_SCALE * self.ally_power / self.enemy_power

    def enemy_within(self, distance):
        return self.nearest_enemy is not None and self.nearest_enemy.dist <= distance

    def make_tile(self, x, y):
        return cambc.Vec2(x, y)

    def altar_field_or_none(self):
        return self.altar_field

    def walkable_neighbours(self, tile):
        """Neighbouring tiles that are not terrain-blocked: a body's ways out of a tile."""
        n = 0
        blocked = self.static_blocked
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx or dy:
                    x, y = tile.x + dx, tile.y + dy
                    if 0 <= x < self.width and 0 <= y < self.height and not blocked.is_set(cambc.Vec2(x, y)):
                        n += 1
        return n

    def spawn_power_balance(self):
        """Power of the bodies on our spawn (ours included) over every visible enemy's."""
        if self.enemy_power <= 0:
            return 99.0
        power = self.hp * self.strength
        for pos, hp, strength, d in self.allies:
            if self.spawn_ally.is_set(pos):
                power += hp * strength
        return power / self.enemy_power

    def spawn_tile_away(self, threats):
        """The spawn tile next to us furthest from the nearest threat, when it beats where we
        stand (ranged campers reach three tiles into the spawn; the far edge is out of reach)."""
        here = min(self.pos.chebyshev_distance(t) for t in threats)
        best, best_d = None, here
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx or dy:
                    tile = cambc.Vec2(self.pos.x + dx, self.pos.y + dy)
                    try:
                        if not self.spawn_ally.is_set(tile):
                            continue
                    except Exception:
                        continue
                    d = min(tile.chebyshev_distance(t) for t in threats)
                    if d > best_d:
                        best, best_d = tile, d
        return best

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
