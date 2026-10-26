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
FLEE_HEALTH = 0.45          # flee is forced below this when an enemy is visible (0.30 before v30; band: our retreats started at 67% health and 17.5% died, the band's at 86% and 3%)
RECALL_HEALTH = 0.60        # recall to heal below this when no enemy is near (a death costs 60 rounds,
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
ALTAR_LOCK_PENALTY = 8      # walking turns a remembered enemy altar lock adds to that altar in the choice [5-15]: five hits
                            # break it (rules), so a locked altar nearby still beats a far one. Locks are remembered once
                            # seen (reverse spiral, ranked: a lock seen only within 8 tiles flipped the choice every step,
                            # Ryth paced two tiles for 600 rounds with 35 gems)
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
GOLD_VALUE = 0.1            # (0.2 in v32: band banked 24 by r300 vs 35, same wins; reverted) gems win games; gold only buys shards (150 gold = one emblem). Camps
                            # now pay about ten gold per gem, so this keeps gold a minority of value.
CAPTURE_TURNS = 5
DEATH_MEMORY = True         # a group this body died at lately scores lower (decide.score_camp) ...
DEATH_MEMORY_ROUNDS = 300   # ... counting deaths within this many rounds ...
DEATH_MEMORY_FACTOR = 0.5   # ... each multiplying the score by this ...
DEATH_MEMORY_DROP = 3       # ... and this many deaths drop the group altogether
CAPTURE_FIGHT_BALANCE = 0.9 # on a contested region, fight the intruder unless clearly weaker than this ...
CAPTURE_STANDOFF_TURNS = 12 # give a contested region up after this many turns face to face ...
CAPTURE_STANDOFF_AVOID = 60 # ... and leave it alone for this long
CAPTURE_BLIND_HOLD = True   # ... the lowest claiming slot keeps standing on it (denial), the others leave (actions.capture)
CAPTURE_BLIND_TURNS = 14    # standing on a cleared region this long with no reward and no enemy in sight means an enemy
                            # we cannot see holds another tile of it (yellow brick road: a 30-tile row, ExCode parked one
                            # body at its far end and two of ours waited 1,900 rounds): give it up like a standoff
