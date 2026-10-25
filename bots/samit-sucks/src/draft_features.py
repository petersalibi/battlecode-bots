"""Integer features (0-255) for the draft head: the map as seen in the draft, plus our picks so
far and which pick this is. SDK-free: the draft builds a MapInfo from game_map on its first turn
and stores the vector's map part in the draft plan, and tools/build_draft_dataset.py builds the
same MapInfo from a replay dump's static record. Layout changes mean retraining
(tools/train_draft_head.py); the head is a plain MLP, N_FEATURES -> 32 -> 12.
"""
from features import clip

N_PICKS = 12                      # ChampionDraft options 0..11
N_KINDS = 6                       # camp kinds 0-5: wolf, elk, gem cluster, gold cluster, monolith, golem
N_MAP = 22                        # map part, fixed for the whole draft
N_FEATURES = N_MAP + 1 + N_PICKS + 1   # 36: map, pick index, picks so far, constant


class MapInfo:
    """What the map features are built from. Both sides fill every field."""
    __slots__ = ("width", "height", "team_size", "n_groups", "kind_counts", "holes", "walls", "bushes",
                 "camps_within_30", "camps_within_60", "camps_within_60_dash", "spawn_turns", "altar_turns",
                 "total_gems", "total_gold")

    def __init__(self):
        self.width = self.height = 0
        self.team_size = 0
        self.n_groups = 0
        self.kind_counts = [0] * N_KINDS
        self.holes = self.walls = self.bushes = 0
        self.camps_within_30 = self.camps_within_60 = self.camps_within_60_dash = 0
        self.spawn_turns = 0
        self.altar_turns = 0
        self.total_gems = self.total_gold = 0


def map_part(info):
    """The N_MAP map features."""
    x = [0] * N_MAP
    tiles = max(info.width * info.height, 1)
    x[0] = clip(info.width * 2)
    x[1] = clip(info.height * 2)
    x[2] = clip(tiles / 64)
    x[3] = clip(info.team_size * 40)
    x[4] = clip(info.n_groups * 4)
    for kind in range(N_KINDS):
        x[5 + kind] = clip(info.kind_counts[kind] * 16)
    x[11] = clip(255 * info.holes / tiles)
    x[12] = clip(255 * info.walls / tiles)
    x[13] = clip(255 * info.bushes / tiles)
    x[14] = clip(info.camps_within_30 * 8)
    x[15] = clip(info.camps_within_60 * 4)
    x[16] = clip(info.camps_within_60_dash * 4)
    x[17] = clip(64 * info.camps_within_60_dash / max(info.camps_within_60, 1))
    x[18] = clip(info.spawn_turns * 2)
    x[19] = clip(info.altar_turns * 4)
    x[20] = clip(info.total_gems / 4)
    x[21] = clip(info.total_gold / 40)
    return x


def build(map_features, picks, pick_index):
    """Full vector from a stored map part, the picks so far (ChampionDraft ints) and the pick
    number (0-based)."""
    x = list(map_features) + [0] * (N_FEATURES - N_MAP)
    x[N_MAP] = clip(pick_index * 64)
    for pick in picks:
        if 0 <= pick < N_PICKS:
            x[N_MAP + 1 + pick] = 255
    x[N_FEATURES - 1] = 255
    return x
