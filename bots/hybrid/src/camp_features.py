"""Integer features (0-255) for the camp scorer: one state vector per turn and one candidate vector
per camp group, built from the world model only (no SDK calls), so the same code runs in the bot
and offline over replay-reconstructed state (tools/replay_camp_dataset.py).

The scorer is a factored candidate scorer (nn.Factored): the first layer's input is
[state | candidate], so the state part is accumulated once per retarget and the candidate part
once per candidate. Layout changes here must be mirrored by retraining (tools/train_camp_scorer.py).

Fuel: every Python statement costs about a thousand fuel and twelve candidates are scored per
retarget, so the part of a candidate vector that never changes (reward, kinds, which half of the
map) is built once per group and cached on it, and the per-candidate part is a dozen statements.
"""
import config
import world as W
from features import clip, turns

N_STATE = 20
N_CAND = 22
N_KINDS = 6             # camp kinds 0-5: wolf, elk, gem cluster, gold cluster, monolith, golem
ENEMY_NEAR = 8          # an enemy remembered within this of the group counts as "near it"
DEATH_MEMORY = 300      # own deaths at a group older than this are forgotten
UNREACHABLE = W.UNREACHABLE


def state(w, turn, decider):
    """What the body is: health, power, bag, position, threat, game state."""
    x = [0] * N_STATE
    x[0] = clip(w.hp_ratio * 255)
    x[1] = clip(w.max_hp / 20)
    x[2] = clip(w.strength / 2)
    x[3] = clip(w.dps / 2)
    x[4] = clip(w.gems)
    x[5] = clip(w.gold / 4)
    x[6] = turns(w.turns_to_spawn)
    x[7] = 255 if w.at_spawn else 0
    x[8] = clip(32 * len(w.enemies))
    x[9] = clip(16 * w.nearest_enemy.dist) if w.nearest_enemy is not None else 255
    x[10] = clip(4 * (w.round - w.last_enemy_round))
    x[11] = clip(255 * w.round / max(w.max_rounds, 1))
    x[12] = clip(32 * w.n_bodies)
    ours, theirs = w.team_gems_banked(), w.enemy_gems_banked()
    x[13] = clip((ours - theirs) / 4 + 128)
    x[14] = clip(64 * len(w.allies))
    x[15] = 255 if w.is_ranged else 0
    x[16] = 255 if w.has_dash else 0
    x[17] = clip(w.aggression)
    x[18] = clip(ours / 4)
    balance = w.power_balance()
    x[19] = clip(128 * min(balance, 1.99)) if balance < 99 else 255
    return x


def group_static(g, ours, theirs):
    """The candidate entries that never change for a group: reward, size, kinds, harmless, and
    which half of the map it sits in. Built once per side and cached on the group (keyed by the
    side's spawn centre, since offline both teams share the group objects)."""
    x = [0] * N_CAND
    x[4] = clip(g.gems)
    x[5] = clip(g.gold / 4)
    x[6] = clip(32 * g.n_camps)
    for kind in g.kinds:
        if 0 <= kind < N_KINDS:
            x[7 + kind] = clip(x[7 + kind] + 64)
    x[15] = 255 if g.harmless else 0
    tile = g.tile_list[0]
    da, de = tile.chebyshev_distance(ours), tile.chebyshev_distance(theirs)
    x[16] = clip(255 * da / (da + de)) if da + de > 0 else 128
    g.camp_static = (ours, x)
    return x


def build(w, turn, decider, cands):
    """State vector and one candidate vector per candidate, in the candidates' order."""
    s = state(w, turn, decider)
    round_number = w.round
    hp = max(w.hp, 1.0)
    budget = max(w.hp - config.HEALTH_RESERVE * w.max_hp, 1.0)
    dps = w.dps
    target = decider.target_group
    target_index = target.index if target is not None else -1
    deaths = decider.deaths
    # Remembered enemies as (pos, round seen); usually none or a few.
    seen = [(pos, when) for pos, when, _ratio in w.enemy_memory.values()]
    ours, theirs = w.spawn_centers()
    rows = []
    for cand in cands:
        g = cand.group
        cached = getattr(g, "camp_static", None)
        x = cached[1] if cached is not None and cached[0] is ours else group_static(g, ours, theirs)
        x = x[:]
        travel = cand.travel
        x[0] = 255 if travel >= UNREACHABLE else (travel if travel < 63 else 63) * 4
        x[1] = min(255, 4 * cand.wait)
        x[2] = min(255, 4 * cand.clear_turns)
        x[3] = min(255, int(128 * cand.damage / hp))
        x[13] = min(255, 64 * cand.claimed)
        x[14] = 255 if g.index == target_index else 0
        if seen:
            tile = g.tile_list[0]
            recent = -1
            for pos, when in seen:
                if when > recent and pos.chebyshev_distance(tile) <= ENEMY_NEAR:
                    recent = when
            x[17] = min(255, 4 * (round_number - recent)) if recent >= 0 else 255
        else:
            x[17] = 255
        if deaths:
            rounds = deaths.get(g.index)
            if rounds:
                recent = [r for r in rounds if round_number - r <= DEATH_MEMORY]
                if recent:
                    x[18] = min(255, 64 * len(recent))
                    x[19] = min(255, 4 * (round_number - recent[-1]))
                else:
                    x[19] = 255
            else:
                x[19] = 255
        else:
            x[19] = 255
        x[20] = 255 if g.state == W.RESPAWNING else 0
        x[21] = min(255, int(128 * g.total_strength * (int(g.total_health / dps) + 1) / budget))
        rows.append(x)
    return s, rows


def candidate(w, cand, decider):
    """One candidate vector (the per-row path; build() is the fast one)."""
    return build(w, None, decider, [cand])[1][0]
