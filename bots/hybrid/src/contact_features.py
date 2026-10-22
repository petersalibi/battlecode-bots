"""Integer features (0-255) for the contact head: what a body does on a turn with an enemy
champion within CONTACT_RANGE. Four options: ATTACK (hit a champion), CLOSE (step towards the
nearest enemy), HOLD (stay, or step sideways: farm, cast, wait) and RETREAT (step away).

Built from the world model only, so the same function runs in the bot (decide._contact_head)
and offline over replay dumps (tools/replay_contact_dataset.py, on the band's own turns). Only
attributes both worlds share are read: the in-bot Enemy's `dmg`/`ehp` are not, and the balance
is the strength-only `power_balance_raw`, which the offline world mirrors exactly.
"""
import config
from features import clip, turns

N_CONTACT = 29
ATTACK, CLOSE, HOLD, RETREAT = 0, 1, 2, 3
N_ACTIONS = 4
ACTION_NAMES = ("attack", "close", "hold", "retreat")


def _reach(e):
    return getattr(e, "reach", 1)


def build(w, decider):
    x = [0] * N_CONTACT
    x[0] = clip(w.hp_ratio * 255)
    x[1] = clip(w.max_hp / 20)
    x[2] = clip(w.strength / 2)
    x[3] = clip(w.dps / 2)
    x[4] = 255 if w.is_ranged else 0
    cds = getattr(w, "cooldowns", None) or [0, 0]
    x[5] = 255 if (w.has_dash and any(c == 0 for c in cds)) else (128 if any(c == 0 for c in cds) else 0)
    near = w.nearest_enemy
    if near is not None:
        x[6] = clip(32 * near.dist)
        x[7] = clip(255 * near.hp / max(near.max_hp, 1))
        x[8] = 255 if _reach(near) > 1 else 0
        x[9] = clip(near.strength / 2)
    n2 = n4 = n6 = 0
    incoming = 0.0
    weakest = 1.0
    for e in w.enemies:
        if e.dist <= 2:
            n2 += 1
        if e.dist <= 4:
            n4 += 1
            ratio = e.hp / max(e.max_hp, 1)
            if ratio < weakest:
                weakest = ratio
        if e.dist <= 6:
            n6 += 1
        if e.dist <= _reach(e):
            incoming += e.strength
    x[10] = clip(64 * n2)
    x[11] = clip(64 * n4)
    x[12] = clip(64 * n6)
    x[13] = clip(incoming / 4)
    x[14] = clip(255 * weakest)
    balance = w.power_balance_raw()
    x[15] = clip(128 * min(balance, 1.99)) if balance < 99 else 255
    a4 = a8 = 0
    for pos, hp, strength, d in w.allies:
        if d <= 4:
            a4 += 1
        if d <= 8:
            a8 += 1
    x[16] = clip(64 * a4)
    x[17] = clip(64 * a8)
    x[18] = clip(w.gems)
    x[19] = clip(255 * w.damage_taken / max(w.max_hp, 1))
    x[20] = 255 if w.in_bush else 0
    x[21] = 255 if w.at_spawn else 0
    x[22] = turns(w.turns_to_spawn)
    x[23] = clip(w.round / 8)
    # What the band's "hold" and "retreat" key off (second training pass, 2026-10-22): a living camp
    # in reach (farming beside an enemy), camps close by, the altar's distance (a carrier heading
    # home), and what this body did last turn (their own hysteresis).
    camp_in_reach, camps_near = 0, 0
    for camp, gi, pos, hp in w.visible_camps:
        if hp <= 0:
            continue
        d = w.pos.chebyshev_distance(pos)
        if d <= w.attack_range:
            camp_in_reach = 1
        if d <= 4:
            camps_near += 1
    x[24] = 255 if camp_in_reach else 0
    x[25] = clip(64 * camps_near)
    x[26] = turns(w.turns_to_altar)
    mode = getattr(decider, "mode", None)
    x[27] = 255 if mode == config.FLEE else 0
    x[28] = 255 if mode == config.FIGHT else 0
    return x
