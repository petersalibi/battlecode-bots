"""Tunable thresholds, weights and switches.

Plain numbers only. This module is imported at build time, so it must not touch the engine.
Every value here is a candidate for offline tuning by batch matches.
"""

# ---- Modes -------------------------------------------------------------------
FARM, CAPTURE, TRIBUTE, RECALL, FIGHT, FLEE = range(6)
MODE_NAMES = ("farm", "capture", "tribute", "recall", "fight", "flee")
N_MODES = 6

# Bonus added to the current mode's score so the selector does not flip-flop.
MODE_STICKINESS = 0.15

# ---- Health thresholds (ratios of max health) --------------------------------
FLEE_HEALTH = 0.30          # flee is forced below this when an enemy is visible
RECALL_HEALTH = 0.50        # recall to heal below this when no enemy is near (a death costs 60 rounds,
                            # the bag and 100 gold to the killer; a recall costs about 15 turns)
FIGHT_MIN_HEALTH = 0.55     # never start a fight below this
CAMP_MIN_HEALTH = 0.45      # never start a new camp below this
SPAWN_LEAVE_HEALTH = 0.95   # stay on spawn until healed to this
HEALTH_RESERVE = 0.20       # keep this much health in hand after clearing a camp
CAMP_FLEE_HEALTH = 0.45     # below this, a camp fight that cannot be finished is abandoned ...
CAMP_FLEE_RESERVE = 0.15    # ... when finishing it would leave less than this
CAMP_FLEE_AVOID_ROUNDS = 40 # and that group is left alone for this long

# ---- Tribute -----------------------------------------------------------------
# Week 2 patch: a wolf pays 4 gems, an elk 15, a gem cluster 7, and a kill pays 100 gold, so
# bags are small and carriers are hunted. Bank often.
TRIBUTE_BAG = 24            # bank once carrying this many gems
TRIBUTE_BAG_NEAR = 8        # ... or this many when an altar is close
TRIBUTE_NEAR_TURNS = 5      # "close" means within this many walking turns
TRIBUTE_BAG_HURT = 6        # bank anything above this when health is low
ENDGAME_ROUNDS = 120        # bank everything in the last rounds of the game
TRIBUTE_SCORE_PER_GEM = 0.04

# ---- Shop and recall ----------------------------------------------------------
SHARD_COST = 30
RECALL_SHOP_GOLD = 150      # recall to spend gold once carrying this much and the bag is empty
RECALL_MIN_SPAWN_TURNS = 8  # walking home is better than recalling when closer than this
RECALL_SAFE_ENEMY_TURNS = 5 # do not start a recall with an enemy closer than this

# ---- Camp scoring ------------------------------------------------------------
GEM_VALUE = 1.0
GOLD_VALUE = 0.1            # gems win games; gold only buys shards (150 gold = one emblem). Camps
                            # now pay about ten gold per gem, so this keeps gold a minority of value.
CAPTURE_TURNS = 5
DEATH_MEMORY = True         # a group this body died at lately scores lower (decide.score_camp) ...
DEATH_MEMORY_ROUNDS = 300   # ... counting deaths within this many rounds ...
DEATH_MEMORY_FACTOR = 0.5   # ... each multiplying the score by this ...
DEATH_MEMORY_DROP = 3       # ... and this many deaths drop the group altogether
CAPTURE_FIGHT_BALANCE = 0.9 # on a contested region, fight the intruder unless clearly weaker than this ...
CAPTURE_STANDOFF_TURNS = 12 # give a contested region up after this many turns face to face ...
CAPTURE_STANDOFF_AVOID = 60 # ... and leave it alone for this long
MAX_CAMP_CANDIDATES = 12
MAX_CAMP_TURNS = 60         # ignore camps further than this
CLAIMED_PENALTY = 0.35      # score multiplier when another ally has claimed the group
DANGER_WEIGHT = 0.8         # score multiplier slope per unit of (damage taken / health)
RESPAWN_WAIT_TURNS = 6      # accept arriving this many turns before a camp respawns
MAX_HELPERS = 3             # plan on at most this many allies joining a camp we cannot solo
HELP_DISCOUNT = 0.8         # score multiplier for plans that depend on allies turning up
HELP_WAIT_RANGE = 4         # allies within this distance of the camp count as present
HOLD_MAX_TURNS = 25         # give up waiting for allies after this long ...
HOLD_AVOID_ROUNDS = 60      # ... and leave that group alone for this long
MIN_CAMP_SCORE = 0.05
RETARGET_EVERY = 10         # re-rank camps at least this often while farming
CAMP_CACHE_TURNS = 5        # reuse the camp candidate list for this many turns unless something happens
LOG_DECISIONS = False       # print the camp ranking at every retarget (costs fuel; dev only)
TRACE_SLOT = -1             # print a per-turn trace line for this body slot (dev only, -1 = off)
LOG_GROUPS = False          # print the camp group table on the first turn (dev only)

