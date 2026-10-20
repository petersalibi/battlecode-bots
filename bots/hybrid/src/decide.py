"""Every decision as a scoring function.

Each chooser has a scripted scorer and an optional network behind the same interface. The
network only ranks options the script generated, and masks are applied before any argmax, so a
network can never choose an illegal action. When a head is off, has no weights, is short of fuel,
or is unsure, the script decides.
"""
import config
import features
import fuel
import nn
import weights
from config import FARM, CAPTURE, TRIBUTE, RECALL, FIGHT, FLEE, N_MODES

MODE_SELECTOR_FUEL = 600_000


class Decider:
    def __init__(self):
        self.mode = FARM
        self.mode_turns = 0
        self.target_group = None      # Group being farmed
        self.target_round = -1000
        self.target_state = None      # the group's state when it was chosen
        self.target_bodies = 1        # champions the plan needs on the target group
        self.hold_since = -1          # round we started waiting for allies, or -1
        self.capture_group = None
        self.last_scores = None
        self.last_source = "script"
        self.camp_cache = None
        self.camp_cache_round = -1000
        self.cache_scored_round = -2000
        self.retargets = 0
        self.idle_turns = 0           # consecutive turns without a move or an action (bot.py)

    # ---- mode ----------------------------------------------------------------------
    def masks(self, w, turn):
        has_enemy = bool(w.enemies)
        return [
            True,                                                     # FARM always allowed
            bool(turn.captures()),                                    # CAPTURE
            w.gems > 0 and turn.altar() is not None,                  # TRIBUTE
            w.at_spawn or w.damage_taken <= 0,                        # RECALL (damage cancels it)
            has_enemy and bool(turn.fight_targets()),                 # FIGHT
            has_enemy,                                                # FLEE
        ]

    def script_scores(self, w, turn):
        scores = [0.0] * N_MODES
        rounds_left = w.rounds_left()
        endgame = rounds_left <= config.ENDGAME_ROUNDS
        enemy_near = w.enemy_within(config.FLEE_ENEMY_RANGE)
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

        scores[self.mode] += config.MODE_STICKINESS
        return scores

    def choose_mode(self, w, turn):
        mask = self.masks(w, turn)
        # Hard rule: a badly hurt champion with an enemy close by runs, and keeps running while
        # the enemy is in sight (hysteresis stops flee flipping back every other turn).
        if w.enemies and not w.at_spawn:
            if w.hp_ratio < config.FLEE_HEALTH and w.enemy_within(config.FLEE_ENEMY_RANGE):
                return self._set_mode(FLEE, "rule")
            if self.mode == FLEE and w.hp_ratio < config.RECALL_HEALTH:
                return self._set_mode(FLEE, "rule")

        chosen, source = None, "script"
        net = weights.MODE_SELECTOR if config.USE_NN_MODE else None
        if net is not None and fuel.can_afford(MODE_SELECTOR_FUEL, config.NN_FUEL_RESERVE):
            try:
                scores = net.forward(features.build(w, turn, self))
                if nn.margin(scores, mask) >= config.NN_MIN_MARGIN:
                    chosen, source = nn.masked_argmax(scores, mask), "nn"
                    self.last_scores = scores
            except Exception:
                chosen = None
        if chosen is None:
            scores = self.script_scores(w, turn)
            self.last_scores = scores
            chosen = nn.masked_argmax(scores, mask)
            if chosen is None:
                chosen = FARM
        return self._set_mode(chosen, source)

    def _set_mode(self, mode, source):
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
            solo_damage = g.total_strength * (int(g.total_health / w.dps) + 1)
            needs_help = solo_damage >= budget
            if cand.damage >= budget:
                # Too much for the bodies on it now. Plan on more allies joining, if that would work:
                # with m bodies the clear is m times faster and the monsters' attention is shared.
                max_bodies = min(w.n_bodies, config.MAX_HELPERS + 1)
                m, damage_m, clear_m = bodies, cand.damage, cand.clear_turns
                while damage_m >= budget and m < max_bodies:
                    m += 1
                    clear_m = int(g.total_health / (w.dps * m)) + 1
                    damage_m = g.total_strength * clear_m / m
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
        if cand.claimed and not needs_help:
            score *= config.CLAIMED_PENALTY                           # do not stack on an easy camp
        if self.target_group is not None and g.index == self.target_group.index:
            score *= 1.15                                             # finish what we started
        cand.score = score
        return score

    def choose_camp(self, turn, w):
        cands = turn.camps(fresh=True)
        if not cands:
            if config.LOG_DECISIONS:
                print(f"r{w.round} {w.kind_name}: no camp candidates", flush=True)
            return None
        if config.USE_NN_CAMP and weights.CAMP_SCORER is not None:
            pass                                                       # TODO: factored candidate scorer
        best = None
        for cand in cands:
            self.score_camp(cand, w)
            if cand.score > config.MIN_CAMP_SCORE and (best is None or cand.score > best.score):
                best = cand
        self.retargets += 1
        if config.LOG_DECISIONS:
            top = sorted(cands, key=lambda c: -c.score)[:3]
            summary = " | ".join("g%d k%s t%d c%d d%d cl%d s%d" % (c.group.index, c.group.kinds, c.travel, c.clear_turns,
                                                                 int(c.damage), c.claimed, int(c.score * 100))
                                 for c in top)
            print("r%d %s: retarget#%d hp=%d n=%d -> %s" % (w.round, w.kind_name, self.retargets, int(w.hp), len(cands),
                                                            summary), flush=True)
        return best

    # ---- fights --------------------------------------------------------------------
    def choose_fight_target(self, turn, w):
        """Lowest health first, gems as a tiebreak, in-range preferred."""
        best, best_key = None, None
        for enemy in turn.fight_targets():
            in_range = enemy.dist <= w.attack_range
            key = (1 if in_range else 0, -(enemy.hp / max(enemy.max_hp, 1)), enemy.gems, -enemy.dist)
            if best_key is None or key > best_key:
                best, best_key = enemy, key
        return best
