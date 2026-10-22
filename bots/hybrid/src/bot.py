"""One champion's turn: refresh the world, choose a mode, act, publish a claim."""
import cambc

import actions
import candidates
import config
import datalog
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
        self.priority = 0             # the turn priority set last (set_priority persists)
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
            if fuel.remaining() < config.FIRST_TURN_QUIET:
                # Setup left almost nothing (boss battle, islands: 23-43K): even the two log lines
                # ran the turn out. Say nothing and end the turn; the next one finishes setup.
                return
            # The plan (22 map features, picks) only in the group log: formatting it on the first
            # turn cost the last 50K of a budget that big maps already spend to within 100K.
            print("init: %s slot %d/%d groups=%d map=%dx%d dps=%d budget=%d init_fuel=%d profile=%s squad=%s mid=%d%%%s" % (
                w.kind_name, w.slot, w.n_bodies, len(w.groups), w.width, w.height, int(w.dps),
                start_fuel, start_fuel - fuel.remaining(), w.init_profile, w.squad, int(100 * w.middle_share),
                (" plan=%s" % (w.plan,)) if config.LOG_GROUPS else ""), flush=True)
            if fuel.remaining() < config.FIRST_TURN_MIN + config.FIRST_TURN_PER_TILE * w.width * w.height:
                # A huge map: acting now would suspend the turn into the next round. Standing still
                # for one round is cheaper, and the next turn takes the cheap first-turn path.
                print("init: skipping the first turn's actions, %d fuel left" % fuel.remaining(), flush=True)
                if fuel.remaining() > config.FINISH_INIT_RESERVE:
                    w.finish_init()
                return
        w.refresh()
        self.nav.turn_reset()
        after_refresh = fuel.remaining()

        if not w.alive:
            if self.d.was_alive:
                self.d.was_alive = False
                self.d.record_death(w.round)
            self.act.shop()
            memory_codec.write_claim(w.slot, memory_codec.STATUS_BUSY, -1)
            if config.TRACE_SLOT == w.slot:
                print("trace r%d dead gold=%d" % (w.round, w.gold), flush=True)
            return
        self.d.was_alive = True
        if w.channeling and config.NO_RECALL_IN_CONTACT and w.recalling and w.enemies and w.hp_ratio >= config.RECALL_CONTACT_HEALTH                 and w.nearest_enemy.dist <= config.RECALL_CONTACT_RANGE - 1:
            # An enemy walked up to a healthy body standing in a recall: stop it and play the turn.
            try:
                w.me.cancel_recall()
                w.refresh_action_flags()
            except Exception:
                pass
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
            prev_mode, prev_turns = self.d.mode, self.d.mode_turns
            mode = self.d.choose_mode(w, turn)
            if config.DATALOG:
                # Free in debug builds, never run in release or on the platform.
                cambc.debug_block(lambda: datalog.write(w, turn, self.d, mode, self.d.last_mask, self.d.last_scores,
                                                        prev_mode, prev_turns))
        after_decide = fuel.remaining()
        self.d.review_cost = 0
        if config.EARLY_ALERT and w.round <= config.EARLY_ROUNDS and w.enemies and w.nearest_enemy.dist <= config.ALERT_SIGHT:
            # The opening: our lone bodies died to one enemy that became three or four while we kept
            # farming. Call the team to the group we are working (byte 6, the party-capture call).
            g = self.d.target_group if self.d.target_group is not None else self.d.capture_group
            if g is not None and g.anchor is not None and w.pos.chebyshev_distance(g.anchor) <= config.ALERT_NEAR:
                memory_codec.write_contested(g.index, w.round)
        if config.CONTEST_SHARE and w.enemies and w.nearest_enemy.dist <= config.CONTEST_RANGE:
            g = self.d.capture_group if mode == config.CAPTURE else self.d.target_group
            if g is not None and g.anchor is not None and w.pos.chebyshev_distance(g.anchor) <= config.CONTEST_NEAR:
                memory_codec.write_contested(g.index, w.round)
        self.act.run(mode, turn)
        memory_codec.write_claim(w.slot, self.act.status, self.act.claim_group)
        self._stall_guard(mode)
        if config.WARD_OFTEN and not w.channeling:
            self.act.ward_often()
        if config.TURN_PRIORITY:
            self._set_priority(w, mode)

        now = fuel.remaining()
        used = start_fuel - now
        if after_decide - now - self.d.review_cost > self.d.act_peak:
            # The network's reserve must cover the worst act so far; the camp review has its own
            # guard and never shares a turn with a retarget, so its cost stays out.
            self.d.act_peak = after_decide - now - self.d.review_cost
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
            print("r%d %s: %s hp=%d%% gems=%d gold=%d target=%d enemies=%d fuel_used=%d avg=%d max=%d retarget=%d/%s [%s] src=%s%s" % (
                w.round, w.kind_name, config.MODE_NAMES[mode], int(w.hp_ratio * 100), w.gems, w.gold, target,
                len(w.enemies), used, avg, self.fuel_max, self.d.retarget_peak, self.d.camp_source, self.worst, self.d.last_source,
                (" squad=%s" % (self.d.squad_stats,)) if config.SQUAD_LOG and w.round % config.LOG_EVERY == 0 else ""), flush=True)
        if not w.near_ready:
            # Leftover fuel builds the group anchors and vision masks, a few groups per turn, after
            # the turn's actions are safely done (a suspended turn loses its actions).
            w.finish_init()
            if w.fields_changed:
                w.fields_changed = False
                self.nav.fields.clear()

    def _set_priority(self, w, mode):
        """Turn order within the team (set_priority applies from next round and persists): a body
        facing an enemy acts before the rest, the nearest and the most hurt first, so it hits or
        escapes before teammates move around it. One engine call, only when the value changes."""
        p = 0
        if w.enemies:
            dist = w.nearest_enemy.dist
            if dist <= config.PRIORITY_ENEMY_RANGE:
                p = 50 + 4 * (config.PRIORITY_ENEMY_RANGE - dist) + int(20 * (1.0 - w.hp_ratio))
                if mode == config.FLEE or mode == config.RECALL:
                    p += 10
        if p != self.priority:
            try:
                w.me.set_priority(p)
                self.priority = p
            except Exception:
                self.priority = p

    def _stall_guard(self, mode):
        """A body that neither moves nor acts for STUCK_TURNS turns, without meaning to wait, has
        hit a pathing hole. Drop its target, shun that group for a while and log it, so the harness
        can count stalls instead of them silently costing a game."""
        w, d, act = self.w, self.d, self.act
        passive = (not act.acted and not act.waiting and not w.at_spawn and not w.waiting_dash
                   and mode in (config.FARM, config.CAPTURE, config.TRIBUTE, config.FLEE))
        idle = passive and not w.moved
        d.idle_turns = d.idle_turns + 1 if idle else 0
        # Pacing between two tiles without ever acting is the same hole with a step in it.
        if passive:
            key = (w.pos.x, w.pos.y)
            if key in d.osc_tiles or len(d.osc_tiles) < 2:
                d.osc_tiles.add(key)
                d.osc_turns += 1
            else:
                d.osc_tiles = {key}
                d.osc_turns = 1
        else:
            d.osc_tiles = set()
            d.osc_turns = 0
        if d.idle_turns < config.STUCK_TURNS and d.osc_turns < config.OSCILLATION_TURNS:
            return
        g = d.capture_group if mode == config.CAPTURE else d.target_group
        print("r%d %s: stuck %d turns in %s at %d,%d gems=%d target=%d%s" % (
            w.round, w.kind_name, max(d.idle_turns, d.osc_turns), config.MODE_NAMES[mode], w.pos.x, w.pos.y, w.gems,
            g.index if g is not None else -1, " pacing" if d.idle_turns < config.STUCK_TURNS else ""), flush=True)
        d.osc_tiles = set()
        d.osc_turns = 0
        if g is not None:
            g.avoid_until = w.round + config.STUCK_AVOID_ROUNDS
        d.target_group = None
        d.capture_group = None
        d.camp_cache = None
        d.idle_turns = 0
