"""Integer features (0-255) for the networks, built from the world model only.

The same function must run inside the bot and offline over replay-reconstructed state, so it
reads plain attributes from World, Turn and Decider and never calls the SDK. The layout follows
docs/NEURAL_NET_GUIDE.md section 5, widened on 2026-10-20 from 64 to 66 inputs so the champion
one-hot covers all 13 body types (Tavi and Heart of Ruin had no slot). Everything after the
one-hot keeps its order, shifted by two.
"""
import memory_codec
import world as W

N_CHAMPION_TYPES = 13
# Block offsets. Self block: 0-6 scalars, 7-19 champion one-hot, then 20-35 as the guide's 18-33.
SELF_FLAGS = 7 + N_CHAMPION_TYPES          # 20: slot 0 cooldown
POSITION = SELF_FLAGS + 16                 # 36: turns to altar
FARMING = POSITION + 6                     # 42: turns to best camp
THREAT = FARMING + 6                       # 48: visible enemies
TEAM = THREAT + 10                         # 58: game progress
N_FEATURES = TEAM + 8                      # 66


def clip(value):
    value = int(value)
    return 0 if value < 0 else 255 if value > 255 else value


def turns(distance):
    """Walking turns, clipped at 63 and scaled by 4 so maps of any size read the same."""
    if distance >= W.UNREACHABLE:
        return 255
    return clip(min(distance, 63) * 4)


def build(w, turn, decider, rescore=True):
    """rescore=False reuses the camp scores the script just computed (decide.py runs the script
    first, so the network's marginal cost is this function plus the forward pass)."""
    w.ensure_feature_state(decider)
    x = [0] * N_FEATURES

    # Self (0-35)
    x[0] = clip(w.hp_ratio * 255)
    x[1] = clip(w.max_hp / 20)
    x[2] = clip(w.strength / 2)
    x[3] = clip(w.focus / 2)
    x[4] = clip(w.gems)
    x[5] = clip(w.gold / 4)
    x[6] = clip(w.shards_bought() * 8)
    if 0 <= w.kind < N_CHAMPION_TYPES:
        x[7 + w.kind] = 255
    f = SELF_FLAGS
    for rec in w.abilities:
        if rec.slot in (0, 1):
            x[f + rec.slot] = clip(w.cooldowns[rec.slot] * 32)
    x[f + 2] = 255 if w.stunned else 0
    x[f + 3] = 255 if w.recalling else 0
    x[f + 4] = 255 if w.tributing else 0
    x[f + 5] = 255 if w.teleporting else 0
    x[f + 6] = clip(w.channel_time * 32)
    x[f + 7] = 255 if w.has_altar_lock else 0
    x[f + 8] = 255 if w.has_tp_crystal else 0
    x[f + 9 + decider.mode] = 255
    x[f + 15] = clip(decider.mode_turns * 4)

    # Position (36-41)
    p = POSITION
    altar = turn.altar()
    x[p] = turns(altar[1]) if altar else 255
    x[p + 1] = turns(w.turns_to_spawn)
    x[p + 2] = clip(w.recall_time * 32)
    x[p + 3] = 255 if w.at_spawn else 0
    x[p + 4] = 255 if w.in_bush else 0
    to_enemy = w.turns_to_enemy_spawn()
    total = w.turns_to_spawn + to_enemy
    x[p + 5] = clip(255 * w.turns_to_spawn / total) if 0 < total < W.UNREACHABLE else 128

    # Farming opportunity (42-47)
    c = FARMING
    camps = turn.camps()
    best = None
    for cand in camps:
        if rescore:
            decider.score_camp(cand, w)
        if best is None or cand.score > best.score:
            best = cand
    if best is not None:
        x[c] = turns(best.travel)
        x[c + 1] = clip(best.group.gems)
        x[c + 2] = clip(128 * best.damage / max(w.hp, 1))
    else:
        x[c] = 255
    x[c + 3] = clip(16 * sum(1 for cand in camps if cand.travel <= 20))
    x[c + 4] = clip(51 * w.capture_rounds_left)
    x[c + 5] = clip(w.camp_damage_incoming / 4)

    # Threat (48-57)
    t = THREAT
    x[t] = clip(32 * len(w.enemies))
    if w.nearest_enemy is not None:
        e = w.nearest_enemy
        x[t + 1] = clip(16 * e.dist)
        x[t + 2] = clip(255 * e.hp / max(e.max_hp, 1))
        x[t + 4] = clip(max(en.gems for en in w.enemies))
    else:
        x[t + 1] = 255
    balance = w.power_balance_raw()
    x[t + 3] = clip(128 * min(balance, 1.99)) if balance < 99 else 255
    x[t + 5] = clip(4 * (w.round - w.last_enemy_round))
    if w.last_enemy_pos is not None:
        x[t + 6] = turns(w.pos.chebyshev_distance(w.last_enemy_pos))
    else:
        x[t + 6] = 255
    x[t + 7] = clip(64 * len(w.allies))
    x[t + 8] = clip(16 * min((a[3] for a in w.allies), default=16))
    x[t + 9] = clip(255 * w.damage_taken / max(w.max_hp, 1))

    # Team and game (58-65)
    g = TEAM
    x[g] = clip(255 * w.round / max(w.max_rounds, 1))
    ours, theirs = w.team_gems_banked(), w.enemy_gems_banked()
    x[g + 1] = clip(ours / 4)
    x[g + 2] = clip(theirs / 4)
    x[g + 3] = clip((ours - theirs) / 4 + 128)
    busy = dead = 0
    for slot, (status, _) in enumerate(w.claims):
        if slot < w.n_bodies and slot != w.slot and status == memory_codec.STATUS_BUSY:
            busy += 1
    x[g + 4] = clip(64 * busy)
    x[g + 5] = clip(64 * dead)
    x[g + 6] = clip(64 * sum(1 for r in w.enemies_killed if w.round - r <= 60))
    x[g + 7] = clip(w.aggression)
    return x
