"""Every tunable number in one short table, each with its reason. Nothing here is read to decide
whether there is fuel to spare: behaviour never depends on fuel (DESIGN.md)."""

# ---- Draft ----------------------------------------------------------------------
# Pick order. Pre-patch ladder data (hybrid's DRAFT_PRIORITY); revisit with post-patch games.
DRAFT_PRIORITY = ("VENDETTA", "MARCELLA", "RYTH", "VARG", "VESPER_VALE", "HEART_OF_RUIN", "REN",
                  "WILLOW", "STELLAR", "TAVI", "PRISM", "CASSI_AND_ISSAC")
N_DRAFT_PICKS = 12

# ---- The market (jobs) -----------------------------------------------------------
GEM_VALUE = 1.0             # gems win games ...
GOLD_VALUE = 0.1            # ... gold buys shards (150 gold an emblem); 0.2 cost banking on the band (v32)
HEALTH_RESERVE = 0.35       # a farm job must leave us this share of health after its expected damage
JOB_REVIEW_EVERY = 25       # rounds between reviews of a job that raised no event (respawns, drift)
SURVIVAL_DEATH_ROUNDS = 150 # deaths at a group this recent cut its score ...
SURVIVAL_PER_DEATH = 0.5    # ... by this factor each
SIGHTING_NEAR = 8           # a fresh enemy sighting this close to a group ...
SIGHTING_FACTOR = 0.6       # ... makes it worth this much (alone; a pair ignores it)
CLAIMED_FACTOR = 0.3        # a group another body farms is worth this much to us, unless it needs two
TWO_BODY_HEALTH = 0.6       # a group whose clear costs more than this share of our health wants two bodies
MAX_PARTY = 4               # at most this many bodies planned on one group
PARTY_DISCOUNT = 0.85       # each extra body a plan needs costs this factor (they must turn up)
PARTY_HOLD_TURNS = 25       # the first of a party waits outside camp reach at most this long for the rest
CAMP_DAMAGE_SHARE = 0.6     # a group's damage over its clear: camps die as it goes, so ~60% of full strength
FARM_MAX_TRIPS = 3          # a group whose clear needs more heal trips than this needs another body
ALTAR_WAIT = 12             # turns held off a contested altar before banking elsewhere ...
ALTAR_AVOID = 60            # ... and that altar is skipped this long
UNREACHABLE_FORGET = 150    # a target our field could not reach is skipped this many rounds (seals and holes change)
FARM_TRAVEL_MAX = 60        # groups further than this many walking turns are not candidates

# Banking: bank when what the bag risks exceeds what the trip costs (gem-turns).
BANK_RISK_PER_TURN = 0.004  # chance a turn of carrying ends in losing the bag (ladder: ~15 deaths in ~1,500 living turns ~ 1%; halved: most deaths carry little)
BANK_MIN_BAG = 6            # never walk to an altar for less
BANK_FULL_BAG = 40          # bank at this bag whatever the risk (a death loses it all)
BANK_ENDGAME_MARGIN = 30    # rounds of slack kept to bank before the round limit
HOME_HEALTH = 0.45          # below this, with nothing healing us, the job becomes HOME (v30: 45% had the fewest deaths)
HOME_SHOP_GOLD = 150        # go home to shop with this much gold and an empty bag (one emblem)
RECALL_MIN_TURNS = 8        # walking is better than recalling (5 turns, interrupted by damage) when home is nearer

# ---- Tactics -----------------------------------------------------------------------
THREAT_WEIGHT = 10.0        # cost of damage that could land next turn, per share of effective health (3a)
HOME_THREAT_WEIGHT = 20.0   # ... while going home hurt
PROGRESS_WEIGHT = 1.0       # value of one walking turn gained towards the job
SOFT_RANGE = 2              # an enemy this far beyond its next-turn reach still counts ...
SOFT_SHARE = 0.25           # ... at this share (3a; 5 tiles towards sight range lost: 36-42)
SIGHTING_FRESH = 6          # rounds the shared sighting counts as a threat, fading
SIGHTING_RANGE = 12
ALLY_NEAR = 4               # allies this close support a tile ...
ALLY_CAP = 2                # ... counted up to this many ...
ALLY_WEIGHT = 0.3           # ... at this value each, while threatened
BACKTRACK = 0.2             # stepping back onto the tile we just left (stops pacing)
MAX_SOURCES = 4             # fixed budget: the nearest enemies scored ...
MAX_CAMPS = 4               # ... and the nearest awake camps
POWER_CALIBRATION = 0.5     # kit power over theirs is "even" at 0.5x raw (band: kills matched deaths at ~2)
FIGHT_BALANCE = 1.0         # attack an enemy champion when our local calibrated power is at least this ...
KILL_HEALTH = 0.4           # ... or it is below this share of its health (kills pay 100 gold and its bag)
STAND_HEALTH = 0.5          # a body going home that cannot outrun its chasers fights back above this

# ---- Shared memory -------------------------------------------------------------------
CLAIM_REFRESH = 32          # rounds after which an unchanged claim is published again

# ---- Logging (debug builds only, never branched on) --------------------------------
LOG_JOBS = True             # one line per job change
TRACE_SLOT = -1             # print a line every turn for this body (dev only)
LOG_EVERY = 200             # rounds between status lines