BOSS_TRIPS = False          # a group too strong for the whole team in one trip is planned over several (decide.score_camp) ...
BOSS_MIN_GEMS = 150         # ... when it is worth at least this many gems (golem groups: 200-470) ...
BOSS_MAX_TRIPS = 4          # ... in at most this many trips, each costing the walk home and back plus
BOSS_TRIP_OVERHEAD = 12     # ... this many turns (recall and healing)
BOSS_FACTOR = 0.6           # score multiplier for such a plan
CAPTURE_PARTY = False       # (off: working, after the byte-6 fix, it lost 32-46 (-78) vs v15; on 47-31 off) a capturer with an enemy on its region posts the group in byte 6; allies join it
CAPTURE_PARTY_FRESH = 6     # rounds a party call counts
CAPTURE_SUPPORT_TRAVEL = 12 # allies within this many walking turns join (the standoff doubles once one arrives)
MAX_CAMP_CANDIDATES = 12
MAX_CAMP_TURNS = 60         # ignore camps further than this
CLAIMED_PENALTY = 0.35      # score multiplier when another ally has claimed the group
DANGER_WEIGHT = 0.8         # score multiplier slope per unit of (damage taken / health)
RESPAWN_WAIT_TURNS = 6      # accept arriving this many turns before a camp respawns
MAX_HELPERS = 3             # plan on at most this many allies joining a camp we cannot solo
HELP_DISCOUNT = 0.8         # score multiplier for plans that depend on allies turning up
HELP_WAIT_RANGE = 4         # allies within this distance of the camp count as present
HOLD_MAX_TURNS = 25         # give up waiting for allies after this long (three times that if someone has answered) ...
# Cooperation through the claims board (no extra bytes): a holder posts IDLE with its group as a call
# for help, helpers score that group up, and byte 6 names the group an enemy was last seen at.
COOP = True
HELP_CALL_BONUS = 1.6       # score multiplier for a group an ally is holding at for help ...
HELP_CALL_MAX_TRAVEL = 25   # ... when it is within this many walking turns
LOG_HELP = True             # print a line when a body answers a call (cheap: once per retarget)
PAIR_CALL = False           # (off: 41-37 vs v11 but 34-44 -81 vs v10) a lone farmer with a stronger enemy close posts the help call (actions.farm) ...
PAIR_CALL_BALANCE = 1.0     # ... when our local power over theirs is below this
CONTEST_SHARE = False       # post the group an enemy is seen at (bot.py; off: alone it cost 63 gems a game vs v11) ...
CONTEST_RANGE = 6           # ... when the enemy is within this of us ...
CONTEST_NEAR = 8            # ... and we are within this of the group's anchor
CONTEST_MEMORY = 64         # rounds a contested mark counts
CONTEST_SOLO_FACTOR = 1.0   # a contested group scores this much for a lone body ...
CONTEST_PAIR_BONUS = 1.0    # ... and this much more once an ally is on it
HOLD_AVOID_ROUNDS = 60      # ... and leave that group alone for this long
MIN_CAMP_SCORE = 0.05
RETARGET_EVERY = 10         # re-rank camps at least this often while farming
CAMP_CACHE_TURNS = 5        # reuse the camp candidate list for this many turns unless something happens
LOG_DECISIONS = False       # print the camp ranking at every retarget (costs fuel; dev only)
TRACE_SLOT = -1             # print a per-turn trace line for this body slot (dev only, -1 = off)
LOG_GROUPS = False          # print the camp group table on the first turn (dev only)
CAMP_NEAREST_RULE = True    # camp_damage_incoming counts an awake camp only when we are its nearest champion in reach (a tie half)
MACRO = False               # phase 4: the draft's macro plan (macro.py): routes, pairs, bank altars, ward spots (on, engage off: 31-47, -73 a game vs v42; off: 39-39, +69)
MACRO_FUEL = 6_000_000      # ... made on the first draft turn only when this much fuel is left
MACRO_CENTRAL_BAND = 4      # ... a group within this many walking turns of equal from both spawns is central (map class)
MACRO_SIDE_MARGIN = 2       # ... our side: groups (and altars) at most this many turns further from us than from them
MACRO_ROUTE_LEN = 6         # ... groups per pair's route ...
MACRO_ROUTE_STEP = 4        # ... taken by value / (this + tiles from the route's last group)
MACRO_WARD_LEAD = 3         # ... the ward spot: on the line to their spawn where they are this many turns further than us
MACRO_ROUTE_BONUS = 1.5     # a group on our pair's route scores this much more ...
MACRO_PARTNER_BONUS = 1.5   # ... and one a partner claims this much more (and is not a stack)
MACRO_ALTAR_SLACK = 6       # the pair's bank altar counts as this many turns nearer than it is
MACRO_WARDS = False         # the ward goes on the pair's planned spot (actions.ward_plan), not wherever we stand (on: 39-39, +2 a game vs v42; off: 45-33, +86)
WARD_PLACE_RANGE = 2        # a ward goes at most this far from us (GameRules)
ENGAGE_RULE = False         # phase 3b: one engage rule (decide._engage), no flee mode, the guard step (Actions.guard) (on: 25-53, -280 a game vs v42; off: 39-39, +69)
ENGAGE_RESCORE_TURNS = 4    # ... in contact, not fighting: the objective is re-scored every this many turns
ENGAGE_KILL_BALANCE = 0.6   # ... a weak enemy close by is run down unless the balance is below this
ENGAGE_JOIN_BALANCE = 0.8   # ... an ally already in a fight beside us is joined at this balance or better
GUARD_MIN = 0.5             # the guard steps away when the damage cost where we stand exceeds this ...
GUARD_FARM_VALUE = 1.5      # ... or this, at a camp in reach of our attack ...
GUARD_CAPTURE_VALUE = 3.0   # ... or this, standing on a capture
FOCUS_FIRE = False          # phase 3c: a fighting body posts its target (byte 7 bit 7 + the sighting bytes); allies in the fight prefer it (on: 42-36, -1 a game, deaths 1,286 vs v42; off: 44-34, +62, 1,124)
MICRO = True                # the threat layer (micro.py) picks every step taken with danger about
MICRO_THREAT_WEIGHT = 10.0  # cost of damage that could land next turn, per share of our effective health ...
MICRO_FLEE_WEIGHT = 20.0    # ... while retreating ...
MICRO_FIGHT_WEIGHT = 0.0    # ... and in a fight (0: off, the fight steps to its target; 3: 41-37, -50 a game vs v42)
MICRO_PROGRESS_WEIGHT = 1.0 # value of one walking turn gained towards the objective
MICRO_SOFT_RANGE = 2        # an enemy this far beyond its reach next turn still counts ...
MICRO_SOFT_SHARE = 0.25     # ... at this share of its damage
MICRO_SIGHTING_FRESH = 6    # rounds the shared enemy sighting counts as a threat (fading) ...
MICRO_SIGHTING_RANGE = 12   # ... when it is this close to us
MICRO_ALLY_NEAR = 4         # allies this close to a tile support it ...
MICRO_ALLY_CAP = 2          # ... counted up to this many ...
MICRO_ALLY_WEIGHT = 0.3     # ... at this value each
MICRO_ALLY_AGE = 2          # board positions older than this (rounds) are not used for allies
MICRO_HORIZON = 2           # outnumbered (balance below 1): enemies' soft reach runs this far beyond reach next turn (5, towards sight range: 36-42 vs v42, deaths 1,204; 2: 44-34, 1,118)
MICRO_MAX_SOURCES = 4       # fixed budget: the nearest enemies scored ...
MICRO_MAX_CAMPS = 4         # ... and the nearest awake camps
MICRO_BACKTRACK = 0.2       # stepping back onto the tile we just left costs this (stops pacing)
LOG_BOARD = False           # print each body's shared-memory picture every turn (debug builds only; board_check.py)

