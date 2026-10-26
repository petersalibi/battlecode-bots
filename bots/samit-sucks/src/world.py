"""What one body knows: the static map (built over the first turns, a fixed step a turn), and a lean
per-turn view. Decisions read plain attributes from here, never the SDK."""
import cambc
from cambc import game_map, vision

import board
import config
import kits
import plan

Team = cambc.Team
Bitboard = cambc.Bitboard
DistanceField = cambc.DistanceField
State = cambc.ChampionState
UNREACHABLE = 60000          # field values at or above this are unreached
ALIVE, REWARD, RESPAWNING, UNKNOWN = range(4)
NEAR_GROUP = 9               # groups whose centre is this close may be in sight
GROUP_SWEEP_EVERY = 8        # rounds between state reads of the groups near us (the job's: every turn)

_stats = {}                  # camp kind -> base stats


class Group:
    __slots__ = ("index", "group", "tiles", "tile_list", "cx", "cy", "gems", "gold", "camps", "health",
                 "strength", "reach", "value", "state", "timer", "back_round", "seen", "deaths", "capture", "health_left")

    def __init__(self, index, group):
        self.index = index
        self.group = group
        self.tiles = group.spawn_tiles()
        self.tile_list = self.tiles.positions()
        self.cx = sum(t.x for t in self.tile_list) // len(self.tile_list)
        self.cy = sum(t.y for t in self.tile_list) // len(self.tile_list)
        self.gems = group.gem_value()
        self.gold = group.gold_value()
        self.value = self.gems * config.GEM_VALUE + self.gold * config.GOLD_VALUE
        self.camps = list(group.camps())
        self.health = 0.0
        self.strength = 0.0
        self.reach = 0
        for camp in self.camps:
            kind = camp._type
            s = _stats.get(kind)
            if s is None:
                s = _stats[kind] = camp.base_stats()
            self.health += s.base_health
            self.strength += s.base_strength
            self.reach = max(self.reach, int(s.base_attack_range or 0))
        self.health_left = self.health  # camps keep their damage until the group respawns
        self.state = UNKNOWN
        self.timer = 0
        self.back_round = -1          # round it respawns, when seen respawning
        self.seen = -1
        self.deaths = []              # rounds our bodies died on a job here
        self.capture = None           # capture state type when last seen (CampGroupCaptureType)


class Enemy:
    __slots__ = ("champion", "pos", "x", "y", "hp", "max_hp", "dmg", "ehp", "reach", "dist", "gems", "on_spawn")