# ---- Fighting ----------------------------------------------------------------
FIGHT_POWER_RATIO = 1.25    # fight when our local power exceeds theirs by this factor
FIGHT_RANGE = 6             # consider enemies within this Chebyshev distance
KILL_STEAL_HEALTH = 0.40    # attack an enemy below this regardless of balance (kills pay 100 gold + bag)
FLEE_POWER_RATIO = 0.7      # flee when our power is below this fraction of theirs
FLEE_ENEMY_RANGE = 5
FLEE_DASH_AWAY = True       # a fleeing dash champion dashes away from the threats before stepping
SPAWN_GATE = True           # on spawn with a stronger enemy group in sight: stay (heal, safe) instead of walking out
SPAWN_GATE_RANGE = 10       # "in sight": an enemy within this of the spawn tile we stand on
SPAWN_BREAK_RATIO = 1.25    # the bodies on spawn leave together once their power is this times the campers' ...
SPAWN_GATE_MAX_TURNS = 60   # ... or after holding this long (the campers are farming our half meanwhile)
FLEE_GROUP = True           # two or more enemies count as near from further away ...
RISK_GATE = False           # the selector's death-risk lane (weights.MODE_SELECTOR_RISK) masks farm, capture and fight when high
OUTNUMBERED_RULE = True     # two or more enemies near and weaker than them: flee as a hard rule (above the network) ...
OUTNUMBERED_STAY_BALANCE = 1.0  # ... and keep fleeing while they are in sight and our power is below theirs times this
FLEE_GROUP_EXTRA = 3        # ... this much beyond FLEE_ENEMY_RANGE
CAMP_ENEMY_PENALTY = False  # camps where an enemy was seen lately score lower
CAMP_ENEMY_RANGE = 8        # "near": an enemy within this of the group's anchor ...
CAMP_ENEMY_MEMORY = 30      # ... seen within this many rounds ...
CAMP_ENEMY_FACTOR = 0.5     # ... multiplies the camp's score by this
CAMP_ENEMY_SIDE_FACTOR = 1.0  # below 1: a camp nearer the enemy spawn than ours is worth this much

# ---- Ward --------------------------------------------------------------------
WARD_REPLACE_EVERY = 40

# ---- Networks ----------------------------------------------------------------
DATALOG = False             # log features, scores and the chosen mode every turn (debug builds only)
USE_NN_MODE = True          # the mode selector in weights.MODE_SELECTOR decides when it is sure and fuel allows
USE_NN_CAMP = False        # the camp scorer in weights.CAMP_SCORER ranks the camp candidates at retargets
CAMP_SCORER_FUEL = 90_000   # fuel per candidate (plus one) the scorer needs in hand: features + dot about 70K each, 80K tower
NN_CAMP_TOP = 8             # the scorer re-ranks only the script's best this many candidates
USE_NN_ESCAPE = False       # the escape head in weights.ESCAPE_HEAD picks the flee step (actions._escape_step)
ESCAPE_FUEL = 400_000       # fuel the escape head needs in hand (stay + ESCAPE_TOP tiles at ~60K each)
ESCAPE_TOP = 4              # neighbours the escape head scores, the furthest from the nearest threat
USE_NN_FIGHT = False        # the fight head in weights.FIGHT_HEAD picks the enemy to attack (decide.choose_fight_target)
FIGHT_FUEL = 300_000        # fuel the fight head needs in hand (up to FIGHT_HEAD_MAX enemies)
FIGHT_HEAD_MAX = 6
NN_MIN_MARGIN = 8           # fall back to the script when the top two scores are this close
NN_FUEL_RESERVE = 1_000_000 # fuel that must remain after a network runs, plus 1.5 map fields (acting can build one)
NN_SKIP_ROUNDS = 15         # no network until the first retargets have shown what acting costs (exhaustions at round 12 before)
NN_ACT_MARGIN = 300_000     # added to the worst act cost seen when sizing the network's reserve
NN_GROUP_RESERVE = 40_000   # per camp group beyond NN_GROUP_FREE: re-ranking 46 groups (islands) costs 900K ...
NN_GROUP_FREE = 30          # ... while a flat per-group reserve cost five wins a sweep on ordinary maps

# ---- Fuel --------------------------------------------------------------------
FUEL_RESERVE = 250_000      # stop optional work below this much remaining fuel
FINISH_INIT_RESERVE = 500_000   # finish_init (run after acting) stops building group anchors below this
                                # much fuel and resumes next turn
FIELD_COST_PER_TILE = 80        # a DistanceField over the map costs about this per tile (750K at 100x100);
                                # fields are only built when that much plus the reserve is in hand
