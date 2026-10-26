"""One body's turn: perceive, review the job on an event, one tactical evaluation, publish."""
import _cambc

import board
import config
import market as M
import paths
import tactics
import world

_fuel = _cambc.fuel_remaining            # logs only: nothing branches on it


class Bot:
    def __init__(self):
        self.w = world.World()
        self.paths = self.market = self.tactics = None
        self.was_alive = True
        self.priority = 0
        self.fuel_total = self.fuel_turns = self.fuel_max = 0
        self.stats = {}                  # turns per job kind and outcome counters (logs only)
        self.prev_gems = 0

    def play_turn(self):
        w = self.w
        start = _fuel()
        if not w.ready:
            # Setup never acts: round one builds the groups, then one fixed step a turn.
            if not hasattr(w, "groups"):
                w.init_static()
            else:
                w.setup_step()
            if w.ready:
                self.paths = paths.Paths(w)
                self.market = M.Market(w, self.paths)
                self.tactics = tactics.Tactics(w, self.paths, self.market)
                print("init: %s slot %d/%d groups=%d map=%dx%d plan=%s" % (
                    w.kind, w.slot, w.n_bodies, len(w.groups), w.width, w.height,
                    "%d groups k=%d" % (w.plan.n, w.plan.k) if w.plan.ok else "none"), flush=True)
                if config.TRACE_SLOT == w.slot:
                    for g in w.groups:
                        print("  g%d at %d,%d camps=%d gems=%d gold=%d hp=%d str=%d reach=%d spawn=%s side=%s" % (
                            g.index, g.cx, g.cy, len(g.camps), g.gems, g.gold, g.health, g.strength, g.reach,
                            w.plan.spawn[g.index] if w.plan.ok else "-", w.plan.side[g.index] if w.plan.ok else "-"),
                            flush=True)
            return
        self.paths.turn_reset()
        t0 = _fuel()
        w.refresh()
        self.t_refresh = t0 - _fuel()
        if not w.alive:
            if self.was_alive:
                self.was_alive = False
                w.deaths += 1
                job = self.market.job
                if job is not None and job.group is not None:
                    job.group.deaths.append(w.round)
                print("r%d s%d %s: died (job %r)" % (w.round, w.slot, w.kind, job), flush=True)
                self.market.job = None
            tactics.shop(w)
            w.board.publish(w.round, board.AWAY, -1, None, dead=True)
            return
        self.was_alive = True
        if w.channeling:
            kind, group = self.market.claim()
            w.board.publish(w.round, kind, group, w.pos)
            return
        if w.at_spawn:
            tactics.shop(w)
        why = self.market.needs_review()
        if why is not None:
            self.market.choose()
        w.job_group = self.market.job.group
        self.tactics.run()
        st = self.stats
        job = self.market.job
        key = job.kind if job is not None else "none"
        st[key] = st.get(key, 0) + 1
        if self.tactics.attacked:
            st["hit"] = st.get("hit", 0) + 1
        if "hold" in self.tactics.trace:
            st["hold"] = st.get("hold", 0) + 1
        if w.gems > self.prev_gems:
            st["gathered"] = st.get("gathered", 0) + w.gems - self.prev_gems
        elif w.gems < self.prev_gems:
            st["banked"] = st.get("banked", 0) + self.prev_gems - w.gems
        self.prev_gems = w.gems
        if w.round % 500 == 0:
            print("stats r%d s%d %s: %s deaths=%d" % (w.round, w.slot, w.kind, sorted(st.items()), w.deaths), flush=True)
        kind, group = self.market.claim()
        w.board.publish(w.round, kind, group, w.pos)
        if w.enemies:
            w.board.report(w.round, w.enemies[0].pos, len(w.enemies))
        self._priority()
        used = start - _fuel()
        if config.TRACE_SLOT == w.slot and self.market.job is not None:
            job = self.market.job
            f = None
            if job.kind == M.FARM:
                f = self.paths.fields.get(("g", job.group.index)) if job.group.state != world.REWARD else                     self.paths.fields.get(("c", job.group.index))
            print("t r%d %d,%d %r st=%s fld=%s hp=%d camps=%d enemies=%d att=%s cast=%s%s fuel=%d refresh=%d prof=%s" % (
                w.round, w.x, w.y, job, job.group.state if job.group is not None else "-",
                self.paths.get(f, w.pos) if f is not None else None, int(w.hp),
                len(w.camps), len(w.enemies), self.tactics.attacked, self.tactics.cast, self.tactics.trace, used,
                self.t_refresh, None),
                flush=True)
        self.fuel_total += used
        self.fuel_turns += 1
        self.fuel_max = max(self.fuel_max, used)
        if config.LOG_EVERY and w.round % config.LOG_EVERY == 0:
            print("r%d s%d %s: %r hp=%d%% gems=%d gold=%d deaths=%d fuel avg=%d max=%d" % (
                w.round, w.slot, w.kind, self.market.job, int(100 * w.hp_ratio), w.gems, w.gold, w.deaths,
                self.fuel_total // max(1, self.fuel_turns), self.fuel_max), flush=True)

    def _priority(self):
        """Within the team, a body facing an enemy acts first, the nearest and the most hurt first
        (v13: with the builds, the biggest single gain hybrid had)."""
        w = self.w
        p = 0
        if w.enemies and w.enemies[0].dist <= 6:
            p = 50 + 4 * (6 - w.enemies[0].dist) + int(20 * (1.0 - w.hp_ratio))
        if p != self.priority:
            try:
                w.me.set_priority(p)
            except Exception:
                pass
            self.priority = p