class World:
    def __init__(self):
        self.ready = False
        self.setup_steps = []
        self.round = 0
        self.job_group = None        # the group our job is on (its state is read every turn)
        self.prev_hp = None
        self.last_pos = None
        self.deaths = 0

    # ---- setup ---------------------------------------------------------------------
    def init_static(self):
        """Round one (2x fuel): who we are, the map, the camp groups. Fields wait for later turns."""
        me = cambc.me()
        self.me = me
        self.handle = int(me)
        allies = list(cambc.champions(Team.ALLY))
        self.n_bodies = len(allies)
        self.slot = next((i for i, c in enumerate(allies) if int(c) == self.handle), 0)
        self.ally_handles = allies
        k = int(me.champion_name())
        self.kind = kits.NAMES[k] if 0 <= k < len(kits.NAMES) else str(k)
        self.attack_range = me.attack_range()
        self.ranged = self.attack_range > 1
        self.abilities = []
        for ab in me.abilities():
            try:
                self.abilities.append(Ability(ab))
            except Exception:
                pass
        self.has_dash = any(a.dash for a in self.abilities)
        self.width, self.height = game_map.width(), game_map.height()
        self.walls, self.holes, self.bushes = game_map.walls(), game_map.holes(), game_map.bushes()
        self.altar_board = game_map.altars()
        self.altars = self.altar_board.positions()
        self.spawn_ally, self.spawn_enemy = game_map.spawn_region(Team.ALLY), game_map.spawn_region(Team.ENEMY)
        self.groups = [Group(i, g) for i, g in enumerate(game_map.camp_groups())]
        camp_tiles = Bitboard()
        self.camp_at = {}
        for g in self.groups:
            camp_tiles |= g.tiles
            for camp in g.camps:
                p = camp.spawn_position()
                self.camp_at[(p.x, p.y)] = (camp, g.index)
        self.camp_tiles = camp_tiles
        self.terrain = self.walls | self.holes | self.altar_board | self.spawn_enemy
        self.blocked = self.terrain | camp_tiles      # what every cached field walks around
        self.plan = plan.Plan(bytes(cambc.DraftMemoryBuffer()))
        self.board = board.Board(self.width, self.height, self.slot)
        self.submit_range = cambc.constants.SUBMIT_GEMS_RANGE
        self.max_rounds = cambc.constants.MAX_ROUNDS
        self.winning_gems = cambc.constants.WINNING_GEMS
        self.spawn_field = None
        self.setup_steps = ["spawn"] + (["holes"] if self.has_dash and not self.holes.is_empty() else [])
        self.pos = me.position()

    def setup_step(self):
        """One fixed step of the remaining setup; the order depends only on the map."""
        step = self.setup_steps.pop(0)
        if step == "spawn":
            self.spawn_field = DistanceField()
            self.spawn_field.compute(self.blocked, self.spawn_ally, Bitboard())
        elif step == "holes":
            # Holes with walkable tiles on opposite sides: a dash crosses them, so for this body
            # every field built afterwards routes across (the navigator dashes there).
            walk = ~self.blocked
            thin = Bitboard()
            for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
                a, b = walk, walk
                if dx:
                    a, b = a.shift_right(dx), b.shift_right(-dx)
                if dy:
                    a, b = a.shift_up(dy), b.shift_up(-dy)
                thin |= self.holes & a & b
            self.thin_holes = thin
            if not thin.is_empty():
                self.blocked = self.blocked & ~thin
                self.spawn_field = DistanceField()
                self.spawn_field.compute(self.blocked, self.spawn_ally, Bitboard())
        self.ready = not self.setup_steps

    # ---- every turn -------------------------------------------------------------------
    def refresh(self):
        me = self.me
        self.round = cambc.round_number()
        state = me.state()
        self.alive = State.ALIVE in state
        self.gold = me.gold_count()
        self.board.read(self.round)
        self.claims = self.board.claims
        self.enemies = []
        self.allies = []
        self.camps = []
        self.awake = None
        if not self.alive:
            self.prev_hp = None
            return
        self.last_pos = getattr(self, "pos", None)
        self.pos = me.position()
        self.x, self.y = self.pos.x, self.pos.y
        self.hp = me.health()
        self.max_hp = me.max_health()
        self.hp_ratio = self.hp / self.max_hp if self.max_hp else 0.0
        self.damage_taken = max(0.0, self.prev_hp - self.hp) if self.prev_hp is not None else 0.0
        self.prev_hp = self.hp
        self.gems = me.gem_count()
        self.strength = me.strength()
        self.focus = me.focus()
        self.dmg, self.ehp = kits.power(self.kind, self.strength, self.focus, self.hp)
        self.can_move = State.CAN_MOVE in state
        self.can_attack = State.CAN_ATTACK in state
        self.can_cast = State.CAN_CAST in state
        self.channeling = bool(state & (State.RECALLING | State.SUBMITTING_GEMS | State.TELEPORTING))
        self.at_spawn = self.spawn_ally.is_set(self.pos)
        self.moved = False
        self._enemies()
        self._camps()
        if self.enemies:
            self._allies()                      # only a fight needs their exact power (else the board's cells)

    def _enemies(self):
        for c in vision.champions(Team.ENEMY):
            try:
                e = Enemy()
                e.champion = c
                e.pos = c.position()
                e.x, e.y = e.pos.x, e.pos.y
                e.hp = c.health()
                e.max_hp = c.max_health()
                k = int(c.champion_name())
                name = kits.NAMES[k] if 0 <= k < len(kits.NAMES) else ""
                e.dmg, e.ehp = kits.power(name, c.strength(), c.focus(), e.hp)
                e.reach = max(1, c.attack_range())
                e.dist = max(abs(e.x - self.x), abs(e.y - self.y))
                e.gems = c.gem_count()
                e.on_spawn = self.spawn_enemy.is_set(e.pos)
            except Exception:
                continue
            self.enemies.append(e)
        self.enemies.sort(key=lambda e: e.dist)

    def _allies(self):
        for c in vision.champions(Team.ALLY):
            if int(c) == self.handle:
                continue
            try:
                p = c.position()
                k = int(c.champion_name())
                name = kits.NAMES[k] if 0 <= k < len(kits.NAMES) else ""
                hp = c.health()
                dmg, ehp = kits.power(name, c.strength(), c.focus(), hp)
            except Exception:
                continue
            self.allies.append((p.x, p.y, hp, dmg, ehp))

    def _camps(self):
        """Visible living camps (camp, group, x, y, health), the awake board, and the state of the
        groups near enough to be in sight."""
        lookup = self.camp_at
        seen, count = {}, {}
        for p in vision.camp_tiles().positions():
            entry = lookup.get((p.x, p.y))
            if entry is None:
                continue
            camp, gi = entry
            try:
                hp = camp.health()
            except Exception:
                continue
            if hp > 0:
                self.camps.append((camp, gi, p.x, p.y, hp))
                seen[gi] = seen.get(gi, 0.0) + hp
                count[gi] = count.get(gi, 0) + 1
        for gi, hp in seen.items():
            g = self.groups[gi]
            if count[gi] == len(g.camps):
                g.health_left = hp
        self.awake = vision.awake_camp_tiles()
        x, y, rnd = self.x, self.y, self.round
        job = self.job_group
        sweep = rnd % GROUP_SWEEP_EVERY == self.slot % GROUP_SWEEP_EVERY
        for g in self.groups:
            if g is not job and not sweep:
                continue
            if abs(g.cx - x) > NEAR_GROUP or abs(g.cy - y) > NEAR_GROUP:
                continue
            try:
                s = g.group.state()
            except Exception:
                continue
            g.seen = rnd
            if isinstance(s, game_map.CampGroupRespawning):
                g.state, g.timer, g.back_round = RESPAWNING, s.timer, rnd + s.timer
                g.health_left = g.health
            elif isinstance(s, game_map.CampGroupRewardAvailable):
                g.state = REWARD
                try:
                    g.capture = int(g.group.capture_state().state)
                except Exception:
                    g.capture = None
            else:
                g.state, g.back_round = ALIVE, -1

    # ---- helpers ---------------------------------------------------------------------
    def power_balance(self, radius=6):
        """Our side's calibrated kit power over theirs within radius of us (large with no enemy)."""
        theirs = 0.0
        for e in self.enemies:
            if e.dist <= radius and not e.on_spawn:
                theirs += e.dmg * e.ehp
        if theirs <= 0:
            return 99.0
        ours = self.dmg * self.ehp
        for ax, ay, hp, dmg, ehp in self.allies:
            if max(abs(ax - self.x), abs(ay - self.y)) <= radius:
                ours += dmg * ehp
        return config.POWER_CALIBRATION * ours / theirs

    def rounds_left(self):
        return self.max_rounds - self.round


class Ability:
    __slots__ = ("ability", "slot", "range", "mult", "exclusive", "camps", "enemies", "allies", "ground",
                 "self_cast", "reset", "dash", "shield", "heal", "stun", "damage", "aoe")

    def __init__(self, ab):
        self.ability = ab
        info = ab.info()
        self.slot = ab.slot()
        self.range = int(getattr(info, "range", 0) or 0)
        self.mult = float(getattr(info, "modifier", 0.0) or 0.0)
        self.exclusive = bool(getattr(info, "exclusive_with_movement", False))
        self.camps = ab.can_target_camps()
        self.enemies = ab.can_target_enemies()
        self.allies = ab.can_target_allies()
        self.ground = ab.can_target_ground()
        self.self_cast = ab.can_target_self()
        self.reset = ab.is_attack_reset()
        self.dash = ab.is_dash()
        self.shield = ab.is_shield()
        self.heal = ab.is_heal()
        self.stun = ab.is_cc()
        self.damage = ab.is_damage() or ab.is_aoe_damage()
        self.aoe = ab.is_aoe_damage()

    def ready(self):
        try:
            return self.ability.cooldown() == 0
        except Exception:
            return False
