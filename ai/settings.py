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
WINDOW_DAYS = 56                         # sliding training window: last 8 weeks only
HALF_LIFE_DAYS = 14                      # time-decay sample weights: a day 2 weeks old counts half
TEST_DAYS = 14                           # chronological test split: the last 14 days
RETENTION_DAYS = 70                      # raw readings older than this are deleted
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

# ---- anomaly detection (INA226)
ANOMALY_K = 4.0                          # median +/- 4 x MAD
ANOMALY_HIGH_RATIO = 1.5                 # and at least 1.5x the usual draw
ANOMALY_DEAD_RATIO = 0.2                 # "on" but below 20% of the usual draw
STANDBY_MIN_W = 0.3                      # "off" but drawing more than this
ANOMALY_PERSIST_MIN = 3                  # condition must hold 3 minutes
ANOMALY_REPEAT_H = 6                     # same alert at most every 6 hours
WEAR_RISE = 0.25                         # weekly median draw up 25% over 4 weeks -> wear warning
