"""One champion's turn: refresh the world, choose a mode, act, publish a claim."""
import cambc

import actions
import candidates
import config
import decide
import fuel
import memory_codec
import navigation
import world


class Bot:
    def __init__(self):
        self.w = world.World()
        self.d = decide.Decider()
        self.nav = None
        self.act = None
        self.last_logged_mode = None
        self.fuel_total = 0
        self.fuel_max = 0
        self.turns = 0
        self.worst = ""

    def play_turn(self):
        w = self.w
        start_fuel = fuel.remaining()
        if not w.ready:
            w.init_static()
            self.nav = navigation.Navigator(w)
            self.act = actions.Actions(w, self.nav, self.d)
            print("init: %s slot %d/%d groups=%d map=%dx%d dps=%d plan=%s budget=%d init_fuel=%d profile=%s" % (
                w.kind_name, w.slot, w.n_bodies, len(w.groups), w.width, w.height, int(w.dps), w.plan,
                start_fuel, start_fuel - fuel.remaining(), w.init_profile), flush=True)
            if fuel.remaining() < config.FIRST_TURN_MIN + config.FIRST_TURN_PER_TILE * w.width * w.height:
                # A huge map: acting now would suspend the turn into the next round. Standing still
                # for one round is cheaper, and the next turn takes the cheap first-turn path.
                print("init: skipping the first turn's actions, %d fuel left" % fuel.remaining(), flush=True)
                w.finish_init()
                return
        w.refresh()
        self.nav.turn_reset()
        after_refresh = fuel.remaining()

        if not w.alive:
            self.act.shop()
            memory_codec.write_claim(w.slot, memory_codec.STATUS_BUSY, -1)
            if config.TRACE_SLOT == w.slot:
                print("trace r%d dead gold=%d" % (w.round, w.gold), flush=True)
            return
        if w.channeling:
            # Recall, tribute or teleport in progress: leave it running.
            memory_codec.write_claim(w.slot, memory_codec.STATUS_BUSY, -1)
            if config.TRACE_SLOT == w.slot:
                print("trace r%d channeling state=%d hp=%d gems=%d pos=%d,%d" % (
                    w.round, int(w.me.state()), int(w.hp), w.gems, w.pos.x, w.pos.y), flush=True)
            return
        if w.at_spawn:
            self.act.shop()

        turn = candidates.Turn(w, self.nav, self.d)
        if self.turns == 0:
            # First turn: setup already cost most of the budget. Farm the nearest sensible camp
            # without ranking every candidate.
            mode = self.d.mode = config.FARM
            g = w.first_target(w.claims)
            if g is not None:
                self.d.target_group, self.d.target_state, self.d.target_round = g, g.state, w.round
        else:
            mode = self.d.choose_mode(w, turn)
        after_decide = fuel.remaining()
        self.act.run(mode, turn)
        memory_codec.write_claim(w.slot, self.act.status, self.act.claim_group)
        self._stall_guard(mode)

        now = fuel.remaining()
        used = start_fuel - now
        if config.TRACE_SLOT == w.slot:
            target = self.d.target_group
            raw = len(cambc.vision.camps())
            print("trace r%d %s pos=%d,%d hp=%d gems=%d tgt=%s st=%s moved=%d %s rawcamps=%d" % (
                w.round, config.MODE_NAMES[mode], w.pos.x, w.pos.y, int(w.hp), w.gems,
                target.index if target else -1, target.state if target else -1, int(w.moved), self.act.trace, raw),
                flush=True)
        if w.round > 1:
            self.fuel_total += used
            self.turns += 1
            if used > self.fuel_max:
                self.fuel_max = used
                self.worst = "r%d %s refresh=%d decide=%d act=%d enemies=%d" % (
                    w.round, config.MODE_NAMES[mode], start_fuel - after_refresh, after_refresh - after_decide,
                    after_decide - now, len(w.enemies))
        elif self.turns == 0:
            self.turns = 1                      # the first turn counts for finish_init scheduling
        if config.PROFILE_EVERY and w.round % config.PROFILE_EVERY == 0:
            print("prof r%d %s: refresh=%d decide=%d act=%d refresh_parts=%s" % (
                w.round, w.kind_name, start_fuel - after_refresh, after_refresh - after_decide, after_decide - now,
                w.refresh_profile), flush=True)
        if mode != self.last_logged_mode or (config.LOG_EVERY and w.round % config.LOG_EVERY == 0):
            self.last_logged_mode = mode
            target = self.d.target_group.index if self.d.target_group is not None else -1
            avg = self.fuel_total // self.turns if self.turns else 0
            print("r%d %s: %s hp=%d%% gems=%d gold=%d target=%d enemies=%d fuel_used=%d avg=%d max=%d [%s]" % (
                w.round, w.kind_name, config.MODE_NAMES[mode], int(w.hp_ratio * 100), w.gems, w.gold, target,
                len(w.enemies), used, avg, self.fuel_max, self.worst), flush=True)
        if not w.near_ready:
            # Leftover fuel builds the group anchors and vision masks, a few groups per turn, after
            # the turn's actions are safely done (a suspended turn loses its actions).
            w.finish_init()

    def _stall_guard(self, mode):
        """A body that neither moves nor acts for STUCK_TURNS turns, without meaning to wait, has
        hit a pathing hole. Drop its target, shun that group for a while and log it, so the harness
        can count stalls instead of them silently costing a game."""
        w, d, act = self.w, self.d, self.act
        idle = (not w.moved and not act.acted and not act.waiting and not w.at_spawn
                and mode in (config.FARM, config.CAPTURE, config.TRIBUTE))
        d.idle_turns = d.idle_turns + 1 if idle else 0
        if d.idle_turns < config.STUCK_TURNS:
            return
        g = d.capture_group if mode == config.CAPTURE else d.target_group
        print("r%d %s: stuck %d turns in %s at %d,%d gems=%d target=%d" % (
            w.round, w.kind_name, d.idle_turns, config.MODE_NAMES[mode], w.pos.x, w.pos.y, w.gems,
            g.index if g is not None else -1), flush=True)
        if g is not None:
            g.avoid_until = w.round + config.STUCK_AVOID_ROUNDS
        d.target_group = None
        d.capture_group = None
        d.camp_cache = None
        d.idle_turns = 0