FIELD_BUILD_RESERVE = 1_000_000 # fuel that must remain after building an optional field mid-turn
THIN_HOLES_COST = 150_000       # bitboard shifts that find the holes a dash can cross (~130K at 80x80)
DASH_SAVE_STEPS = 6         # no speed-up dash when the route crosses a thin hole within this many steps (the dash is needed there)
HOLE_FAIL_LIMIT = 3         # failed hole crossings with the dash ready before the hole is treated as a wall
DASH_ROUTES = True              # champions with a dash route across thin holes (fields treat them as passable)
DASH_SQUARE_LANDINGS = True     # dash landings from the ability's range square (crosses holes) instead of walked tiles
STUCK_TURNS = 10            # turns without a move or an action before a body drops its target ...
STUCK_AVOID_ROUNDS = 20     # ... and leaves that group alone for this long (usually it is just crowded)
FIRST_TURN_MIN = 1_000_000  # end the first turn without acting if setup left less than this ...
FIRST_TURN_PER_TILE = 100   # ... plus this per map tile (field computes scale with the map area)
LOG_EVERY = 100             # rounds between fuel log lines (0 disables)
PROFILE_EVERY = 0           # rounds between per-phase fuel lines (0 disables)
CAPTURE_MEMORY_ROUNDS = 15  # trust a seen "reward available" state for this long

# ---- Draft -------------------------------------------------------------------
# Pick order (week 2 patch, 2026-10-20). Melee auto-attackers farm fastest in a damage race:
# Vendetta has the top strength and resets on camp kills, Marcella now earns gold per gem
# tributed and is stun-immune, Ryth and Heart of Ruin are durable melee with stuns, Varg's
# splash still clears packs. Ranged picks last: they cannot move and shoot.
DRAFT_PRIORITY = ("VENDETTA", "MARCELLA", "RYTH", "VARG", "VESPER_VALE", "HEART_OF_RUIN", "REN",
                  "WILLOW", "STELLAR", "TAVI", "PRISM", "CASSI_AND_ISSAC")
N_DRAFT_PICKS = 12          # ChampionDraft values 0..11 (Cassi & Issac are one pick)
USE_NN_DRAFT = False        # the draft head in weights.DRAFT_HEAD picks from the map (draft._net_pick)
USE_NN_DRAFT_VALUE = True   # the draft value head in weights.DRAFT_VALUE drafts greedily by predicted margin (draft._value_pick)
DRAFT_RANDOM = 0.0          # self-play exploration: probability each pick is a random allowed champion (data runs only, never submitted)
DRAFT_RANDOM_SALT = 0       # varies per exploration sweep so the same map draws different drafts
DRAFT_HEAD_FUEL = 8_000_000 # the map features need this much in hand (measured 3.2M on corners, 5.1M on big-world and islands): first draft turn only
DRAFT_VALUE_FUEL = 1_500_000  # the value head's greedy planning stops when less than this is left (each step is up to ten forwards)
DRAFT_LOG_FEATURES = False  # compute and log the map features even with every draft head off (parity checks)
# Champions the script can play; the draft head never picks outside this set.
DRAFT_ALLOWED = ("VENDETTA", "MARCELLA", "RYTH", "VARG", "VESPER_VALE", "HEART_OF_RUIN", "REN", "WILLOW", "TAVI", "CASSI_AND_ISSAC")
# Champions whose dash crosses holes, first on maps where holes wall the camps off (starvation,
# islands, shortcut, fracture, concentrated-wealth): a walker there has nothing to farm.
DASH_DRAFT_PRIORITY = ("VENDETTA", "VESPER_VALE", "HEART_OF_RUIN", "CASSI_AND_ISSAC", "REN")
DASH_MAP_RATIO = 2          # a map is a "dash map" when dashes at least double the camps within reach
DASH_MAP_FUEL = 4_000_000   # the dash-map check (two fields plus the camp groups) needs this much in hand
SMALL_MAP_TILES = 40 * 40   # maps smaller than this are "small": more contact, more aggression

# ChampionType order in the SDK (Cassi and Issac are separate bodies here).
CHAMPION_NAMES = ("RYTH", "VESPER_VALE", "VENDETTA", "CASSI", "ISSAC", "STELLAR", "PRISM", "MARCELLA",
                  "WILLOW", "REN", "VARG", "TAVI", "HEART_OF_RUIN")

# ---- Builds ------------------------------------------------------------------
# Per champion type name: which stat the shards buy. Emblem family order is in SHOP_FAMILIES.
BUILD_STAT = {
    "RYTH": "HEALTH", "VESPER_VALE": "STRENGTH", "VENDETTA": "STRENGTH", "CASSI": "FOCUS",
    "ISSAC": "STRENGTH", "STELLAR": "FOCUS", "PRISM": "FOCUS", "MARCELLA": "STRENGTH",
    "WILLOW": "FOCUS", "REN": "STRENGTH", "VARG": "STRENGTH", "TAVI": "STRENGTH",
    "HEART_OF_RUIN": "STRENGTH",
}
# Jungle first: 30% less camp damage is the biggest lever in a damage race against monsters.
SHOP_FAMILIES = ("JUNGLE", "MONSTER", "POWER", "DURABILITY", "TREASURE", "HOME", "BRUISER")