# ---- Fighting ----------------------------------------------------------------
FIGHT_POWER_RATIO = 1.25    # fight when our local power exceeds theirs by this factor
FIGHT_RANGE = 6             # consider enemies within this Chebyshev distance
KILL_STEAL_HEALTH = 0.40    # attack an enemy below this regardless of balance (kills pay 100 gold + bag)
FLEE_POWER_RATIO = 0.7      # flee when our power is below this fraction of theirs
FLEE_ENEMY_RANGE = 5
SPAWN_SITTERS_DISTANT = True  # an enemy on its own spawn is neither a target nor (beyond distance 1) a threat (world)
THREAT_DETOUR = 4           # an enemy in sight whose walk to us is more than this longer than its straight-line distance,
                            # and out of its own attack reach, is not a threat: it goes to w.distant_enemies (reverse spiral:
                            # a body stood in flee for 1,950 rounds from three enemies behind the spiral wall)
THREAT_FIELD_RANGE = 14     # how far the walking field for that test is explored ...
THREAT_FIELD_MAX_AREA = 4096  # ... only on maps up to this many tiles (a field allocates the whole board) and only when a
THREAT_FIELD_REUSE = 6      # flee rule could fire; it is reused for this many rounds while the body stands still
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
USE_NN_MODE = False         # the mode selector in weights.MODE_SELECTOR decides when it is sure and budget.fits (off in
                            # samit-sucks: 36-44 against v42 with both live heads off, inside the mirror's noise)
