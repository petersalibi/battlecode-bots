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
            # Setup turns never act. Round one builds the map and the camp groups, and the rest too
            # unless the map has too many camps for one turn (budget.first_turn_fits), in which
            # case it finishes on round two. Which rounds those are depends only on the map.
            if w.groups is None:
                w.init_static()
            else:
                w.init_rest()
            if w.ready:
                self.nav = navigation.Navigator(w)
                self.act = actions.Actions(w, self.nav, self.d)
                print("init: %s slot %d/%d groups=%d map=%dx%d dps=%d init_fuel=%s squad=%s route=%s bank=%s ward=%s partners=%s%s" % (
                    w.kind_name, w.slot, w.n_bodies, len(w.groups), w.width, w.height, int(w.dps),
                    w.init_profile, w.squad, w.route, (w.bank_altar.x, w.bank_altar.y) if w.bank_altar else None,
                    (w.ward_spot.x, w.ward_spot.y) if w.ward_spot else None, w.partners,
                    (" plan=%s" % (w.plan,)) if config.LOG_GROUPS else ""), flush=True)
            return
        if not w.near_ready and w.setup_alone():
            w.finish_init()
            if w.fields_changed:
                w.fields_changed = False
                self.nav.fields.clear()
            return
        w.refresh()
        self.nav.turn_reset()
        after_refresh = fuel.remaining()

        if not w.alive:
            if self.d.was_alive:
                self.d.was_alive = False
                self.d.record_death(w.round)
            self.act.shop()
            w.board.publish(w.round, memory_codec.STATUS_BUSY, -1, w.pos, dead=True)
            self._log_board(w)
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
            self._publish(w, memory_codec.STATUS_BUSY, -1)
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
        self._publish(w, self.act.status, self.act.claim_group)
        self._stall_guard(mode)
        if config.MACRO_WARDS and not w.channeling and w.ward_spot is not None:
            self.act.ward_plan()
        elif config.WARD_OFTEN and not w.channeling:
            self.act.ward_often()
        if config.TURN_PRIORITY:
            self._set_priority(w, mode)

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
            print("r%d %s: %s hp=%d%% gems=%d gold=%d target=%d enemies=%d fuel_used=%d avg=%d max=%d camps=%s [%s] src=%s%s" % (
                w.round, w.kind_name, config.MODE_NAMES[mode], int(w.hp_ratio * 100), w.gems, w.gold, target,
                len(w.enemies), used, avg, self.fuel_max, "%s waited=%d" % (self.d.camp_source, w.fields_waited), self.worst, self.d.last_source,
                (" squad=%s" % (self.d.squad_stats,)) if config.SQUAD_LOG and w.round % config.LOG_EVERY == 0 else ""), flush=True)
        if not w.near_ready:
            # Leftover fuel builds the group anchors and vision masks, a few groups per turn, after
            # the turn's actions are safely done (a suspended turn loses its actions).
            w.finish_init()
            if w.fields_changed:
                w.fields_changed = False
                self.nav.fields.clear()

    def _publish(self, w, status, group):
        """End of a living turn: our claim or position to the board, and what we see of the enemy."""
        w.board.publish(w.round, status, group, w.pos)
        if w.enemies:
            target = self.act.focus_target
            if config.FOCUS_FIRE and target is not None:
                w.board.report_enemies(w.round, target.pos, len(w.enemies), focus=True)
            else:
                w.board.report_enemies(w.round, w.nearest_enemy.pos, len(w.enemies))
        self._log_board(w)

    def _log_board(self, w):
        if config.LOG_BOARD:
            # Free in release builds; tools/analysis/board_check.py compares it with the truth.
            cambc.debug_block(lambda: print(_board_line(w), flush=True))

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


def _board_line(w):
    """board r<round> s<slot> <x>,<y>|dead <w>x<h> then k:x,y,age per ally we hold a position for,
    and e:x,y,count,age for the sighting."""
    b = w.board
    parts = ["board r%d s%d %s %dx%d" % (w.round, w.slot, "%d,%d" % (w.pos.x, w.pos.y) if w.alive else "dead",
                                         w.width, w.height)]
    for k in range(min(w.n_bodies, memory_codec.SHARED_BODIES)):
        if k != w.slot and b.cells[k] is not None:
            parts.append("%d:%d,%d,%d" % (k, b.cells[k][0], b.cells[k][1], b.age(k, w.round)))
    if b.sighting is not None:
        (x, y), count, seen = b.sighting
        parts.append("e:%d,%d,%d,%d" % (x, y, count, w.round - seen))
    return " ".join(parts)
