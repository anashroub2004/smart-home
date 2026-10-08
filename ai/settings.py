"""Constants and defaults. Every number here is an agreed project decision —
change them only after the team agrees (and update docs/PROJECT_CONTEXT.md + the skill)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "docs" / "seed.json"
DATA_DIR = ROOT / "ai" / "data"          # SQLite lives here on the Pi (git-ignored)
MODEL_DIR = ROOT / "ai" / "models"       # trained models + report + runtime state (git-ignored)
DB_PATH = DATA_DIR / "home.db"

TZ = "Asia/Hebron"                       # hour-of-day features use local wall-clock time
WEEKEND_DAYS = (4, 5)                    # Friday, Saturday (Python: Monday = 0)

# ---- model
SLOT_MIN = 15
SLOT = f"{SLOT_MIN}min"
SLOTS_PER_DAY = 24 * 60 // SLOT_MIN      # 96
HORIZON = 4                              # predict 4 slots = 60 minutes ahead (prediction horizon, NOT switch-on time)
HORIZON_MIN = HORIZON * SLOT_MIN
MIN_DAYS = 21                            # "learning" until 3 weeks of data
WINDOW_DAYS = 112                        # sliding training window: last 16 weeks (a whole semester; chosen on
                                         # held-out weeks of the simulated year: F1 0.41 -> 0.47 vs 8 weeks)
HALF_LIFE_DAYS = 28                      # time-decay sample weights: a day 4 weeks old counts half
TEST_DAYS = 14                           # chronological test split: the last 14 days
RETENTION_DAYS = 140                     # raw readings older than this are deleted (window + lags + margin)
MODEL_PARAMS = dict(max_iter=300, learning_rate=0.05, class_weight="balanced", random_state=0)

# ---- decision policy
ACT_AT = 0.80                            # global anchor (config thresholds.ai_act_at overrides)
SUGGEST_GAP = 0.20                       # suggest from (act - 0.20)
SUGGEST_OFF_AT = 0.20                    # p <= 0.20 while ON -> suggest switching off
HYSTERESIS = 0.10                        # once armed, stay armed until p < act - 0.10
ACT_MIN, ACT_MAX = 0.60, 0.97
ENERGY_SLOPE = 0.085                     # act(w) = ai_act_at - 0.15 + 0.085 * log10(1 + watts)
ENERGY_BASE_DROP = 0.15                  #   -> 3 W fan ~0.70, 1.5 kW AC ~0.92
ADAPT_REJECT, ADAPT_ACCEPT = 0.03, 0.02  # adaptive threshold step per answer
ADAPT_MIN, ADAPT_MAX = -0.10, 0.15
ADAPT_BUCKET_H = 2                       # adaptive offsets are kept per device per 2-hour block
DRIFT_BUMP = 0.10                        # while relearning: act threshold +0.10 (suggest more, act less)
DRIFT_DROP = 0.15                        # drift if last-7-day F1 is 0.15 below the 7 days before

# ---- runtime (minutes, real time; divided by `speed` in the simulator's --fast mode)
LEAD_MIN_THERMAL = 15                    # pre-cooling / pre-heating
GUARD_GRACE_MIN = 10                     # waste guard: nobody came by target + 10 min -> switch off
OFF_AFTER_MIN = {"light": 5, "thermal": 10, "generic": 10}   # smart off after confirmed vacancy
OVERRIDE_PAUSE_MIN = 120                 # manual control -> AI leaves the device alone 2 h
SUGGESTION_TTL_MIN = 15                  # suggestions expire after 15 min (or when the situation changes)
LIGHT_WINDOW_MIN = (15, 30)              # lights: armed from target-15 to target+30 min
HOME_RECENT_ENTRY_MIN = 30               # "someone is home" = presence anywhere or a door entry in the last 30 min
SAVED_HORIZON_MIN = 60                   # saved Wh is estimated over the next hour (labelled as an estimate)

# ---- learned habits + trust ladder (docs/updates/2026-10-09_01_trust-ladder.md)
HABIT_AHEAD_MIN = 30                     # "do you use it in the next half hour" (from the history, per 15-min slot)
HABIT_ON_AT = 0.30                       # entering a dark room: the AI switches the light on if habit >= 0.30
HABIT_ON_AT_REST = 0.50                  # ... but at times you usually rest / sleep there it must be clearly your
                                         #     habit: leaving a reader in the dark costs one press, lighting a
                                         #     sleeper can cost the whole night
HABIT_OFF_AT = 0.50                      # light on while you rest: only if it is NOT clearly your habit now (<= 0.50);
                                         # 10 min without movement in a room where you usually rest is the main signal
REST_AT = 0.80                           # "usually resting here now": the PIR was quiet in >= 80% of occupied slots
STILL_OFF_MIN = 10                       # the PIR must also be quiet right now for 10 min (radar still sees you)
ENTRY_WINDOW_S = 120                     # the AI decides about the light within 2 min of you walking in
HABIT_MIN_DAYS = 7                       # a habit table needs at least a week of history
TRUST_ASK, TRUST_NOTIFY, TRUST_SILENT = 0, 1, 2
TRUST_PROMOTE_YES = 3                    # 3 "yes" in a row -> does it itself and tells you (with undo)
TRUST_SILENT_AFTER = 5                   # 5 automatic actions nobody undid -> does it silently
UNDO_WINDOW_MIN = 10                     # you reversed an automatic action within 10 min = "that was wrong"
NO_ANSWER_SNOOZE_MIN = 30                # an unanswered question is not asked again for 30 min
SELF_OFF_DAYS = 28                       # history counts as answers: in the last 4 weeks ...
SELF_OFF_MIN_NIGHTS = 5                  # ... you switched the light off yourself after lying down on >= 5 nights
                                         #     -> it does it itself and tells you (level 1) without asking first

# ---- anomaly detection (INA226)
ANOMALY_K = 4.0                          # median +/- 4 x MAD
ANOMALY_HIGH_RATIO = 1.5                 # and at least 1.5x the usual draw
ANOMALY_DEAD_RATIO = 0.2                 # "on" but below 20% of the usual draw
STANDBY_MIN_W = 0.3                      # "off" but drawing more than this
ANOMALY_PERSIST_MIN = 3                  # condition must hold 3 minutes
ANOMALY_REPEAT_H = 6                     # same alert at most every 6 hours
WEAR_RISE = 0.25                         # weekly median draw up 25% over 4 weeks -> wear warning