USE_NN_CAMP = False        # the camp scorer in weights.CAMP_SCORER ranks the camp candidates at retargets
CAMP_SCORER_FUEL = 90_000   # fuel per candidate (plus one) the scorer needs in hand: features + dot about 70K each, 80K tower
NN_CAMP_TOP = 8             # the scorer re-ranks only the script's best this many candidates
USE_NN_ESCAPE = False       # the escape head in weights.ESCAPE_HEAD picks the flee step (actions._escape_step)
ESCAPE_FUEL = 400_000       # fuel the escape head needs in hand (stay + ESCAPE_TOP tiles at ~60K each)
ESCAPE_TOP = 4              # neighbours the escape head scores, the furthest from the nearest threat
USE_NN_CONTACT = False      # the contact head in weights.CONTACT_HEAD (attack / close / hold / retreat with an enemy within
                            # CONTACT_RANGE), trained by imitation of the band's own turns (tools/train_contact_head.py); it
                            # decides above CONTACT_FLOOR health, the script's hard rules keep the floor (decide._contact_head)
CONTACT_RANGE = 4
CONTACT_FLOOR = 0.25        # below this health the FLEE_HEALTH rule stands whatever the head says [0.2-0.35]
CONTACT_FUEL = 250_000      # fuel the contact head needs in hand (24 features, 24-16-4 forward)
USE_NN_FIGHT = False        # the fight head in weights.FIGHT_HEAD picks the enemy to attack (decide.choose_fight_target)
FIGHT_FUEL = 300_000        # fuel the fight head needs in hand (up to FIGHT_HEAD_MAX enemies)
FIGHT_HEAD_MAX = 6
NN_MIN_MARGIN = 8           # fall back to the script when the top two scores are this close
NN_SKIP_ROUNDS = 15         # no network in the opening (setup is still finishing)

# ---- Fuel plan (budget.py) -----------------------------------------------------
# Upper ends (about p99) of v42's profiled turns, 40 maps. Nothing branches on the fuel left.
FIELD_COST_PER_TILE = 80        # a DistanceField over the map costs about this per tile (750K at 100x100)
EST_REFRESH = 600_000           # refresh with nothing in sight (p50 290K, p99 680K) ...
EST_REFRESH_ENEMY = 80_000      # ... plus this per enemy in sight ...
EST_REFRESH_CAMP = 20_000       # ... and this per camp in sight
EST_DECIDE = 1_000_000          # the script's mode choice (p50 185K, p99 1.05M)
EST_RETARGET_GROUP = 50_000     # a camp re-rank per group (24+ groups: p50 900K, p99 1.34M)
EST_MARGIN = 200_000            # kept free below the turn's 2.5M
FIRST_TURN_FUEL = 4_400_000     # the first turn of play (measured 4.41M-4.43M)
INIT_BASE = 150_000             # setup before the camp groups ...
INIT_PER_GROUP = 36_500         # ... camp_groups() and Group objects: per group ...
INIT_PER_CAMP = 16_000          # ... and per camp (fit over 38 maps, residual under 185K)
INIT_REST = 450_000             # spawn field (FIELD_COST_PER_TILE a tile) plus roster, abilities and plan
INIT_ANCHORS_PER_TURN = 6       # finish_init builds this many group anchors a turn, nearest first
THIN_HOLES_COST = 150_000       # bitboard shifts that find the holes a dash can cross (~130K at 80x80)
INIT_INLINE_MAX = 500_000       # on maps whose whole-map field is estimated above this (80x80 up: big world,
                                # ambush, expanse, starvation), every setup step gets a turn of its own ...
INIT_ANCHORS_ALONE = 12         # ... and those turns build this many anchors each
HEAVY_FIELD = 150_000           # a new field estimated at this or more is a turn's heavy job (navigation.field_to):
                                # maps from about 43x43 up
DASH_SAVE_STEPS = 6         # no speed-up dash when the route crosses a thin hole within this many steps (the dash is needed there)
HOLE_FAIL_LIMIT = 3         # failed hole crossings with the dash ready before the hole is treated as a wall
SEAL_ROUNDS = 150           # ... and for this long only (a seal made from inside a ring of holes trapped a body all game)
DASH_ROUTES = True              # champions with a dash route across thin holes (fields treat them as passable)
DASH_SQUARE_LANDINGS = True     # dash landings from the ability's range square (crosses holes) instead of walked tiles
LIVE_APPROACH = True        # a body at its camp's static ring with nothing in reach paths over live terrain (dead camps
                            # walkable) to the living camps, or next to the unseen tiles (actions._approach) ...
