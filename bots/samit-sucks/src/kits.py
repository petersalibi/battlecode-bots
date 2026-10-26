"""Champion data for engine 2.0.0 (docs/GameRules.md, 2026-10-26)."""

# ChampionType order in the SDK (Cassi and Issac are separate bodies).
NAMES = ("RYTH", "VESPER_VALE", "VENDETTA", "CASSI", "ISSAC", "STELLAR", "PRISM", "MARCELLA",
         "WILLOW", "REN", "VARG", "TAVI", "HEART_OF_RUIN")

# Damage a turn and effective health from the kit. Every slot has an 8-turn cooldown and a body
# casts once a turn, so a spell of k x focus adds k/8 focus a turn, an attack reset 1/8 strength,
# Varg's splash 1/4; shields count as health. (strength x, focus x, shield focus x)
POWER = {
    "CASSI": (1.0, 0.25, 0.0),          # 2x focus nuke
    "ISSAC": (1.125, 0.0, 0.0),         # reset
    "RYTH": (1.125, 0.125, 0.0),        # reset, 1x focus area stun
    "VESPER_VALE": (1.125, 0.0, 0.0),   # reset
    "PRISM": (1.0, 0.31, 0.0),          # 1.5x stun nuke, 1x area
    "STELLAR": (1.0, 0.43, 0.0),        # 2x nuke, 1.4x area
    "VENDETTA": (1.125, 0.0, 0.0),      # reset (kills reset cooldowns)
    "MARCELLA": (1.125, 0.25, 0.0),     # reset with stun, 2x nuke
    "TAVI": (1.125, 0.2, 0.0),          # reset, 1.6x stun nuke
    "WILLOW": (1.125, 0.0, 0.0),        # reset, heal
    "REN": (1.0, 0.3, 0.0),             # 1.1x stun nuke, 0.2x focus retaliation per hit taken
    "VARG": (1.25, 0.0, 2.5),           # reset + splash, 2.5x focus shield
    "HEART_OF_RUIN": (1.0, 0.19, 1.5),  # 1.5x landing, 1.5x shield
}

# Builds: (family, stat, shards) in order; the last entry repeats while there is gold. A first
# guess for the new items: the Capture emblem first (5 -> 3 round captures wherever its holder
# stands: every farmer captures several times a game), Jungle for the front line (30% less camp
# damage was the best pre-patch first emblem for melee), Monster for ranged hitters (15% more camp
# damage), Power for casters; Treasure is out (20% -> 10%).
_MELEE = (("CAPTURE", "HEALTH", 5), ("JUNGLE", "HEALTH", 5), ("BRUISER", "HEALTH", 5), ("BRUISER", "STRENGTH", 99))
_RANGED = (("CAPTURE", "STRENGTH", 5), ("MONSTER", "STRENGTH", 5), ("BRUISER", "HEALTH", 5), ("BRUISER", "STRENGTH", 99))
_CASTER = (("CAPTURE", "FOCUS", 5), ("POWER", "FOCUS", 5), ("BRUISER", "HEALTH", 5), ("BRUISER", "FOCUS", 99))
BUILDS = {
    "VENDETTA": _MELEE, "VARG": _MELEE, "RYTH": _MELEE, "MARCELLA": _MELEE, "REN": _MELEE,
    "ISSAC": _MELEE, "HEART_OF_RUIN": _MELEE,
    "VESPER_VALE": _RANGED, "TAVI": _RANGED, "WILLOW": _RANGED,
    "STELLAR": _CASTER, "PRISM": _CASTER, "CASSI": _CASTER,
}


def power(name, strength, focus, hp):
    """(damage a turn, effective health) for a champion of this kit."""
    s, f, shield = POWER.get(name, (1.0, 0.0, 0.0))
    return strength * s + focus * f, hp + focus * shield
