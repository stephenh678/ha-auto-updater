DOMAIN = "ha_auto_updater"
PLATFORMS = ["sensor", "binary_sensor", "switch", "button", "select"]

# ---------------------------------------------------------------------------
# Config / options keys
# ---------------------------------------------------------------------------

# Schedule
CONF_ENABLED = "enabled"
CONF_FREQUENCY = "frequency"
CONF_TIME_OF_DAY = "time_of_day"
CONF_DAY_OF_WEEK = "day_of_week"

# Timing
CONF_PRE_NOTIFY_DELAY = "pre_notify_delay"   # minutes before installing
CONF_STAGGER_DELAY = "stagger_delay"          # seconds between each update
CONF_RETRY_DELAY = "retry_delay"             # seconds between install retry attempts

# Version filters
CONF_INCLUDE_MAJOR = "include_major"
CONF_SKIP_BETA = "skip_beta"

# Feature toggles (stored in options, exposed as switch entities)
CONF_BACKUP_BEFORE_UPDATE = "backup_before_update"
CONF_BACKUP_CLEANUP = "backup_cleanup"          # auto-purge old pre-update backups
CONF_BACKUP_KEEP_DAYS = "backup_keep_days"      # purge pre-update backups older than N days
CONF_DEBUG = "debug_logging"
CONF_AUTO_RESTART = "auto_restart"
CONF_WEEKLY_DIGEST = "weekly_digest"

# Notifications (switch entities)
CONF_NOTIFY_SUCCESS = "notify_success"
CONF_NOTIFY_FAILURE = "notify_failure"
CONF_NOTIFY_SERVICE = "notify_service"

# Notifications
CONF_NOTIFY_ON_NEW_UPDATES = "notify_on_new_updates"
DEFAULT_NOTIFY_ON_NEW_UPDATES = False

# Limits
CONF_MAX_UPDATES_PER_RUN = "max_updates_per_run"

# Exclusions
CONF_EXCLUDED_ENTITIES = "excluded_entities"

# ---------------------------------------------------------------------------
# Frequency options
# ---------------------------------------------------------------------------
FREQ_HOURLY = "hourly"
FREQ_DAILY = "daily"
FREQ_WEEKLY = "weekly"
FREQUENCY_OPTIONS = [FREQ_HOURLY, FREQ_DAILY, FREQ_WEEKLY]

# ---------------------------------------------------------------------------
# Day of week (Python weekday: Mon=0)
# ---------------------------------------------------------------------------
DAYS_OF_WEEK = {
    "Monday": 0,
    "Tuesday": 1,
    "Wednesday": 2,
    "Thursday": 3,
    "Friday": 4,
    "Saturday": 5,
    "Sunday": 6,
}

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_ENABLED = True
DEFAULT_FREQUENCY = FREQ_DAILY
DEFAULT_TIME_OF_DAY = "02:00"
DEFAULT_DAY_OF_WEEK = "Monday"
DEFAULT_PRE_NOTIFY_DELAY = 5        # minutes
DEFAULT_STAGGER_DELAY = 30          # seconds
DEFAULT_RETRY_DELAY = 60             # seconds
DEFAULT_INCLUDE_MAJOR = False
DEFAULT_SKIP_BETA = True
DEFAULT_BACKUP_BEFORE_UPDATE = True
DEFAULT_BACKUP_CLEANUP = False
DEFAULT_BACKUP_KEEP_DAYS = 7         # days
DEFAULT_DEBUG = False
DEFAULT_AUTO_RESTART = False
DEFAULT_WEEKLY_DIGEST = False
DEFAULT_NOTIFY_SUCCESS = True
DEFAULT_NOTIFY_FAILURE = True
DEFAULT_NOTIFY_SERVICE = ""
DEFAULT_MAX_UPDATES_PER_RUN = 0     # 0 = unlimited
DEFAULT_EXCLUDED_ENTITIES: list = []

# ---------------------------------------------------------------------------
# Service names
# ---------------------------------------------------------------------------
SERVICE_RUN_UPDATES = "run_updates"
SERVICE_SNOOZE_UPDATE = "snooze_update"
SERVICE_CLEAR_SNOOZE = "clear_snooze"
DEFAULT_SNOOZE_DAYS = 7

# ---------------------------------------------------------------------------
# Repeated-failure escalation
# ---------------------------------------------------------------------------
# If the same component fails on this many consecutive runs, escalate the
# failure notification so it can be dealt with manually.
FAILURE_ESCALATION_THRESHOLD = 3

# ---------------------------------------------------------------------------
# hass.data key
# ---------------------------------------------------------------------------
DATA_COORDINATOR = "coordinator"

# ---------------------------------------------------------------------------
# History / state persistence files
# ---------------------------------------------------------------------------
HISTORY_FILE = "ha_auto_updater_history.json"
MAX_HISTORY_ENTRIES = 50

# Digest state persistence file
DIGEST_STATE_FILE = "ha_auto_updater_digest.json"
# Tracks pre-update backups we created, for auto-cleanup
BACKUP_STATE_FILE = "ha_auto_updater_backups.json"
# Tracks per-entity snooze expiry timestamps
SNOOZE_STATE_FILE = "ha_auto_updater_snooze.json"