LIVE_APPROACH_RANGE = 20    # ... with a local field explored this far
OFF_WALL_RADIUS = 4         # a body deep inside a cleared cluster looks this far for a tile its route reaches (navigation.step_along)
STUCK_TURNS = 10            # turns without a move or an action before a body drops its target ...
STUCK_AVOID_ROUNDS = 20     # ... and leaves that group alone for this long (usually it is just crowded)
OSCILLATION_TURNS = 16      # a body walking between two tiles this long without an action counts as stuck too (the
                            # harness only saw same-tile stalls; yellow brick road had three bodies pacing 1,000+ rounds)
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
USE_NN_DRAFT_VALUE = False  # the draft value head in weights.DRAFT_VALUE drafts greedily by predicted margin (draft._value_pick)
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

# Ordered builds per champion: (family, stat, shards) bought in order; the last entry repeats for
# as long as there is gold (stats stack; only the emblem passive does not). Taken from the top-25
# teams' winning sides on engine 1.0.1 (item counts in the replay dumps, 2026-10-21): Treasure
# first (20% more gold), then health for the front line and focus for the casters, Bruiser shards
# for the rest. Our old all-strength builds ended at a third of their health (1,000 against
# 2,000-3,600 on the same champions) and died twice as often. USE_BUILD_ORDER = False restores
# BUILD_STAT and SHOP_FAMILIES.
USE_BUILD_ORDER = True
STAND_GROUND = True         # a flee with a chaser in reach turns into a fight when running would not help (decide.should_stand) ...
STAND_CHASE_RANGE = 3       # ... enemies this close are chasers ...
STAND_CHASE_TURNS = 6       # ... that keep hitting for at most this many turns of the walk home ...
STAND_FLEE_DEATH = 0.9      # ... stand if their damage over that walk would take this share of our health ...
STAND_FUTILE_MIN_HEALTH = 0.5  # ... (only above this health ratio [0.25-0.5]: without it 35-43 vs v13, deaths 1379 vs 843; the
                            # v35 band dumps say a melee body at 30-45% with a melee chaser adjacent died 32-36% of the time
                            # fleeing and 9-15% fighting, so this is the first knob of the tuning pass) ...
STAND_MIN_BALANCE = 0.0     # ... and only while the local power balance is at least this [0.0-0.9] (0 = no balance test) ...
STAND_ESCAPE = 1            # ... unless safety (own spawn, a bush, an ally) is within this many tiles [0-4] or a dash is ready ...
STAND_ALLY_RANGE = 4        # ... or if our side (us and allies this close) ...
STAND_EDGE = 1.0            # ... kills the weakest chaser in this share of the time it needs to kill us
DOOMED_RULE = False         # (off: 41-39 vs v35 with it, 51-29 without; deaths 947 vs 857) a body that dies within DOOMED_TURNS to the enemies already in reach, with no safety that close and no
DOOMED_TURNS = 2            # dash ready, never retreats: it fights for the most damage it can still do [1-3] (decide.should_stand,
                            # Tyler 2026-10-22: a guaranteed death is spent on damage for the team, not on running)
PARTING_SHOT = True         # a fleeing melee body hits an adjacent enemy BEFORE stepping away (the engine allows attack then move:
                            # probe 2026-10-22, 147 such turns; band dumps: 4,455 such flee turns, 245 hits with the step-first order)
