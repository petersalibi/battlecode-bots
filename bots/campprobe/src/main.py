"""Camp targeting probe for 00rushbait-v2: both teams run this bot.

Nobody ever attacks a champion, so every health loss is camp damage. One body per side (the
striker, role S) hits a chosen wolf on a schedule; the others walk to scripted tiles around it.
Every turn each body prints its state, and tools/camp_target_probe.py reads the replay and checks
which champion each awake camp hit against where everyone stood.

Lines (all start with "P "):
  P init r side role id name cdm      once: camp damage multiplier
  P s r side role id hp x y           turn start (after the round's camp actions)
  P a r side role id x y hp           an awake camp this body sees at turn start
  P e r side role id x y              turn end position (where camps find us next round)
  P h r side role id x y              the striker hit the camp at x,y
"""
import cambc
from cambc import game_map

Team, Vec2, Draft = cambc.Team, cambc.Vec2, cambc.ChampionDraft

# Roles by champion, first choice then a fallback in case both teams cannot share a pick.
ROLE_PICKS = (
    ("S", (Draft.TAVI, Draft.STELLAR)),          # striker: ranged, 1200 health (it takes most hits)
    ("R", (Draft.PRISM, Draft.WILLOW)),          # second ranged body: never attacks
    ("M", (Draft.RYTH, Draft.MARCELLA)),         # melee, never attacks
    ("N", (Draft.VENDETTA, Draft.HEART_OF_RUIN)),
)
ROLE_OF = {}
for role, picks in ROLE_PICKS:
    for p in picks:
        ROLE_OF[str(Draft.to_string(int(p))).split(".")[-1]] = role

# The left column of wolves (x = 13, holes at x = 14, right side x >= 15).
W31, W26, W21, W16 = Vec2(13, 31), Vec2(13, 26), Vec2(13, 21), Vec2(13, 16)

# (start round, {side: wolf its striker hits}, {side:role: tile}). Tiles carry forward until changed;
# a side missing from the wolf dict does not hit. Offsets below are (dx, dy) from the wolf.
SCHEDULE = [
    # E1 at W31: attacker vs nearest, across teams and the holes.
    (1,   {},         {"L:S": (10, 31), "R:R": (20, 31), "R:S": (20, 30), "R:M": (20, 21)}),
    (12,  {"L": W31}, {}),                                         # a: striker alone at (3,0)
    (17,  {"L": W31}, {"R:R": (15, 31)}),                          # b: enemy non-attacker nearer, (2,0)
    (27,  {"L": W31}, {"R:R": (16, 31)}),                          # c: tie (3,0) vs (3,0), attacker id 0
    (33,  {},         {"L:S": (6, 31), "L:R": (10, 31), "R:R": (20, 31)}),
    (40,  {},         {"R:S": (16, 31)}),
    (42,  {"R": W31}, {}),                                         # d: tie, attacker is the enemy id 5
    (49,  {},         {"R:S": (20, 30), "L:R": (6, 33),
                       "L:S": (10, 26), "L:M": (7, 23), "L:N": (7, 29)}),
    # E2 at W26: distance metric, non-attackers only once the striker has left.
    (55,  {"L": W26}, {}),                                         # wake it
    (58,  {},         {"L:M": (10, 23), "L:N": (13, 23)}),         # M (3,3), N (0,3): Chebyshev tie
    (66,  {},         {"L:S": (6, 26)}),                           # striker leaves
    (73,  {},         {"L:N": (12, 19)}),                          # swap: N steps out ...
    (76,  {},         {"L:M": (13, 23)}),                          # ... M to (0,3) ...
    (80,  {},         {"L:N": (10, 23)}),                          # ... N to (3,3)
    (90,  {},         {"L:M": (11, 24), "L:N": (10, 26)}),         # B: M (2,2) vs N (3,0)
    # E3 at W21: ties between non-attackers, across teams and within one.
    (100, {},         {"L:S": (10, 18), "L:M": (7, 21), "L:N": (7, 24)}),
    (109, {"L": W21}, {}),                                         # wake it
    (112, {},         {"L:M": (11, 21), "R:M": (15, 21)}),         # a: M id 2 (2,0) vs enemy M id 6 (2,0)
    (122, {},         {"R:M": (20, 21), "L:N": (13, 19)}),         # b: M id 2 (-2,0) vs N id 3 (0,-2)
    (132, {},         {"L:M": (9, 24)}),                           # swap: M steps out ...
    (134, {},         {"L:N": (11, 21)}),                          # ... N to (-2,0) ...
    (138, {},         {"L:M": (13, 19)}),                          # ... M to (0,-2)
    # E4 at W16: what puts a camp to sleep.
    (150, {},         {"L:S": (10, 16), "L:M": (7, 21), "L:N": (7, 24)}),
    (156, {"L": W16}, {}),                                         # three hits
    (159, {},         {}),                                         # no more damage, striker stays in reach
    (169, {},         {"L:S": (5, 16)}),                           # striker leaves: nobody in reach
    (176, {},         {"L:M": (11, 16)}),                          # M walks up to the (sleeping?) wolf
]


