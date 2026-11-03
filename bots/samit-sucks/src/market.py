"""Jobs: which group to farm, when to bank, when to go home. Chosen by reward per turn, and only
when something happened (DESIGN.md: no per-turn re-ranking).

A FARM job is clear-and-capture of one group. Its price: the group's value over the turns it
takes (walk + wait for a respawn + clear + capture), discounted by what we know of the danger
there. Walks come from the draft's distance table (plan.py), so pricing every group costs no field.
"""
import cambc

import board
import config
from world import REWARD, RESPAWNING

FARM, BANK, HOME, EVADE = "farm", "bank", "home", "evade"
CAPTURE_TURNS = 5


class Job:
    __slots__ = ("kind", "group", "altar", "since", "bodies", "score", "why", "wait", "close", "waiting")

    def __init__(self, kind, since, group=None, altar=None, bodies=1, score=0.0, why=""):
        self.kind, self.since, self.group, self.altar = kind, since, group, altar
        self.bodies, self.score, self.why = bodies, score, why
        self.wait = 0                     # turns spent held off a contested altar
        self.close = False                # ranged: our camps were out of sight from range, so stand beside them
        self.waiting = False              # taken while the group was respawning: we came to wait for it

    def __repr__(self):
        target = ("g%d" % self.group.index) if self.group is not None else (
            "%d,%d" % (self.altar.x, self.altar.y) if self.altar is not None else "-")
        return "%s %s" % (self.kind, target)