FLEE_STRIKE_BACK = True     # a melee body that stepped away still hits a chaser in reach (actions.flee)
# The opening (challenge games vs teams 200 above: our bodies dead 20% of rounds 1-150 against
# their 8%; 165 early deaths, most a lone farmer that saw one enemy, kept farming while three or
# four gathered, and died fleeing after 10+ rounds of warning).
EARLY_ROUNDS = 400
EARLY_ALERT = False         # (off: v18 band, no fewer early deaths and 9 fewer gems banked by r300) a body with an enemy in sight posts its group in byte 6 (bot.py); idle allies come (decide) ...
ALERT_SIGHT = 8             # ... enemies this close count ...
ALERT_NEAR = 8              # ... posted only when we are this close to the group ...
ALERT_FRESH = 4             # ... answered while this fresh (rounds) ...
ALERT_TRAVEL = 12           # ... by allies within this many tiles of the group ...
ALERT_MIN_HEALTH = 0.6      # ... and at least this healthy
EARLY_LEAVE = False         # (off: with the alert fixed 29-49 (-46) vs v14) two enemies within ALERT_SIGHT, weaker, no ally within EARLY_LEAVE_ALLY_RANGE: flee now (no stand)
EARLY_LEAVE_ALLY_RANGE = 5
EARLY_HOLD_MAX = 0          # (off: no measurable effect on the band; 10 when on) in the opening wait at most this long beside a camp for helpers (three-body clears took 46 turns, the band's 16)
# Per champion (GameRules kits): damage a turn = strength x STR + focus x FOCUS, health counted with
# shields = health + focus x SHIELD. Abilities: 8-turn cooldowns, one cast a turn, so a spell of
# k x focus adds k/8 a turn; an attack reset adds 1/8 of strength. Used when CHAMPION_POWER is on.
CHAMPION_POWER = True
CHAMPION_KIT = {   # name: (STR, FOCUS, SHIELD)
    "CASSI": (1.0, 0.25, 0.0), "ISSAC": (1.125, 0.0, 0.0), "RYTH": (1.125, 0.125, 0.0),
    "VESPER_VALE": (1.125, 0.0, 0.0), "PRISM": (1.0, 0.31, 0.0), "STELLAR": (1.0, 0.43, 0.0),
    "VENDETTA": (1.125, 0.0, 0.0), "MARCELLA": (1.125, 0.25, 0.0), "TAVI": (1.125, 0.18, 0.0),
    "WILLOW": (1.125, 0.0, 0.0), "REN": (1.0, 0.28, 0.0), "VARG": (1.25, 0.0, 2.5),
    "HEART_OF_RUIN": (1.0, 0.22, 1.5),
}
# Band games, 129K contact samples: kills matched deaths only at a kit-power balance of about 2
# (1.0-1.2: we lost two bodies per kill). The rules' balance is scaled by this so 1.0 means even.
CAPTURE_OWN_CLEAR = True    # capture a group we just cleared right away (decide.choose_mode) ...
CAPTURE_OWN_TRAVEL = 2      # ... when it is this close ...
CAPTURE_OWN_BALANCE = 0.7   # ... unless the enemies there outweigh us (calibrated balance below this)
CAST_BEFORE_STEP = True     # in a fight, cast a spell that already reaches the target before stepping (actions.fight)
CHASE = True                # run down a weak enemy close by (decide._chase_rule) ...
CHASE_RANGE = 4
CHASE_HP = 0.35             # ... at or below this share of its health ...
CHASE_MIN_HEALTH = 0.5      # ... when we have this much ...
CHASE_TURNS = 6             # ... and keep after it this long
POWER_CALIBRATION = 0.5     # band (70 paired games): v26 at 0.5 won 12, deaths 21.6; v25 at 1.0 won 9, deaths 24.8
NN_BALANCE_SCALE = 1.0      # scale of the strength-only balance the selector sees (its training scale is 1.0)
FOCUS_POWER_WEIGHT = 0.25   # power = health x (strength + this x focus) in fight rules (world.power_balance); 0 = strength only
SPAWN_STRIKE = True         # a body held on our spawn hits enemies in reach (actions.recall)
NO_RECALL_IN_CONTACT = True # no recall (and cancel one) with an enemy within RECALL_CONTACT_RANGE at RECALL_CONTACT_HEALTH+
RECALL_CONTACT_RANGE = 5
RECALL_CONTACT_HEALTH = 0.5
FIGHT_LIVE_FIELD = True     # fight steps path over terrain + standing camps, not every camp tile (navigation.live_field)
PAIR_ROUNDS = 2000          # the pair rules below apply this long (join all game: 45-33 (+53) vs v28 in the mirror; 400 = opening only)
PAIR_JOIN = True            # an ally within PAIR_JOIN_RANGE has an enemy within 3 of it and one is near us: fight beside it ...
PAIR_JOIN_RANGE = 6
PAIR_MIN_HEALTH = 0.5       # ... when we are at least this healthy
REGROUP = False             # (off: 35-43 (-69) vs v19 alone) first sighting, no ally within 4 but one within REGROUP_RANGE: step to it ...
REGROUP_RANGE = 8
REGROUP_TURNS = 3           # ... for at most this many turns
WARD_OFTEN = True           # a ward whenever the charge is back (actions.ward_often) ...
WARD_OFTEN_EVERY = 20       # ... at most every this many rounds ...
WARD_OFTEN_SPACING = 5      # ... and this many tiles from our last ward, never on our spawn
EARLY_PAIRS = True          # (mirror: 33-45 alone vs v14, 40-38 -36 gems on top of wards; on for the band test) in the first rounds a camp with one ally on it scores up instead of down (decide.score_camp) ...
EARLY_PAIR_ROUNDS = 300
EARLY_PAIR_BONUS = 1.3
ELK_BONUS = 1.15            # ... and a paired group with an elk a little more
TURN_PRIORITY = True        # bodies facing an enemy act first within the team (bot._set_priority) ...
PRIORITY_ENEMY_RANGE = 6    # ... when the nearest enemy is within this many tiles
_FOCUS_BUILD = (("TREASURE", "FOCUS", 5), ("POWER", "FOCUS", 5), ("BRUISER", "HEALTH", 5), ("BRUISER", "FOCUS", 99))
BUILD_ORDER = {
    "HEART_OF_RUIN": _FOCUS_BUILD,
    "STELLAR": _FOCUS_BUILD,
    "PRISM": _FOCUS_BUILD,
    "WILLOW": _FOCUS_BUILD,
    "CASSI": (("POWER", "FOCUS", 5), ("DURABILITY", "FOCUS", 5), ("BRUISER", "HEALTH", 99)),
    "REN": (("BRUISER", "HEALTH", 5), ("TREASURE", "STRENGTH", 5), ("DURABILITY", "HEALTH", 5), ("BRUISER", "STRENGTH", 99)),
    "RYTH": (("TREASURE", "HEALTH", 5), ("BRUISER", "HEALTH", 5), ("DURABILITY", "HEALTH", 5), ("BRUISER", "STRENGTH", 99)),
    "MARCELLA": (("TREASURE", "FOCUS", 5), ("JUNGLE", "STRENGTH", 5), ("BRUISER", "HEALTH", 5), ("BRUISER", "STRENGTH", 99)),
    "VARG": (("JUNGLE", "STRENGTH", 5), ("TREASURE", "HEALTH", 5), ("BRUISER", "HEALTH", 5), ("BRUISER", "STRENGTH", 99)),
    "VESPER_VALE": (("BRUISER", "HEALTH", 5), ("MONSTER", "STRENGTH", 5), ("TREASURE", "STRENGTH", 5), ("BRUISER", "STRENGTH", 99)),
    "ISSAC": (("TREASURE", "STRENGTH", 5), ("POWER", "STRENGTH", 5), ("DURABILITY", "HEALTH", 5), ("POWER", "STRENGTH", 99)),
    "VENDETTA": (("JUNGLE", "HEALTH", 5), ("TREASURE", "HEALTH", 5), ("BRUISER", "HEALTH", 5), ("BRUISER", "STRENGTH", 99)),
    "TAVI": (("TREASURE", "STRENGTH", 5), ("JUNGLE", "HEALTH", 5), ("BRUISER", "HEALTH", 5), ("BRUISER", "STRENGTH", 99)),
}

