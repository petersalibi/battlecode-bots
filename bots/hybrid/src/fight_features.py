"""Integer features (0-255) for the fight head: which visible enemy to attack. One state vector
per turn and one candidate vector per visible enemy, from the world model only, so the same code
runs in the bot (decide.choose_fight_target) and offline (tools/replay_fight_dataset.py). The
head is bilinear (nn.Bilinear): one dot product per enemy.
"""
import config
from features import clip, turns

N_STATE = 12
N_CAND = 12


def state(w, decider):
    x = [0] * N_STATE
    x[0] = clip(w.hp_ratio * 255)
    x[1] = clip(w.max_hp / 20)
    x[2] = clip(w.strength / 2)
    x[3] = clip(w.dps / 2)
    x[4] = clip(32 * len(w.enemies))
    x[5] = clip(64 * sum(1 for pos, hp, strength, d in w.allies if d <= config.FIGHT_RANGE))
    balance = w.power_balance()
    x[6] = clip(128 * min(balance, 1.99)) if balance < 99 else 255
    x[7] = clip(w.gems)
    x[8] = clip(255 * w.damage_taken / max(w.max_hp, 1))
    x[9] = 255 if w.is_ranged else 0
    x[10] = clip(64 * len([1 for rec in w.abilities if getattr(rec, "is_dash", False)]))
    x[11] = turns(w.turns_to_spawn)
    return x


def build(w, decider, enemies):
    """State vector and one candidate vector per enemy (world.Enemy-like: pos, hp, max_hp,
    strength, gems, dist)."""
    s = state(w, decider)
    allies = [pos for pos, hp, strength, d in w.allies]
    positions = [e.pos for e in enemies]
    nearest = min(enemies, key=lambda e: e.dist) if enemies else None
    my_power = max(w.hp * w.strength, 1.0)
    rows = []
    for e in enemies:
        x = [0] * N_CAND
        x[0] = clip(255 * e.hp / max(e.max_hp, 1))
        x[1] = clip(e.hp / 20)
        x[2] = clip(e.strength / 2)
        x[3] = clip(16 * e.dist)
        x[4] = 255 if e.dist <= w.attack_range else 0
        x[5] = clip(e.gems)
        x[6] = clip(64 * sum(1 for a in allies if a.chebyshev_distance(e.pos) <= 2))
        x[7] = clip(64 * sum(1 for p in positions if p is not e.pos and p.chebyshev_distance(e.pos) <= 3))
        x[8] = 255 if e is nearest else 0
        x[9] = clip(128 * min(e.hp * e.strength / my_power, 1.99))
        x[10] = clip(255 * e.hp / max(w.dps * 10, 1))      # turns of our damage to kill it, over ten
        x[11] = clip(16 * e.pos.chebyshev_distance(w.pos)) if w.at_spawn else 0
        rows.append(x)
    return s, rows
