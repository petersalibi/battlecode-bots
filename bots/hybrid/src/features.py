"""Integer features (0-255) for the networks, built from the world model only.

The same function must run inside the bot and offline over replay-reconstructed state, so it
reads plain attributes from World, Turn and Decider and never calls the SDK. The layout follows
docs/NEURAL_NET_GUIDE.md section 5.
"""
import memory_codec
import world as W

N_FEATURES = 64
N_CHAMPION_TYPES = 11


def clip(value):
    value = int(value)
    return 0 if value < 0 else 255 if value > 255 else value


def turns(distance):
    """Walking turns, clipped at 63 and scaled by 4 so maps of any size read the same."""
    if distance >= W.UNREACHABLE:
        return 255
    return clip(min(distance, 63) * 4)


def build(w, turn, decider):
    w.ensure_feature_state(decider)
    x = [0] * N_FEATURES

    # Self (0-33)
    x[0] = clip(w.hp_ratio * 255)
    x[1] = clip(w.max_hp / 20)
    x[2] = clip(w.strength / 2)
    x[3] = clip(w.focus / 2)
    x[4] = clip(w.gems)
    x[5] = clip(w.gold / 4)
    x[6] = clip(w.shards_bought() * 8)
    if 0 <= w.kind < N_CHAMPION_TYPES:
        x[7 + w.kind] = 255
    for rec in w.abilities:
        if rec.slot in (0, 1):
            x[18 + rec.slot] = clip(w.cooldowns[rec.slot] * 32)
    x[20] = 255 if w.stunned else 0
    x[21] = 255 if w.recalling else 0
    x[22] = 255 if w.tributing else 0
    x[23] = 255 if w.teleporting else 0
    x[24] = clip(w.channel_time * 32)
    x[25] = 255 if w.has_altar_lock else 0
    x[26] = 255 if w.has_tp_crystal else 0
    x[27 + decider.mode] = 255
    x[33] = clip(decider.mode_turns * 4)

    # Position (34-39)
    altar = turn.altar()
    x[34] = turns(altar[1]) if altar else 255
    x[35] = turns(w.turns_to_spawn)
    x[36] = clip(w.recall_time * 32)
    x[37] = 255 if w.at_spawn else 0
    x[38] = 255 if w.in_bush else 0
    to_enemy = w.turns_to_enemy_spawn()
    total = w.turns_to_spawn + to_enemy
    x[39] = clip(255 * w.turns_to_spawn / total) if 0 < total < W.UNREACHABLE else 128

    # Farming opportunity (40-45)
    camps = turn.camps()
    best = None
    for cand in camps:
        decider.score_camp(cand, w)
        if best is None or cand.score > best.score:
            best = cand
    if best is not None:
        x[40] = turns(best.travel)
        x[41] = clip(best.group.gems)
        x[42] = clip(128 * best.damage / max(w.hp, 1))
    else:
        x[40] = 255
    x[43] = clip(16 * sum(1 for c in camps if c.travel <= 20))
    x[44] = clip(51 * w.capture_rounds_left)
    x[45] = clip(w.camp_damage_incoming / 4)

    # Threat (46-55)
    x[46] = clip(32 * len(w.enemies))
    if w.nearest_enemy is not None:
        e = w.nearest_enemy
        x[47] = clip(16 * e.dist)
        x[48] = clip(255 * e.hp / max(e.max_hp, 1))
        x[50] = clip(max(en.gems for en in w.enemies))
    else:
        x[47] = 255
    balance = w.power_balance()
    x[49] = clip(128 * min(balance, 1.99)) if balance < 99 else 255
    x[51] = clip(4 * (w.round - w.last_enemy_round))
    if w.last_enemy_pos is not None:
        x[52] = turns(w.pos.chebyshev_distance(w.last_enemy_pos))
    else:
        x[52] = 255
    x[53] = clip(64 * len(w.allies))
    x[54] = clip(16 * min((a[3] for a in w.allies), default=16))
    x[55] = clip(255 * w.damage_taken / max(w.max_hp, 1))

    # Team and game (56-63)
    x[56] = clip(255 * w.round / max(w.max_rounds, 1))
    ours, theirs = w.team_gems_banked(), w.enemy_gems_banked()
    x[57] = clip(ours / 4)
    x[58] = clip(theirs / 4)
    x[59] = clip((ours - theirs) / 4 + 128)
    busy = dead = 0
    for slot, (status, _) in enumerate(w.claims):
        if slot < w.n_bodies and slot != w.slot and status == memory_codec.STATUS_BUSY:
            busy += 1
    x[60] = clip(64 * busy)
    x[61] = clip(64 * dead)
    x[62] = clip(64 * sum(1 for r in w.enemies_killed if w.round - r <= 60))
    x[63] = clip(w.aggression)
    return x