# ---- self-play (tools/selfplay_modes.py) -----------------------------------------------------
SQUADS = False              # (off: mirror 28-50 to 37-41 vs v33 over five sweeps, gems per 100 rounds 39-42 vs 46-51; deaths level or lower) fixed squads by slot share one target and move as a unit (world._init_squads, decide, actions) ...
SQUAD_SIZE = 2              # ... pairs; a remainder joins the last squad (5 bodies: 2 + 3) ...
SQUAD_ALL_MIDDLE = 0.8      # ... the whole team is one squad when this share of the groups sits on the centre line ...
SQUAD_MIDDLE_TOLERANCE = 3  # ... (a group's distances to the two spawn centres differ by at most this)
SQUAD_FOLLOW_TRAVEL = 25    # a follower takes its leader's group when it is within this many walking turns
SQUAD_JOIN_BONUS = 1.5      # the leader's score multiplier for a group a squad mate already claims (instead of CLAIMED_PENALTY)
SQUAD_RETREAT_ROUNDS = 2    # a follower retreats once its leader has been retreating this many rounds running (one-turn
                            # flee flips at balance 1 scattered whole squads in the first sweep)
SQUAD_WAIT_FOR = 1          # a hold at a camp ends once this many squad mates are beside it (a triple waiting for its
                            # third body idled 20% of its turns)