class Market:
    def __init__(self, w, paths):
        self.w = w
        self.paths = paths
        self.job = None
        self.unreachable = {}            # group -> round our own field could not reach it (forgotten after a while)
        self.bad_altars = {}             # altar (x, y) -> round our own field could not reach it
        self.hold_start = {}             # group -> round we began holding beside it for the party
        self.last_group = None          # the group we last stood beside (the table's origin)

    # ---- pricing ------------------------------------------------------------------
    def travel(self, g):
        """Walking turns from here to group g: the job field when we have it, else the table."""
        w = self.w
        f = self.paths.fields.get(("g", g.index, w.attack_range))
        if f is not None:
            return self.paths.get(f, w.pos)
        plan = w.plan
        cheb = max(abs(g.cx - w.x), abs(g.cy - w.y))
        best = int(cheb * 1.3) + 1
        if plan.ok and g.index < plan.n:
            if w.at_spawn and plan.spawn[g.index] < 255:
                return plan.spawn[g.index]
            h = self.last_group
            if h is not None and h.index < plan.n:
                t = plan.turns(h.index, g.index)
                if t is not None:
                    best = min(best, t + max(abs(h.cx - w.x), abs(h.cy - w.y)))
        return best

    def price(self, g, others):
        """(reward per turn, bodies planned) of farming g when `others` of us already farm it.

        Camps do not heal until they respawn, so a group too strong for one trip is cleared over
        several, each extra trip a walk home to heal and back. With b bodies the clear is b times
        faster and its damage is split b ways, so each body soaks load / b^2: the fewest bodies
        that keep that within FARM_MAX_TRIPS trips are planned, the ones not yet there discounted
        (PARTY_DISCOUNT). A group already farmed by enough of us is not for us (0)."""
        w = self.w
        if w.round - self.unreachable.get(g.index, -10000) < config.UNREACHABLE_FORGET:
            return 0.0, 1
        if not w.has_dash and w.plan.ok and g.index < w.plan.n and w.plan.spawn[g.index] == 255:
            return 0.0, 1                                   # walled off from walkers (the draft's table)
        travel = self.travel(g)
        if travel > config.FARM_TRAVEL_MAX:
            return 0.0, 1
        wait = 0
        if g.state == RESPAWNING:
            wait = max(0, g.back_round - w.round - travel)
        clear = 0
        trips = 1
        needed = 1
        if g.state != REWARD:
            dps = max(1.0, w.dmg)
            budget = max(1.0, w.max_hp * (1.0 - config.HEALTH_RESERVE))
            load = config.CAMP_DAMAGE_SHARE * g.strength * g.health_left / dps
            party_max = min(w.n_bodies, config.MAX_PARTY)
            while needed < party_max and 1 + int(load / (needed * needed * budget)) > config.FARM_MAX_TRIPS:
                needed += 1
            trips = 1 + int(load / (needed * needed * budget))
            if trips > config.FARM_MAX_TRIPS:
                return 0.0, needed                          # not even the whole party
        if others >= needed:
            return 0.0, needed                              # enough of us there already
        bodies = max(needed, others + 1)
        if g.state != REWARD:
            clear = int(g.health_left / (max(1.0, w.dmg) * bodies)) + 1
            trips = 1 + int(config.CAMP_DAMAGE_SHARE * g.strength * clear / bodies
                            / max(1.0, w.max_hp * (1.0 - config.HEALTH_RESERVE)))
        home = w.plan.spawn[g.index] if w.plan.ok and g.index < w.plan.n and w.plan.spawn[g.index] < 255 else travel
        turns = travel + wait + clear + CAPTURE_TURNS + (trips - 1) * (2 * min(home, 5 + home // 2) + 2)
        score = g.value / bodies / max(1, turns) * config.PARTY_DISCOUNT ** (bodies - 1 - others)
        recent = [r for r in g.deaths if w.round - r <= config.SURVIVAL_DEATH_ROUNDS]
        score *= config.SURVIVAL_PER_DEATH ** len(recent)
        s = w.board.sighting
        if s is not None and w.round - s[2] <= config.SIGHTING_FRESH * 2 and bodies == 1:
            if max(abs(s[0][0] - g.cx), abs(s[0][1] - g.cy)) <= config.SIGHTING_NEAR:
                score *= config.SIGHTING_FACTOR
        if g.capture is not None and g.capture in (2, 3) and g.state == REWARD:
            score *= 0.3                                    # contested or the enemy is capturing it
        return score, bodies

    def best_farm(self):
        """(group, score, bodies): the best farm for us, joining a party when a group needs one.

        A group too strong for one body (more heal trips than FARM_MAX_TRIPS) is priced for the
        fewest bodies that make it feasible, each extra body discounted (PARTY_DISCOUNT: the
        others must come). A group others already farm is joined only when it still needs us."""
        w = self.w
        claimed = {}
        for k, (kind, gi) in enumerate(w.claims):
            if k != w.slot and k < w.n_bodies and kind in (board.FARM, board.HELP) and gi >= 0:
                claimed[gi] = claimed.get(gi, 0) + 1
        # Walking is never shorter than the straight line, so value / (Chebyshev + capture) bounds
        # a group's score: price the best bounds first and stop once none can win (~50K a group
        # priced; lanes has 42).
        x, y = w.x, w.y
        current = self.job.group if self.job is not None and self.job.kind == FARM else None
        # The clear takes at least health / (our damage x the biggest party), split that many ways.
        party = min(w.n_bodies, config.MAX_PARTY)
        dps = max(1.0, w.dmg) * party
        order = []
        for g in w.groups:
            if g.value > 0:
                clear = g.health_left / dps if g.state != REWARD else 0.0
                order.append((g.value / (max(abs(g.cx - x), abs(g.cy - y)) + CAPTURE_TURNS + clear), g))
        order.sort(key=lambda t: -t[0])
        best, best_score, best_bodies = None, 0.0, 1
        self.priced = 0
        if current is not None:
            order = [t for t in order if t[1] is current] + [t for t in order if t[1] is not current]
        for bound, g in order:
            if bound * config.JOB_STICKINESS <= best_score or self.priced >= config.MAX_PRICED:
                break                                 # a fixed budget: the best bounds only
            self.priced += 1
            score, bodies = self.price(g, claimed.get(g.index, 0))
            if g is current:
                score *= config.JOB_STICKINESS        # finish what we started: a switch walks away from it
            if score > best_score:
                best, best_score, best_bodies = g, score, bodies
        return best, best_score, best_bodies

    def best_altar(self):
        """(altar, walking turns): the job field's exact turns when built, else the draft's
        table from the group we stand by, else the straight line; altars we found walled off
        are skipped."""
        w = self.w
        plan = w.plan
        h = self.last_group
        table = None
        if plan.ok and h is not None and h.index < plan.n and plan.altar[h.index] < len(w.altars)                 and max(abs(h.cx - w.x), abs(h.cy - w.y)) <= 4:
            table = (plan.altar[h.index], plan.altar_turns[h.index])
        best, best_t = None, None
        for i, a in enumerate(w.altars):
            if w.round - self.bad_altars.get((a.x, a.y), -10000) < config.UNREACHABLE_FORGET:
                continue
            f = self.paths.fields.get(("a", a.x, a.y))
            if f is not None:
                t = self.paths.get(f, w.pos)
            elif table is not None and table[0] == i and table[1] < 255:
                t = table[1]
            else:
                t = int(max(abs(a.x - w.x), abs(a.y - w.y)) * 1.4) + 2
            if best_t is None or t < best_t:
                best, best_t = a, t
        return best, best_t

    # ---- events -------------------------------------------------------------------
    def _can_finish(self, g):
        """Hurt beside a group that is nearly dead: the damage its remaining camps can still do
        before we finish them fits in what we have left (a heal trip now would waste the clear)."""
        w = self.w
        if g.state == REWARD:
            return not w.enemies                       # standing on our capture
        if g.health <= 0 or max(abs(g.cx - w.x), abs(g.cy - w.y)) > g.reach + 2:
            return False
        left = g.health_left / g.health
        turns = g.health_left / max(1.0, w.dmg) + 1
        return config.CAMP_DAMAGE_SHARE * g.strength * left * turns < w.hp - config.FINISH_RESERVE * w.max_hp

    def threatened(self):
        """A stronger enemy side within a few tiles of its reach: leave the job (turning away early
        is what the top teams do; the guard step was what cut deaths in phase 3b)."""
        w = self.w
        if w.at_spawn:
            return False
        near = False
        for e in w.enemies:
            if not e.on_spawn and e.dist <= e.reach + config.EVADE_MARGIN:
                near = True
                break
        return near and w.power_balance() < config.FIGHT_BALANCE

    def needs_review(self):
        w, job = self.w, self.job
        if job is None:
            return "none"
        if job.kind == EVADE:
            if not any(e.dist <= config.EVADE_CLEAR and not e.on_spawn for e in w.enemies):
                return "clear"
            return None
        if w.round - job.since >= config.JOB_REVIEW_EVERY:
            return "review"
        if job.kind != HOME and self.threatened():
            return "threat"
        hurt = w.hp_ratio < config.HOME_HEALTH and not w.at_spawn
        if job.kind == FARM:
            g = job.group
            if g.state == RESPAWNING and not job.waiting and g.back_round - w.round > self.travel(g) + 5:
                return "done"
            if hurt and not self._can_finish(g):
                return "hurt"
            if w.gems >= config.BANK_FULL_BAG:
                return "bag"
        elif job.kind == BANK:
            if w.gems == 0:
                return "banked"
            if hurt and max(abs(job.altar.x - w.x), abs(job.altar.y - w.y)) > 5:
                return "hurt"
        elif job.kind == HOME:
            if w.at_spawn and w.hp_ratio >= 0.95 and (job.why != "idle" or w.round - job.since >= 10):
                return "healed"
        return None

    def choose(self):
        """A new job for this body. Returns it (also kept in self.job)."""
        w = self.w
        rnd = w.round
        import _cambc
        f0 = _cambc.fuel_remaining()
        g, score, bodies = self.best_farm()
        self.prof = (f0 - _cambc.fuel_remaining(), self.priced, len(w.groups))
        hurt = w.hp_ratio < config.HOME_HEALTH
        if not w.at_spawn and self.threatened():
            return self._set(Job(EVADE, rnd, why="%d enemies" % len(w.enemies)))
        if not w.at_spawn and (hurt or (w.gold >= config.HOME_SHOP_GOLD and w.gems == 0 and g is None)):
            return self._set(Job(HOME, rnd, why="hurt" if hurt else "shop"))
        if w.at_spawn and w.hp_ratio < 0.95:
            return self._set(Job(HOME, rnd, why="heal"))
        # Bank when the bag risks more than the trip costs.
        if w.gems >= config.BANK_MIN_BAG:
            altar, turns = self.best_altar()
            if altar is not None:
                trip = turns + (w.gems + 9) // 10
                risk = w.gems * config.BANK_RISK_PER_TURN * max(60, trip)
                endgame = w.rounds_left() <= trip + config.BANK_ENDGAME_MARGIN
                winning = w.gems + _team_banked(w) >= w.winning_gems
                if (w.gems >= config.BANK_FULL_BAG or endgame or winning or g is None
                        or risk >= trip * score):
                    return self._set(Job(BANK, rnd, altar=altar, why="bag %d" % w.gems))
        if g is not None:
            job = Job(FARM, rnd, group=g, bodies=bodies, score=score)
            job.waiting = g.state == RESPAWNING
            return self._set(job)
        return self._set(Job(HOME, rnd, why="idle"))

    def _set(self, job):
        old = self.job
        self.job = job
        if old is None or old.group is not job.group:
            self.hold_start = {}                     # a new group: its party hold starts afresh
        if config.LOG_JOBS and (old is None or old.kind != job.kind or old.group is not job.group):
            print("r%d s%d %s: %r%s score=%.3f" % (self.w.round, self.w.slot, self.w.kind, job,
                                                  (" (" + job.why + ")") if job.why else "", job.score), flush=True)
        return job

    def claim(self):
        """(board kind, group) to publish for the current job."""
        job = self.job
        if job is None or not self.w.alive:
            return board.AWAY, -1
        if job.kind == FARM:
            return (board.HELP if job.bodies > 1 else board.FARM), job.group.index
        return board.AWAY, -1


def _team_banked(w):
    try:
        return cambc.global_submitted_gems(cambc.Team.ALLY)
    except Exception:
        return 0