def plan(round_number, side, key):
    tile, wolves = None, {}
    for start, targets, tiles in SCHEDULE:
        if start > round_number:
            break
        wolves = targets
        if key in tiles:
            tile = tiles[key]
    return wolves.get(side), (Vec2(*tile) if tile is not None else None)


def draft_turn():
    ally = [int(p) for p in cambc.draft.current_draft(Team.ALLY)]
    enemy = [int(p) for p in cambc.draft.current_draft(Team.ENEMY)]
    taken = set(ally) | set(enemy)
    role_index = len(ally)
    for prefer_free in (True, False):
        for role, picks in ROLE_PICKS[role_index:] + ROLE_PICKS[:role_index]:
            for p in picks:
                if int(p) in ally or (prefer_free and int(p) in taken):
                    continue
                try:
                    cambc.draft.pick(p)
                    return
                except Exception:
                    continue


class Probe:
    def __init__(self):
        self.side = None
        self.role = None

    def setup(self, me):
        self.side = "L" if min(t.x for t in game_map.spawn_region(Team.ALLY).positions()) < 15 else "R"
        name = str(cambc.ChampionType.to_string(int(me.champion_name()))).split(".")[-1]
        self.role = ROLE_OF.get(name, "X")
        try:
            cdm = me.base_stats().camp_damage_multiplier
        except Exception:
            cdm = -1
        print("P init %d %s %s %d %s %s" % (cambc.round_number(), self.side, self.role, int(me), name, cdm), flush=True)

    def turn(self):
        me = cambc.me()
        if self.side is None:
            self.setup(me)
        r = cambc.round_number()
        tag = "%d %s %s %d" % (r, self.side, self.role, int(me))
        pos = me.position()
        print("P s %s %.1f %d %d" % (tag, me.health(), pos.x, pos.y), flush=True)
        for t in cambc.vision.awake_camp_tiles().positions():
            try:
                hp = cambc.vision.camp_at(t).health()
            except Exception:
                hp = -1
            print("P a %s %d %d %.1f" % (tag, t.x, t.y, hp), flush=True)
        if not me.is_alive():
            return
        wolf, tile = plan(r, self.side, self.side + ":" + self.role)
        if tile is not None and pos != tile:
            try:
                me.move_towards(tile)
            except Exception as error:
                print("P x %s move %s" % (tag, error), flush=True)
        if self.role == "S" and wolf is not None and me.can_attack():
            try:
                camp = cambc.vision.camp_at(wolf)
                if camp.health() > 0 and me.attack_tiles().is_set(wolf):
                    me.attack(camp)
                    print("P h %s %d %d" % (tag, wolf.x, wolf.y), flush=True)
            except Exception as error:
                print("P x %s hit %s" % (tag, error), flush=True)
        pos = me.position()
        print("P e %s %d %d" % (tag, pos.x, pos.y), flush=True)


def main():
    probe = Probe()
    while True:
        try:
            if cambc.is_draft():
                draft_turn()
            else:
                probe.turn()
        except Exception as error:
            print("P x error %s" % error, flush=True)
        cambc.end_turn()


if __name__ == "__main__":
    main()