SQUAD_CAPTURE_WAIT = 8      # a follower beside a group its leader is capturing waits this many turns instead of leaving
SQUAD_LOG = True            # one line when a follower adopts a target or a squad retreat fires (cheap)
BREAKOUT_SIGNAL = False     # (off: alone vs v33 35-43, deaths 915 vs 866) the spawn gate's break-out byte (v8): decide.py never
                            # imported memory_codec, so the write and the read raised inside try/except and every gated body left on its own test
# Fight commitment and kiting (2026-10-22). Values in [brackets] are the ranges a later tuning pass
# should search; every constant here is a candidate for tools/tune.py or a ladder arm.
FIGHT_COMMIT = True         # a fight or flee decision is held (decide._commitment) ...
FIGHT_COMMIT_TURNS = 3      # ... for this many turns [2-5] unless health crosses FLEE_HEALTH or the enemy is gone ...
FLEE_COMMIT = False         # ... retreats too (off: one-turn flee flickers became three-turn retreats, median 1 to 3, and
                            # deaths rose 880 to 931 vs v34; retreating is where our bodies die) [ladder arm candidate]
OUTNUMBERED_TURNS = 2       # the outnumbered rule fires after this many turns running with 2+ enemies near [1-3]: the
                            # flicker came from the enemy count crossing 2 every other turn
FIGHT_STAY_RATIO = 0.9      # ... a fight goes on while the calibrated balance stays above this [0.7-1.1]; entry needs FIGHT_POWER_RATIO
FLEE_EXIT_RATIO = 1.25      # ... a retreat ends early once the balance is back above this [1.0-1.5] with FIGHT_MIN_HEALTH in hand
KITE = True                 # a ranged body in a fight dashes away from an adjacent melee enemy, then attacks (actions._kite):
                            # a dash is an ability, so unlike a step it does not use the ranged move-or-attack allowance (probe
                            # 2026-10-22: dash then attack ran, step then attack raised "Attack already used")
KITE_MIN_GAP = 2            # ... landing at least this far from every melee enemy [2-3], target still in attack range
MODE_EXPLORE = 0.0          # data runs only: probability the chosen mode is swapped for another legal one
MODE_EXPLORE_SALT = 0       # varies per sweep so the same turn explores differently
MODE_EXPLORE_HOLD = 3       # an explored mode is held this many turns (one-turn deviations have no measurable outcome)
USE_ADVERSARY_WEIGHTS = False  # the adversary profile plays the selector in weights_adv.py instead of weights.py

# An exported profile (replays/selfplay/bots/<name>/src/profile.py) overrides any constant above.
try:
    from profile import *  # noqa: F401,F403
except ImportError:
    pass
