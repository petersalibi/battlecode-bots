"""Every decision as a scoring function.

Each chooser has a scripted scorer and an optional network behind the same interface. The
network only ranks options the script generated, and masks are applied before any argmax, so a
network can never choose an illegal action. When a head is off, has no weights, is short of fuel,
or is unsure, the script decides.
"""
import camp_features
import config
import fight_features
import datalog
import features
import fuel
import nn
import weights
import weights_adv
from config import FARM, CAPTURE, TRIBUTE, RECALL, FIGHT, FLEE, N_MODES

MODE_SELECTOR_FUEL = 500_000      # features (reusing the script's camp scores) plus one 66-32-16-6 forward


class Decider:
    def __init__(self):
        self.mode = FARM
        self.mode_turns = 0
        self.target_group = None      # Group being farmed
        self.act_peak = 0             # most fuel acting has cost in one turn (bot.py), for the network's reserve
        self.retarget_peak = 0        # most fuel one camp re-rank has cost (actions.farm)
        self.target_round = -1000
        self.target_state = None      # the group's state when it was chosen
        self.target_bodies = 1        # champions the plan needs on the target group
        self.hold_since = -1          # round we started waiting for allies, or -1
        self.capture_group = None
        self.capture_since = -1       # round the current capture started (standoff detection)
        self.last_scores = None
        self.last_source = "script"
        self.camp_cache = None
        self.last_mask = None
        self.camp_cache_round = -1000
        self.cache_scored_round = -2000
        self.retargets = 0
        self.idle_turns = 0           # consecutive turns without a move or an action (bot.py)
        self.deaths = {}              # group index -> rounds this body died while targeting it
        self.camp_review_round = -1   # round the camp scorer reviews the last ranking (actions.farm)
        self.review_cost = 0          # fuel the camp review cost this turn (kept out of act_peak)
        self.net_target = None        # group index the scorer chose; the script keeps it while it is a candidate
        self.was_alive = True
        self.camp_source = "script"   # who ranked the camps at the last retarget
        self.gated_since = -1         # round the spawn gate started holding this body, or -1
        self.helping_group = None     # the group whose call for help this body answered
        self.chase_handle = None      # the weak enemy this body is running down, and until when
        self.chase_until = -1
        self.regroup_pos = None       # opening: the ally tile this body is stepping towards, and until when
        self.regroup_until = -1
        self.enemy_seen_round = -100  # last round an enemy was in sight (the first sighting starts a regroup)
        self.explore_mode = FARM      # self-play exploration: the mode being held and until when
        self.explore_until = -1
        self.risk_score = None        # the selector's death-risk lane this turn (None when it did not run)
        self.risk_fleeing = False     # fleeing on the risk lane: stays until the lane drops below the lower threshold
        self.escape_source = "script" # who chose the last flee step
        self.fight_source = "script"  # who chose the last fight target
        self.breakout_round = -1      # round this body joined a break-out

    def record_death(self, round_number):
        """Called on the first dead turn: remember where we were going (camp_features reads it)."""
        g = self.target_group if self.target_group is not None else self.capture_group
        if g is not None:
            rounds = self.deaths.setdefault(g.index, [])
            rounds.append(round_number)
            if len(rounds) > 8:
                del rounds[0]

    def camp_reserve(self, w):
        """The camp scorer runs inside the farm action, after the script's own ranking: what is
        left to pay for is at most one approach field (80 fuel per tile) plus a margin."""
        return config.FIELD_COST_PER_TILE * w.width * w.height + config.NN_ACT_MARGIN

    def nn_reserve(self, w):
        """Fuel that must remain after a network runs: acting afterwards may build a whole-map field
        (80 fuel per tile) or re-rank 28 groups (01yellow-brick-road: 780K), so the reserve is the
        larger of a map-sized estimate and a quarter more than the worst act seen this game."""
        return max(config.NN_FUEL_RESERVE + 3 * config.FIELD_COST_PER_TILE * w.width * w.height // 2
                   + config.NN_GROUP_RESERVE * max(0, len(w.groups) - config.NN_GROUP_FREE),
                   self.act_peak * 5 // 4 + config.NN_ACT_MARGIN)

    # ---- mode ----------------------------------------------------------------------
    def masks(self, w, turn):
        has_enemy = bool(w.enemies)
        return [
            True,                                                     # FARM always allowed
            bool(turn.captures()),                                    # CAPTURE
            w.gems > 0 and turn.altar() is not None,                  # TRIBUTE
            (w.at_spawn or w.damage_taken <= 0) and not self.recall_in_contact(w),   # RECALL (damage cancels it)
            has_enemy and bool(turn.fight_targets()),                 # FIGHT
            has_enemy or self.camp_hitting(w),                        # FLEE (from champions or camps)
        ]

    @staticmethod
    def recall_in_contact(w):
        """Band games: 14% of our contact turns were a body recalling at 94% health beside an enemy
        (mostly to shop): no attack, no cast, 55 damage a turn. Not with an enemy close and health
        to fight with."""
        return (config.NO_RECALL_IN_CONTACT and not w.at_spawn and w.enemies and w.hp_ratio >= config.RECALL_CONTACT_HEALTH
                and w.nearest_enemy.dist <= config.RECALL_CONTACT_RANGE)

    @staticmethod
    def camp_hitting(w):
        """A living camp has us in reach and we lost health this turn: it is awake and on us."""
        return w.camp_damage_incoming > 0 and w.damage_taken > 0

    def camp_overrun(self, w):
        """True when finishing the current camp fight would cost more health than we can spare:
        the camps in reach keep hitting us for as long as their remaining health takes to clear.
        More than half of all deaths were to camps, with no enemy in sight. Allies next to us
        share the clearing, so their strength counts towards the clear rate."""
        if not self.camp_hitting(w) or w.dps <= 0 or w.hp_ratio >= config.CAMP_FLEE_HEALTH:
            return False
        g = self.target_group
        remaining = 0.0
        for camp, gi, pos, hp in w.visible_camps:
            if hp > 0 and (g is None or gi == g.index) and pos.chebyshev_distance(w.pos) <= w.groups[gi].attack_range:
                remaining += hp
        if remaining <= 0:
            return False
        team_dps = w.dps + sum(strength for pos, hp, strength, dist in w.allies if dist <= config.HELP_WAIT_RANGE)
        turns = remaining / team_dps + 1
        return w.camp_damage_incoming * turns >= w.hp - config.CAMP_FLEE_RESERVE * w.max_hp

    def script_scores(self, w, turn):
        scores = [0.0] * N_MODES
        rounds_left = w.rounds_left()
        endgame = rounds_left <= config.ENDGAME_ROUNDS
        enemy_near = w.enemy_within(config.FLEE_ENEMY_RANGE)
        if config.FLEE_GROUP and len(w.enemies) >= 2 and w.enemy_within(config.FLEE_ENEMY_RANGE + config.FLEE_GROUP_EXTRA):
            enemy_near = True                                         # a group closes faster than one body
        balance = w.power_balance()

        # Farm: the best camp's value rate, scaled so a decent camp scores about 1.3.
        # Scores are reused while the candidate list itself is reused.
        camps = turn.camps()
        best = None
        rescore = turn.fresh_camps or self.cache_scored_round != self.camp_cache_round
        for cand in camps:
            if rescore:
                self.score_camp(cand, w)
            if cand.score > config.MIN_CAMP_SCORE and (best is None or cand.score > best.score):
                best = cand
        if rescore:
            self.cache_scored_round = self.camp_cache_round
        scores[FARM] = 0.6 + best.score if best is not None else 0.2
        if w.hp_ratio < config.RECALL_HEALTH:
            # Even a harmless camp is a death trap at low health: any enemy that finds us wins.
            scores[FARM] *= 0.5 if w.hp_ratio >= config.CAMP_MIN_HEALTH else 0.2
        if endgame and w.gems > 0 and best is not None and best.travel + best.clear_turns + 5 + w.turns_to_altar > rounds_left:
            scores[FARM] = 0.1                                        # it cannot be banked in time

        # Capture: a cleared group next to us is five rounds for its whole reward.
        if turn.captures():
            group, travel = turn.captures()[0]
            scores[CAPTURE] = 2.2 - 0.03 * travel

        # Tribute: bigger bags, nearer altars, low health and the endgame all push to bank.
        if w.gems > 0:
            altar = turn.altar()
            turns = altar[1] if altar else 999
            score = 0.3 + w.gems * config.TRIBUTE_SCORE_PER_GEM
            if w.gems >= config.TRIBUTE_BAG:
                score += 0.6
            if turns <= config.TRIBUTE_NEAR_TURNS and w.gems >= config.TRIBUTE_BAG_NEAR:
                score += 0.6
            if w.hp_ratio < config.RECALL_HEALTH and w.gems >= config.TRIBUTE_BAG_HURT:
                score += 0.8
            if endgame:
                score += 2.0 if turns + (w.gems // 10) + 2 < rounds_left else 0.0
            if best is None:
                score += 0.5
            if w.gems + w.team_gems_banked() >= w.winning_gems:
                score += 3.0                                          # this bag wins the game
            scores[TRIBUTE] = score

        # Recall: heal, or go home to spend a pile of gold.
        if w.at_spawn:
            scores[RECALL] = 1.7 if w.hp_ratio < config.SPAWN_LEAVE_HEALTH else 0.0
        else:
            score = 0.0
            if w.hp_ratio < config.RECALL_HEALTH and not enemy_near:
                score = 1.8 - w.hp_ratio
            if w.gold >= config.RECALL_SHOP_GOLD and w.gems == 0 and not w.shop_exhausted:
                score = max(score, 1.0 + min(0.4, (w.gold - config.RECALL_SHOP_GOLD) / 400))
            if best is None and w.gems == 0:
                score = max(score, 0.5)
            scores[RECALL] = score

        # Fight and flee from the local power balance.
        if w.enemies:
            targets = turn.fight_targets()
            if targets:
                weakest = min(targets, key=lambda e: e.hp / e.max_hp if e.max_hp else 1.0)
                in_range = any(e.dist <= w.attack_range for e in targets)
                if weakest.hp / max(weakest.max_hp, 1) < config.KILL_STEAL_HEALTH:
                    scores[FIGHT] = 1.8 + 0.01 * weakest.gems
                elif balance >= config.FIGHT_POWER_RATIO and w.hp_ratio >= config.FIGHT_MIN_HEALTH:
                    scores[FIGHT] = 1.1 + 0.4 * (w.aggression / 255) + 0.01 * weakest.gems
                elif in_range and balance >= 1.0:
                    # Already in range: trading is free for melee, and letting them hit us is not.
                    scores[FIGHT] = 1.5 if w.damage_taken > 0 else 1.0
            if enemy_near and balance < config.FLEE_POWER_RATIO:
                scores[FLEE] = 1.4 + 0.5 * (1 - w.hp_ratio)
            elif enemy_near and w.hp_ratio < config.RECALL_HEALTH:
                scores[FLEE] = 1.2
            else:
                scores[FLEE] = 0.1
        if self.camp_overrun(w):
            # The camp will kill us before we kill it: step out of its reach, let it sleep, heal.
            scores[FLEE] = max(scores[FLEE], 2.0)

        scores[self.mode] += config.MODE_STICKINESS
        return scores

    def choose_mode(self, w, turn):
        self.cur_w = w
        mask = self.masks(w, turn)
        self.last_mask = mask
        self.last_scores = None
        # Hard rule: a badly hurt champion with an enemy close by runs, and keeps running while
        # the enemy is in sight (hysteresis stops flee flipping back every other turn).
        if w.enemies and not w.at_spawn:
            if w.hp_ratio < config.FLEE_HEALTH and w.enemy_within(config.FLEE_ENEMY_RANGE):
                return self._set_mode(FLEE, "rule")
            if self.mode == FLEE and w.hp_ratio < config.RECALL_HEALTH:
                return self._set_mode(FLEE, "rule")
        if not w.at_spawn and self.camp_hitting(w) and w.hp_ratio < config.FLEE_HEALTH:
            return self._set_mode(FLEE, "rule")                       # a camp is finishing us off
        if config.OUTNUMBERED_RULE and not w.at_spawn and len(w.enemies) >= 2                 and w.enemy_within(config.FLEE_ENEMY_RANGE + config.FLEE_GROUP_EXTRA):
            # Outnumbered by a stronger group: run, and keep running while they are in sight
            # (elk arena on the ladder: full-health bodies flipped from flee to capture with three
            # enemies beside them and died within five turns). A hard rule, above the network.
            balance = w.power_balance()
            if balance < config.FLEE_POWER_RATIO or (self.mode == FLEE and balance < config.OUTNUMBERED_STAY_BALANCE):
                return self._set_mode(FLEE, "rule")
        if config.EARLY_LEAVE and w.round <= config.EARLY_ROUNDS and not w.at_spawn and w.enemies:
            # Opening: two enemies near and nobody of ours within reach is how most early deaths
            # began (3-4 enemies by the end, 10+ rounds of warning). Leave now, not when hurt.
            near = 0
            for e in w.enemies:
                if e.dist <= config.ALERT_SIGHT:
                    near += 1
            if near >= 2 and w.power_balance() < 1.0:
                helpers = 0
                for pos, hp, strength, d in w.allies:
                    if d <= config.EARLY_LEAVE_ALLY_RANGE:
                        helpers += 1
                if helpers == 0:
                    return self._set_mode(FLEE, "early-leave")
        if config.EARLY_ALERT and w.round <= config.EARLY_ROUNDS and not w.enemies and not w.at_spawn                 and w.hp_ratio >= config.ALERT_MIN_HEALTH and w.contested is not None and w.contested[1] <= config.ALERT_FRESH:
            # An ally is calling for help at a group near it: go there (as a farm target, so the
            # walk uses the group's field; the fight starts on arrival when the enemy is in sight).
            g = w.groups[w.contested[0]] if 0 <= w.contested[0] < len(w.groups) else None
            if g is not None and g.anchor is not None and (self.target_group is None or g.index != self.target_group.index)                     and w.pos.chebyshev_distance(g.anchor) <= config.ALERT_TRAVEL:
                self.target_group, self.target_state, self.target_bodies, self.target_round = g, g.state, 1, w.round
                self.hold_since = -1
                return self._set_mode(FARM, "alert")
        pair = self._pair_rules(w)
        if pair is not None:
            return pair
        chase = self._chase_rule(w)
        if chase is not None:
            return chase
        # Spawn camping (elk arena on the ladder: 79 deaths, most within 8 turns of our spawn at
        # full health, three enemies in sight): the spawn heals and enemies cannot enter it, so a
        # body that would walk out into a stronger group stays until it has left or allies are up.
        if config.SPAWN_GATE and w.at_spawn and w.enemies and w.enemy_within(config.SPAWN_GATE_RANGE):
            mode = self._spawn_gate(w)
            if mode is not None:
                return self._set_mode(mode, "rule")
        else:
            self.gated_since = -1

        # The script always runs: it builds and scores the candidates the features summarise, and
        # it is the fallback. The network then costs only the feature pass and one forward.
        scores = self.script_scores(w, turn)
        self.last_scores = scores
        chosen, source = nn.masked_argmax(scores, mask), "script"
        if chosen is None:
            chosen = FARM
        net = (weights_adv.MODE_SELECTOR if config.USE_ADVERSARY_WEIGHTS else weights.MODE_SELECTOR) if config.USE_NN_MODE else None
        reserve = self.nn_reserve(w)
        self.risk_score = None
        if net is not None and w.round > config.NN_SKIP_ROUNDS and fuel.can_afford(MODE_SELECTOR_FUEL, reserve):
            try:
                net_scores = net.forward(features.build(w, turn, self, rescore=False))
                risk_limit = getattr(weights, "MODE_SELECTOR_RISK", None)
                if config.RISK_GATE and risk_limit is not None and len(net_scores) > N_MODES and not w.at_spawn:
                    self.risk_score = net_scores[N_MODES]
                    stay = getattr(weights, "MODE_SELECTOR_RISK_STAY", risk_limit)
                    limit = stay if self.risk_fleeing else risk_limit
                    if self.risk_score >= limit and (mask[FLEE] or mask[RECALL]):
                        # Too likely to die here in the next twenty rounds: only flee or recall.
                        safe = [False] * N_MODES
                        safe[FLEE], safe[RECALL] = mask[FLEE], mask[RECALL]
                        mask = safe
                        self.last_mask = mask
                        self.risk_fleeing = True
                        chosen = nn.masked_argmax(scores, mask)
                        if chosen is None:
                            chosen = FLEE
                        source = "risk"
                    else:
                        self.risk_fleeing = False
                if nn.margin(net_scores, mask) >= getattr(weights, "MODE_SELECTOR_MARGIN", config.NN_MIN_MARGIN):
                    pick = nn.masked_argmax(net_scores, mask)
                    if pick is not None:
                        chosen, source = pick, "nn" if source != "risk" else "risk"
            except Exception:
                pass
        else:
            self.risk_fleeing = False
        if config.MODE_EXPLORE > 0:
            explored = self._explore(w, mask, chosen)
            if explored is not None:
                chosen, source = explored, "explore"
        return self._set_mode(chosen, source)

    def _explore(self, w, mask, chosen):
        """Self-play exploration (data runs only): with probability MODE_EXPLORE, another legal
        mode among the ones a body can reverse next turn (farm, capture, fight, flee: recall and
        tribute start channels the bot waits out, and an explored recall every ten turns sent
        bodies home all game), held for MODE_EXPLORE_HOLD turns so the deviation has a measurable
        consequence. A small deterministic generator seeded from the salt, the round and our slot."""
        if self.explore_until > w.round and mask[self.explore_mode]:
            return self.explore_mode
        if not w.enemies or chosen in (CAPTURE, TRIBUTE, RECALL):
            # Only the decisions with an enemy in sight are explored: those are the ones the
            # ladder punishes, and a random deviation elsewhere (breaking a capture, walking
            # home from a camp) only wastes the game (2% everywhere cost 150 gems a game).
            return None
        state = (config.MODE_EXPLORE_SALT * 1000003 + w.round * 7919 + w.slot * 104729 + 12345) & 0x7FFFFFFF
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        if ((state >> 8) % 1000) / 1000.0 >= config.MODE_EXPLORE:
            return None
        options = [m for m in (FARM, FIGHT, FLEE) if mask[m] and m != chosen]
        if not options:
            return None
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        self.explore_mode = options[(state >> 8) % len(options)]
        self.explore_until = w.round + config.MODE_EXPLORE_HOLD
        return self.explore_mode

    def _spawn_gate(self, w):
        """On spawn with enemies in sight. Hold (RECALL: heal, shop, stand off the near edge) until
        the bodies on spawn outweigh the campers by SPAWN_BREAK_RATIO, an ally has already called
        the break-out this round or last, or the hold has lasted SPAWN_GATE_MAX_TURNS; then post
        the break-out and leave fighting, so the team walks out together instead of one at a time.
        None means the gate does not apply (we clearly outweigh them: act normally)."""
        if self.gated_since < 0:
            self.gated_since = w.round
        try:
            age = memory_codec.breakout_age(w.round)
        except Exception:
            age = None
        if age is not None and age <= 1 and self.breakout_round != w.round:
            self.breakout_round = w.round
            self.gated_since = -1
            return FIGHT if w.enemy_within(config.FIGHT_RANGE) else FARM
        balance = w.spawn_power_balance()
        if balance >= config.SPAWN_BREAK_RATIO or w.round - self.gated_since >= config.SPAWN_GATE_MAX_TURNS:
            try:
                memory_codec.write_breakout(w.round)
            except Exception:
                pass
            self.breakout_round = w.round
            self.gated_since = -1
            return FIGHT if w.enemy_within(config.FIGHT_RANGE) else None
        return RECALL

    def _chase_rule(self, w):
        """Band games: their bodies retreated 86 times a game and took 7 damage a turn doing it
        (3% died); ours took 26 a turn (17.5% died). They chase and we do not: they kill 22 of
        ours a game, we kill 9. A weak enemy close by is worth running down: the kill pays 100
        gold and its gems. Committed for CHASE_TURNS while it stays in sight and in reach."""
        self.chase_handle = None if w.round > self.chase_until else self.chase_handle
        if not config.CHASE or not w.enemies or w.at_spawn or w.hp_ratio < config.CHASE_MIN_HEALTH:
            return None
        weak = None
        for e in w.enemies:
            if e.dist <= config.CHASE_RANGE and e.hp <= config.CHASE_HP * e.max_hp:
                if self.chase_handle is not None and e.handle == self.chase_handle:
                    weak = e
                    break
                if weak is None or e.hp < weak.hp:
                    weak = e
        if weak is None:
            self.chase_handle = None
            return None
        others = 0
        for e in w.enemies:
            if e is not weak and e.dist <= config.FIGHT_RANGE and e.hp > config.CHASE_HP * e.max_hp:
                others += 1
        allies = 0
        for pos, hp, strength, d in w.allies:
            if d <= config.FIGHT_RANGE:
                allies += 1
        if others > allies:
            return None                                       # their fresh bodies outnumber ours here
        if weak.handle != self.chase_handle:
            self.chase_handle = weak.handle
            self.chase_until = w.round + config.CHASE_TURNS
        return self._set_mode(FIGHT, "chase")

    def _pair_rules(self, w):
        """Opening (band games: after an enemy first came into sight, our bodies died 11% of the time
        whether or not an ally was within 4 tiles; the band's 5-6% with an ally, 8-9% alone; five
        rounds on 52% of ours had an ally beside them, 60-68% of theirs):
        - join: an ally within PAIR_JOIN_RANGE has an enemy within 3 tiles of it and an enemy is
          near us too: fight beside it instead of deciding alone;
        - regroup: on a first sighting with no ally within 4 but one within REGROUP_RANGE, step
          towards that ally for a few turns before anything else."""
        seen_before = self.enemy_seen_round
        if w.enemies:
            self.enemy_seen_round = w.round
        if w.round > config.PAIR_ROUNDS or w.at_spawn or not w.allies:
            self.regroup_pos = None
            return None
        if w.enemies:
            if config.PAIR_JOIN and w.hp_ratio >= config.PAIR_MIN_HEALTH and w.nearest_enemy.dist <= config.PAIR_JOIN_RANGE:
                for pos, hp, strength, d in w.allies:
                    if d <= config.PAIR_JOIN_RANGE and hp > 0:
                        for e in w.enemies:
                            if e.pos.chebyshev_distance(pos) <= 3:
                                self.regroup_pos = None
                                return self._set_mode(FIGHT, "pair-join")
            if config.REGROUP:
                nearest = None
                for pos, hp, strength, d in w.allies:
                    if nearest is None or d < nearest[1]:
                        nearest = (pos, d)
                if self.regroup_pos is not None and w.round <= self.regroup_until and nearest is not None and nearest[1] > 2                         and w.nearest_enemy.dist > 2:
                    self.regroup_pos = nearest[0]
                    return self._set_mode(FARM, "regroup")
                if w.round - seen_before > 3 and nearest is not None and 4 < nearest[1] <= config.REGROUP_RANGE                         and w.nearest_enemy.dist > 2:
                    self.regroup_pos = nearest[0]
                    self.regroup_until = w.round + config.REGROUP_TURNS
                    return self._set_mode(FARM, "regroup")
        self.regroup_pos = None
        return None

    def should_stand(self, w):
        """Fleeing from a chaser in reach only gives it free hits (challenge games vs the top ten:
        70% of our deaths came in flee mode, 5% in fight mode, most in our own half; in lost
        one-on-ones we were at 73% health against their 87%). Stand and fight when the walk home
        would most likely kill us anyway, or when our side kills the weakest chaser first."""
        if not w.enemies or w.at_spawn:
            return False
        near = [e for e in w.enemies if e.dist <= config.STAND_CHASE_RANGE]
        if not near:
            return False                                        # far enough to get away
        incoming = 0.0
        for e in near:
            incoming += e.dmg
        if incoming <= 0:
            return False
        home = w.spawn_field.get(w.pos)
        escape = min(home if home is not None and home >= 0 else 99, config.STAND_CHASE_TURNS)
        if incoming * escape >= w.hp * config.STAND_FLEE_DEATH and w.hp_ratio >= config.STAND_FUTILE_MIN_HEALTH:
            return True                                         # running would not save us
        side_dps = w.dps
        for pos, hp, strength, d in w.allies:
            if d <= config.STAND_ALLY_RANGE and hp > 0:
                side_dps += strength
        target = min(near, key=lambda e: e.hp)
        return target.hp / side_dps <= config.STAND_EDGE * w.hp / incoming

    def _set_mode(self, mode, source):
        if mode == FLEE and config.STAND_GROUND and source != "early-leave" and getattr(self, "cur_w", None) is not None and self.should_stand(self.cur_w):
            mode, source = FIGHT, source + "+stand"
        if mode == self.mode:
            self.mode_turns += 1
        else:
            self.mode = mode
            self.mode_turns = 0
        self.last_source = source
        return mode

    # ---- camps ---------------------------------------------------------------------
    def score_camp(self, cand, w):
        """Reward per turn invested, discounted by the damage we expect to take."""
        g = cand.group
        bodies = 1 + cand.claimed
        turns = cand.travel + cand.wait + cand.clear_turns + config.CAPTURE_TURNS
        if turns <= 0:
            turns = 1
        # The reward is split between the allies on the region when it is captured.
        score = g.value / bodies / turns
        needs_help = False
        if not g.harmless:
            budget = max(w.hp - config.HEALTH_RESERVE * w.max_hp, 1.0)
            solo_damage = g.total_strength * (int(g.health_left / w.dps) + 1)
            needs_help = solo_damage >= budget
            if cand.damage >= budget:
                # Too much for the bodies on it now. Plan on more allies joining, if that would work:
                # with m bodies the clear is m times faster and the monsters' attention is shared.
                max_bodies = min(w.n_bodies, config.MAX_HELPERS + 1)
                m, damage_m, clear_m = bodies, cand.damage, cand.clear_turns
                while damage_m >= budget and m < max_bodies:
                    m += 1
                    clear_m = int(g.health_left / (w.dps * m)) + 1
                    damage_m = g.total_strength * clear_m / m
                if damage_m >= budget and config.BOSS_TRIPS and g.gems >= config.BOSS_MIN_GEMS:
                    # A boss (golems: 5,000 health each): the whole team, over several trips. Camps
                    # do not heal until they respawn, so each trip's damage stays; winners on boss
                    # battle had 3-5 bodies on it from round 40-120, we never had more than two.
                    m = w.n_bodies
                    clear_m = int(g.health_left / (w.dps * m)) + 1
                    damage_m = g.total_strength * clear_m / m
                    trips = int(damage_m / budget) + 1
                    if trips <= config.BOSS_MAX_TRIPS:
                        home = w.spawn_field.get(g.anchor) if g.anchor is not None else cand.travel
                        turns = cand.travel + cand.wait + clear_m + config.CAPTURE_TURNS + (trips - 1) * (home + config.BOSS_TRIP_OVERHEAD)
                        cand.bodies = m
                        score = g.value / m / max(turns, 1) * config.HELP_DISCOUNT * config.BOSS_FACTOR
                        if cand.claimed:
                            score *= 1.5
                        return self._finish_camp_score(cand, w, g, score, True)
                if damage_m < budget:
                    bodies = m
                    turns = cand.travel + cand.wait + clear_m + config.CAPTURE_TURNS
                    score = g.value / bodies / turns * config.HELP_DISCOUNT
                    score *= max(0.1, 1.0 - config.DANGER_WEIGHT * damage_m / budget)
                    if cand.claimed:
                        score *= 1.5                                  # someone is already committed
                else:
                    score = 0.0                                       # not even the whole team could
            else:
                score *= max(0.1, 1.0 - config.DANGER_WEIGHT * cand.damage / budget)
        cand.bodies = bodies
        return self._finish_camp_score(cand, w, g, score, needs_help)

    def _finish_camp_score(self, cand, w, g, score, needs_help):
        """The adjustments shared by every plan: enemies near, calls for help, claims, the
        current target, deaths there."""
        if config.CAMP_ENEMY_PENALTY and g.anchor is not None:
            # Top ladder teams spend half as many turns as we do with an enemy within 5 tiles. A
            # camp with an enemy seen near it lately is worth less than its gems say; so, when
            # CAMP_ENEMY_SIDE_FACTOR is below 1, is a camp nearer the enemy spawn than ours.
            recent = w.round - config.CAMP_ENEMY_MEMORY
            for pos, seen, _ratio in w.enemy_memory.values():
                if seen >= recent and pos.chebyshev_distance(g.anchor) <= config.CAMP_ENEMY_RANGE:
                    score *= config.CAMP_ENEMY_FACTOR
                    break
            if config.CAMP_ENEMY_SIDE_FACTOR < 1.0:
                enemy_field = w.enemy_spawn_field()
                if enemy_field is not None and enemy_field.get(g.anchor) < w.spawn_field.get(g.anchor):
                    score *= config.CAMP_ENEMY_SIDE_FACTOR
        if config.COOP:
            if cand.travel <= config.HELP_CALL_MAX_TRAVEL and w.help_called(g.index) and cand.claimed < max(cand.bodies, 2):
                # An ally is holding there for help and the plan is still short of bodies: answer
                # it (within a walk that arrives before the hold runs out, and never more
                # helpers than the camp needs: 24 answered calls a game stacked everyone there).
                score *= config.HELP_CALL_BONUS
                cand.helping = True
            contested = w.contested
            if contested is not None and contested[0] == g.index and contested[1] <= config.CONTEST_MEMORY:
                # An enemy was seen at this group lately: not a solo trip; a pair is fine.
                score *= config.CONTEST_PAIR_BONUS if cand.claimed else config.CONTEST_SOLO_FACTOR
        # The first 300 rounds: two thirds of the top teams' camp hits had an ally within 3 tiles,
        # ours a third, and they gathered twice our gems. One ally on a camp is the pair we want
        # (it clears twice as fast and splits the monsters' attention); a third body still stacks.
        paired = False
        if config.EARLY_PAIRS and w.round <= config.EARLY_PAIR_ROUNDS and not needs_help and cand.claimed == 1:
            paired = True
            score *= config.EARLY_PAIR_BONUS
            if config.ELK_BONUS != 1.0 and 1 in g.kinds:
                score *= config.ELK_BONUS
        if cand.claimed and not needs_help and not paired and not (config.COOP and w.help_called(g.index)):
            score *= config.CLAIMED_PENALTY                           # do not stack on an easy camp
        if self.target_group is not None and g.index == self.target_group.index:
            score *= 1.15                                             # finish what we started
        if config.DEATH_MEMORY and self.deaths:
            rounds = self.deaths.get(g.index)
            if rounds:
                recent = 0
                for r in rounds:
                    if w.round - r <= config.DEATH_MEMORY_ROUNDS:
                        recent += 1
                if recent >= config.DEATH_MEMORY_DROP:
                    score = 0.0                                       # we keep dying there: not a candidate
                elif recent:
                    score *= config.DEATH_MEMORY_FACTOR ** recent
        cand.score = score
        return score

    def choose_camp(self, turn, w):
        cands = turn.camps(fresh=True)
        if not cands:
            if config.LOG_DECISIONS:
                print(f"r{w.round} {w.kind_name}: no camp candidates", flush=True)
            return None
        # The script ranks first: its scores are the fallback, and its zero (a group not even the
        # whole team could clear) and MIN_CAMP_SCORE (nothing worth farming) stay as the mask.
        best = None
        for cand in cands:
            self.score_camp(cand, w)
            if cand.score > config.MIN_CAMP_SCORE and (best is None or cand.score > best.score):
                best = cand
        self.retargets += 1
        self.camp_source = "script"
        if config.COOP and self.helping_group is not None:
            # Committed to a call: keep it while the group is still a candidate and someone still
            # claims it (helpers that bounced between four holders' calls never cleared any).
            kept = next((c for c in cands if c.score > config.MIN_CAMP_SCORE and c.group.index == self.helping_group), None)
            if kept is not None and (w.help_called(self.helping_group) or w.ally_claims(self.helping_group)):
                best = kept
            else:
                self.helping_group = None
        if best is not None and getattr(best, "helping", False) and self.helping_group != best.group.index:
            self.helping_group = best.group.index
            if config.LOG_HELP:
                print("r%d %s: answering the call at g%d (travel %d)" % (w.round, w.kind_name, best.group.index, best.travel), flush=True)
        allowed = [cand.score > config.MIN_CAMP_SCORE for cand in cands]
        if self.net_target is not None:
            kept = next((cand for index, cand in enumerate(cands) if allowed[index] and cand.group.index == self.net_target), None)
            if kept is not None:
                best = kept
                self.camp_source = "nn"
            else:
                self.net_target = None
        # The network's review runs next turn (review_camp): this turn already paid for the
        # candidate build, the ranking and usually an approach field.
        self.camp_review_round = w.round + 1 if config.USE_NN_CAMP and weights.CAMP_SCORER is not None and sum(allowed) >= 2 else -1
        if config.DATALOG:
            # Debug builds only (no fuel locally, never on the platform): the camp rows for
            # tools/replay_camp_dataset.py --check. The script's pick is the label.
            script_pick = -1
            for index, cand in enumerate(cands):
                if allowed[index] and (script_pick < 0 or cand.score > cands[script_pick].score):
                    script_pick = index
            self._camp_log(w, turn, cands, script_pick)
        if config.LOG_DECISIONS:
            top = sorted(cands, key=lambda c: -c.score)[:3]
            summary = " | ".join("g%d k%s t%d c%d d%d cl%d s%d" % (c.group.index, c.group.kinds, c.travel, c.clear_turns,
                                                                 int(c.damage), c.claimed, int(c.score * 100))
                                 for c in top)
            print("r%d %s: retarget#%d hp=%d n=%d -> %s" % (w.round, w.kind_name, self.retargets, int(w.hp), len(cands),
                                                            summary), flush=True)
        return best

    def review_camp(self, turn, w):
        """The camp scorer's pass over the script's last ranking, the turn after it: re-rank the
        script's best NN_CAMP_TOP candidates and return the one to switch to, or None. Each
        candidate costs about 70K fuel (features plus the dot product), the tower 80K."""
        self.camp_review_round = -1
        net = weights.CAMP_SCORER
        cands = self.camp_cache
        if net is None or not cands or w.round <= config.NN_SKIP_ROUNDS:
            return None
        top = sorted((c for c in cands if c.score > config.MIN_CAMP_SCORE), key=lambda c: -c.score)[:config.NN_CAMP_TOP]
        if len(top) < 2 or not fuel.can_afford(config.CAMP_SCORER_FUEL * (1 + len(top)), self.camp_reserve(w)):
            return None
        try:
            state, rows = camp_features.build(w, turn, self, top)
            scores = net.score(state, rows)
            ok = [True] * len(top)
            if nn.margin(scores, ok) < getattr(weights, "CAMP_SCORER_MARGIN", 0):
                return None
            pick = nn.masked_argmax(scores, ok)
            if pick is None:
                return None
            self.camp_source = "nn"
            self.net_target = top[pick].group.index
            return top[pick]
        except Exception:
            return None

    def _camp_log(self, w, turn, cands, script_pick):
        try:
            import cambc
            cambc.debug_block(lambda: datalog.write_camp(w, turn, self, cands, script_pick, self.camp_source))
        except Exception:
            pass

    # ---- fights --------------------------------------------------------------------
    def choose_fight_target(self, turn, w):
        """Lowest health first, gems as a tiebreak, in-range preferred; the fight head re-ranks
        the visible enemies when it is on, affordable and sure."""
        best, best_key = None, None
        targets = list(turn.fight_targets())
        if self.chase_handle is not None:
            for enemy in targets:
                if enemy.handle == self.chase_handle:
                    self.fight_source = "chase"
                    return enemy
        for enemy in targets:
            in_range = enemy.dist <= w.attack_range
            key = (1 if in_range else 0, -(enemy.hp / max(enemy.max_hp, 1)), enemy.gems, -enemy.dist)
            if best_key is None or key > best_key:
                best, best_key = enemy, key
        self.fight_source = "script"
        net = weights.FIGHT_HEAD if config.USE_NN_FIGHT else None
        if net is not None and len(targets) >= 2 and w.round > config.NN_SKIP_ROUNDS \
                and fuel.can_afford(config.FIGHT_FUEL, self.camp_reserve(w)):
            try:
                enemies = sorted(targets, key=lambda e: e.dist)[:config.FIGHT_HEAD_MAX]
                state, rows = fight_features.build(w, self, enemies)
                scores = net.score(state, rows)
                ok = [True] * len(enemies)
                if nn.margin(scores, ok) >= getattr(weights, "FIGHT_HEAD_MARGIN", 0):
                    pick = nn.masked_argmax(scores, ok)
                    if pick is not None:
                        best = enemies[pick]
                        self.fight_source = "nn"
            except Exception:
                pass
        return best
