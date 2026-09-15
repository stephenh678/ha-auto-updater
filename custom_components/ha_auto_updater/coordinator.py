"""Auto Updater coordinator — scheduling, update execution, history, and debug."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from datetime import datetime, timedelta

from awesomeversion import AwesomeVersion, AwesomeVersionStrategy
from homeassistant.components.persistent_notification import async_create
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_call_later,
    async_track_point_in_time,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import (
    BACKUP_STATE_FILE,
    BACKUP_TIMEOUT_SECONDS,
    CONF_ABORT_ON_BACKUP_FAILURE,
    CONF_AUTO_QUARANTINE,
    CONF_AUTO_RESTART,
    CONF_BACKUP_BEFORE_UPDATE,
    CONF_BACKUP_CLEANUP,
    CONF_BACKUP_KEEP_DAYS,
    CONF_DAY_OF_WEEK,
    CONF_DEBUG,
    CONF_ENABLED,
    CONF_EXCLUDED_ENTITIES,
    CONF_FREQUENCY,
    CONF_INCLUDE_MAJOR,
    CONF_MAX_UPDATES_PER_RUN,
    CONF_MIN_DISK_SPACE_GB,
    CONF_NOTIFY_FAILURE,
    CONF_NOTIFY_ON_NEW_UPDATES,
    CONF_NOTIFY_SERVICE,
    CONF_NOTIFY_SUCCESS,
    CONF_PRE_NOTIFY_DELAY,
    CONF_RETRY_DELAY,
    CONF_SKIP_BETA,
    CONF_STAGGER_DELAY,
    CONF_TIME_OF_DAY,
    CONF_UPDATE_ADDONS,
    CONF_UPDATE_FIRMWARE,
    CONF_UPDATE_HACS,
    CONF_UPDATE_SYSTEM,
    CONF_WEEKLY_DIGEST,
    DAYS_OF_WEEK,
    DEFAULT_ABORT_ON_BACKUP_FAILURE,
    DEFAULT_AUTO_QUARANTINE,
    DEFAULT_AUTO_RESTART,
    DEFAULT_BACKUP_BEFORE_UPDATE,
    DEFAULT_BACKUP_CLEANUP,
    DEFAULT_BACKUP_KEEP_DAYS,
    DEFAULT_DAY_OF_WEEK,
    DEFAULT_DEBUG,
    DEFAULT_ENABLED,
    DEFAULT_EXCLUDED_ENTITIES,
    DEFAULT_FREQUENCY,
    DEFAULT_INCLUDE_MAJOR,
    DEFAULT_MAX_UPDATES_PER_RUN,
    DEFAULT_MIN_DISK_SPACE_GB,
    DEFAULT_NOTIFY_FAILURE,
    DEFAULT_NOTIFY_ON_NEW_UPDATES,
    DEFAULT_NOTIFY_SERVICE,
    DEFAULT_NOTIFY_SUCCESS,
    DEFAULT_PRE_NOTIFY_DELAY,
    DEFAULT_RETRY_DELAY,
    DEFAULT_SKIP_BETA,
    DEFAULT_SNOOZE_DAYS,
    DEFAULT_STAGGER_DELAY,
    DEFAULT_TIME_OF_DAY,
    DEFAULT_UPDATE_ADDONS,
    DEFAULT_UPDATE_FIRMWARE,
    DEFAULT_UPDATE_HACS,
    DEFAULT_UPDATE_SYSTEM,
    DEFAULT_WEEKLY_DIGEST,
    DIGEST_STATE_FILE,
    EVENT_BACKUP_COMPLETE,
    EVENT_BACKUP_START,
    EVENT_ITEM_COMPLETE,
    EVENT_RUN_FINISHED,
    EVENT_UPDATE_START,
    FAILURE_ESCALATION_THRESHOLD,
    FIRMWARE_DEVICE_CLASS,
    FIRMWARE_PLATFORMS,
    FREQ_HOURLY,
    FREQ_WEEKLY,
    HISTORY_FILE,
    MAX_HISTORY_ENTRIES,
    RESUME_RUN_DELAY_MINUTES,
    RUN_STATE_FILE,
    SNOOZE_STATE_FILE,
    SYSTEM_UPDATE_POLL_SECONDS,
    SYSTEM_UPDATE_WATCH_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

EVENT_RUN_COMPLETE = "ha_auto_updater_run"

# Pre-release version pattern: b1, b12, beta, rc1, rc, dev, alpha
_PRERELEASE_RE = re.compile(r"(b\d+|\.beta|rc\d*|\.dev|alpha)", re.IGNORECASE)

# HA system update entities — always install regardless of include_major setting.
# OS, Supervisor, and Core use sequential or calendar versioning where a bump
# in the major number is a routine release, not a breaking API change.
_HA_SYSTEM_UPDATE_ENTITIES = {
    "update.home_assistant_supervisor",
    "update.home_assistant_operating_system",
    "update.home_assistant_core",
    "update.home_assistant_core_update",
}


class AutoUpdaterCoordinator:
    """Manages scheduling, update execution, history, and debug logging."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._unsub_timer = None
        self._unsub_scan = None
        self._unsub_resume = None
        self._is_running = False
        self._scan_in_progress = False
        self._run_task: asyncio.Task | None = None
        self._listeners: list = []
        # System updates (Core/OS/Supervisor) that were triggered but whose
        # outcome could not be observed yet — verified on later scans.
        self._pending_verification: list[dict] = []
        # Updates queued behind a system-update restart, waiting for the
        # follow-up pass. Persisted so the pass survives the restart itself.
        self._deferred: list[dict] = []

        # State exposed to sensor entities
        self.pending_count: int = 0
        self.pending_updates: list[dict] = []
        self.failed_updates: list[dict] = []
        self.last_run: datetime | None = None
        self.last_run_count: int = 0
        self.last_run_failed: int = 0
        self.last_run_status: str = "Never run"
        self.last_run_duration: int = 0   # seconds
        self.next_run: datetime | None = None
        self.history: list[dict] = []
        self._previous_pending_count: int = -1   # -1 = first scan, never triggers notification
        self._last_digest_sent: datetime | None = None
        self._snoozed: dict[str, str] = {}       # entity_id -> ISO expiry timestamp
        self._tracked_backups: list[dict] = []   # pre-update backups we created

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def options(self) -> dict:
        return {**self.entry.data, **self.entry.options}

    @property
    def enabled(self) -> bool:
        return self.options.get(CONF_ENABLED, DEFAULT_ENABLED)

    @property
    def debug(self) -> bool:
        return self.options.get(CONF_DEBUG, DEFAULT_DEBUG)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_setup(self) -> None:
        if self._unsub_scan is not None:
            self._unsub_scan()
            self._unsub_scan = None
        self._apply_log_level()
        await self._load_history()
        await self._load_digest_state()
        await self._load_snooze()
        await self._load_backup_state()
        await self._async_reconcile_interrupted_run()
        await self._async_reschedule()
        # Scan for pending updates immediately, then every 30 minutes so the
        # sensor stays current between scheduled install runs.
        await self._async_scan_pending(None)
        self._unsub_scan = async_track_time_interval(
            self.hass, self._async_scan_pending, timedelta(minutes=30)
        )

    async def async_unload(self) -> None:
        self._cancel_timer()
        if self._unsub_scan is not None:
            self._unsub_scan()
            self._unsub_scan = None
        if self._unsub_resume is not None:
            self._unsub_resume()
            self._unsub_resume = None
        # Stop a run that is sleeping in a pre-notify / stagger / retry wait so
        # an integration reload or HA shutdown doesn't leave it running detached.
        task = self._run_task
        if task is not None and not task.done():
            _LOGGER.info("Auto Updater: unloading — cancelling update run in progress.")
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._run_task = None

    # ------------------------------------------------------------------
    # Debug logging
    # ------------------------------------------------------------------

    def _apply_log_level(self) -> None:
        level = logging.DEBUG if self.debug else logging.INFO
        logging.getLogger("custom_components.ha_auto_updater").setLevel(level)
        _LOGGER.debug("Auto Updater: debug logging %s.", "enabled" if self.debug else "disabled")

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def _cancel_timer(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    async def _async_reschedule(self) -> None:
        self._cancel_timer()
        if not self.enabled:
            self.next_run = None
            _LOGGER.debug("Auto Updater: disabled, not scheduling.")
            self._notify_listeners()
            return
        self.next_run = self._next_run_time()
        _LOGGER.info("Auto Updater: next run scheduled for %s", self.next_run)
        self._unsub_timer = async_track_point_in_time(
            self.hass, self._async_fire, self.next_run
        )
        self._notify_listeners()

    def _next_run_time(self) -> datetime:
        frequency = self.options.get(CONF_FREQUENCY, DEFAULT_FREQUENCY)
        time_str = self.options.get(CONF_TIME_OF_DAY, DEFAULT_TIME_OF_DAY)
        try:
            parts = str(time_str).split(":")
            hour, minute = int(parts[0]), int(parts[1])
        except (ValueError, AttributeError, IndexError):
            hour, minute = 2, 0

        now = dt_util.now()

        if frequency == FREQ_HOURLY:
            return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

        if frequency == FREQ_WEEKLY:
            day_name = self.options.get(CONF_DAY_OF_WEEK, DEFAULT_DAY_OF_WEEK)
            target_weekday = DAYS_OF_WEEK.get(day_name, 0)
            days_ahead = (target_weekday - now.weekday()) % 7
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            candidate += timedelta(days=days_ahead)
            if candidate <= now:
                candidate += timedelta(weeks=1)
            return candidate

        # Daily (default for any non-weekly/hourly value, including legacy configs)
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    async def _async_fire(self, _now: datetime) -> None:
        await self.async_run_updates()
        await self._async_reschedule()

    # ------------------------------------------------------------------
    # Shared update filter & safety guards
    # ------------------------------------------------------------------

    def _check_disk_space(self, min_gb: float) -> tuple[bool, float]:
        """Check available disk space in config dir. Return (is_ok, free_gb)."""
        try:
            total, used, free = shutil.disk_usage(self.hass.config.config_dir)
            free_gb = round(free / (1024 ** 3), 2)
            return free_gb >= min_gb, free_gb
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Auto Updater: disk space check error — %s", exc)
            return True, 999.0

    def _filter_available_updates(self, log_skips: bool = False) -> list:
        """Return all update entities that pass the current filter settings.

        Args:
            log_skips: When True, logs INFO messages for each skipped entity
                       (used during install runs). Keep False for background scans
                       to avoid log noise.
        """
        excluded: list[str] = self.options.get(CONF_EXCLUDED_ENTITIES, DEFAULT_EXCLUDED_ENTITIES)
        include_major: bool = self.options.get(CONF_INCLUDE_MAJOR, DEFAULT_INCLUDE_MAJOR)
        skip_beta: bool = self.options.get(CONF_SKIP_BETA, DEFAULT_SKIP_BETA)
        update_addons: bool = self.options.get(CONF_UPDATE_ADDONS, DEFAULT_UPDATE_ADDONS)
        update_hacs: bool = self.options.get(CONF_UPDATE_HACS, DEFAULT_UPDATE_HACS)
        update_firmware: bool = self.options.get(CONF_UPDATE_FIRMWARE, DEFAULT_UPDATE_FIRMWARE)
        update_system: bool = self.options.get(CONF_UPDATE_SYSTEM, DEFAULT_UPDATE_SYSTEM)

        available = []
        for entity in self.hass.states.async_all("update"):
            if entity.state != "on":
                continue
            if entity.entity_id in excluded:
                if log_skips:
                    _LOGGER.debug("Auto Updater: skipping excluded %s", entity.entity_id)
                continue
            if self._is_snoozed(entity.entity_id):
                if log_skips:
                    _LOGGER.info(
                        "Auto Updater: skipping snoozed %s (until %s)",
                        entity.entity_id, self._snoozed.get(entity.entity_id),
                    )
                continue
            if entity.attributes.get("in_progress", False):
                if log_skips:
                    _LOGGER.info(
                        "Auto Updater: skipping %s — install already in progress "
                        "(likely started elsewhere, e.g. HA's Update All).",
                        entity.entity_id,
                    )
                continue

            source = self._get_update_source(entity.entity_id)
            if source == "Add-on" and not update_addons:
                if log_skips:
                    _LOGGER.info("Auto Updater: skipping Add-on %s (Add-on updates disabled)", entity.entity_id)
                continue
            if source == "HACS" and not update_hacs:
                if log_skips:
                    _LOGGER.info("Auto Updater: skipping HACS %s (HACS updates disabled)", entity.entity_id)
                continue
            if source == "Firmware" and not update_firmware:
                if log_skips:
                    _LOGGER.info("Auto Updater: skipping Firmware %s (Firmware updates disabled)", entity.entity_id)
                continue
            if source == "HA System" and not update_system:
                if log_skips:
                    _LOGGER.info("Auto Updater: skipping System %s (System updates disabled)", entity.entity_id)
                continue

            attrs = entity.attributes
            latest = attrs.get("latest_version", "")
            if (
                not include_major
                and entity.entity_id not in _HA_SYSTEM_UPDATE_ENTITIES
                and self._is_major_bump(attrs)
            ):
                if log_skips:
                    _LOGGER.info(
                        "Auto Updater: skipping major update for %s (%s → %s)",
                        attrs.get("title") or entity.entity_id,
                        attrs.get("installed_version", "?"),
                        latest,
                    )
                continue
            if skip_beta and self._is_prerelease(latest):
                if log_skips:
                    _LOGGER.info(
                        "Auto Updater: skipping pre-release %s (%s)",
                        attrs.get("title") or entity.entity_id,
                        latest,
                    )
                continue
            available.append(entity)
        return available

    def _build_pending_list(self, available: list) -> list[dict]:
        """Build the pending_updates list-of-dicts shared by scan and run."""
        return [
            {
                "title": e.attributes.get("title") or e.entity_id,
                "installed_version": e.attributes.get("installed_version") or "?",
                "latest_version": e.attributes.get("latest_version") or "?",
                "entity_id": e.entity_id,
                "source": self._get_update_source(e.entity_id),
                "release_url": e.attributes.get("release_url"),
                "release_summary": e.attributes.get("release_summary"),
            }
            for e in available
        ]

    # ------------------------------------------------------------------
    # Background pending-update scan (read-only, no installs)
    # ------------------------------------------------------------------

    async def _async_scan_pending(self, _now) -> None:
        """Refresh pending_count and pending_updates without installing anything.

        Runs on startup and every 30 minutes so the sensor reflects the real-time
        state of update entities between scheduled install runs.
        """
        # Verification below can quarantine (snooze) an entity, and snoozing
        # triggers a scan of its own. The outer scan is about to recompute
        # everything anyway, so a nested scan is pure duplicate work.
        if self._scan_in_progress:
            return
        self._scan_in_progress = True
        try:
            await self._async_scan_pending_inner()
        finally:
            self._scan_in_progress = False

    async def _async_scan_pending_inner(self) -> None:
        # Resolve any Core/OS/Supervisor installs whose result is still unknown
        try:
            await self._async_verify_pending()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Auto Updater: pending-update verification error — %s", exc)

        available = self._filter_available_updates(log_skips=False)

        self.pending_count = len(available)
        self.pending_updates = self._build_pending_list(available)

        # Prune items from failed_updates if they have been updated outside Auto Updater
        if self.failed_updates:
            still_failed = []
            for f in self.failed_updates:
                eid = f.get("entity_id")
                if eid:
                    st = self.hass.states.get(eid)
                    if st is not None and st.state != "on":
                        continue
                still_failed.append(f)
            self.failed_updates = still_failed

        _LOGGER.debug("Auto Updater: scan found %d pending update(s).", self.pending_count)
        self._notify_listeners()

        # Notify if new updates appeared since last scan
        if (
            self._previous_pending_count >= 0
            and self.pending_count > self._previous_pending_count
            and self.options.get(CONF_NOTIFY_ON_NEW_UPDATES, DEFAULT_NOTIFY_ON_NEW_UPDATES)
        ):
            new_count = self.pending_count - self._previous_pending_count
            _LOGGER.info("Auto Updater: %d new update(s) detected.", new_count)
            self._send_push_notification(
                "New Updates Available",
                "{} new update(s) available — {} total pending.".format(
                    new_count, self.pending_count
                ),
            )
        self._previous_pending_count = self.pending_count

        # Weekly digest check — fires when 7+ days have elapsed since last digest
        if self.options.get(CONF_WEEKLY_DIGEST, DEFAULT_WEEKLY_DIGEST):
            now = dt_util.now()
            if self._last_digest_sent is None:
                # First time with digest enabled — start the 7-day clock from now
                # without firing immediately (there's no history worth summarising yet)
                self._last_digest_sent = now
                await self._save_digest_state()
            elif (now - self._last_digest_sent).total_seconds() >= 7 * 86400:
                await self._async_weekly_digest(now)

    # ------------------------------------------------------------------
    # Main update flow
    # ------------------------------------------------------------------

    async def async_scan_pending(self) -> None:
        """Public wrapper — refresh pending updates count without installing."""
        await self._async_scan_pending(None)

    async def async_run_updates(self, resume: bool = False) -> None:
        """Run a full update pass.

        resume=True is used for the follow-up pass after a system update
        (Core/OS/Supervisor) restart: the pre-update backup and notice were
        already done minutes earlier, so they are skipped.
        """
        if self._is_running:
            _LOGGER.warning("Auto Updater: run already in progress — skipping.")
            return
        self._is_running = True
        self._run_task = asyncio.current_task()
        self.last_run_status = "Running"
        self._notify_listeners()
        try:
            await self._async_run_updates_inner(resume=resume)
        except asyncio.CancelledError:
            _LOGGER.warning("Auto Updater: update run cancelled.")
            # Persist whatever the run had achieved so far so failure counts and
            # the history sensor don't silently lose the partial run.
            await self._async_finalize_run_marker(note="Run cancelled (integration unloaded)")
            self.last_run_status = "Aborted (Cancelled)"
            raise
        finally:
            self._is_running = False
            self._run_task = None
            self._notify_listeners()

    async def async_install_single(self, entity_id: str) -> bool:
        """Manually install an update for a single entity with safety guards & backup."""
        if self._is_running:
            _LOGGER.warning("Auto Updater: run already in progress — skipping single install of %s.", entity_id)
            return False

        self._is_running = True
        self._run_task = asyncio.current_task()
        self._notify_listeners()
        try:
            # --- Safe Mode Guard ---
            if getattr(self.hass.config, "safe_mode", False):
                _LOGGER.warning("Auto Updater: Home Assistant is running in Safe Mode — aborting manual update for %s.", entity_id)
                return False

            # --- Disk Space Guard ---
            min_disk_gb = float(self.options.get(CONF_MIN_DISK_SPACE_GB, DEFAULT_MIN_DISK_SPACE_GB))
            space_ok, free_gb = self._check_disk_space(min_disk_gb)
            if not space_ok:
                _LOGGER.error(
                    "Auto Updater: insufficient disk space (%.2f GB available, %.2f GB required) — aborting manual update for %s.",
                    free_gb, min_disk_gb, entity_id,
                )
                self._send_status_notification(
                    "Storage Warning — Update Aborted",
                    "Available disk space ({:.2f} GB) is below the configured minimum ({:.2f} GB). Update for {} aborted.".format(
                        free_gb, min_disk_gb, entity_id
                    ),
                    notification_id="ha_auto_updater_low_storage",
                )
                return False

            # --- Entity State Check ---
            current_state = self.hass.states.get(entity_id)
            if current_state is None or current_state.state != "on":
                _LOGGER.info("Auto Updater: %s is not available for update or already up to date.", entity_id)
                return False

            attrs = current_state.attributes
            title = attrs.get("title") or entity_id
            installed = attrs.get("installed_version", "?")
            latest = attrs.get("latest_version", "?")
            release_url = attrs.get("release_url")
            restart_required = attrs.get("restart_required", True)
            is_system_update = entity_id in _HA_SYSTEM_UPDATE_ENTITIES

            # --- Backup ---
            backup_enabled: bool = self.options.get(CONF_BACKUP_BEFORE_UPDATE, DEFAULT_BACKUP_BEFORE_UPDATE)
            abort_on_backup_failure: bool = self.options.get(
                CONF_ABORT_ON_BACKUP_FAILURE, DEFAULT_ABORT_ON_BACKUP_FAILURE
            )
            if backup_enabled:
                _LOGGER.info("Auto Updater: creating backup before manual update of %s…", entity_id)
                self.hass.bus.async_fire(EVENT_BACKUP_START, {"entity_id": entity_id})
                self._send_status_notification(
                    "Creating backup…",
                    "A backup is being created before installing update for {}.".format(title),
                    notification_id="ha_auto_updater_backup_progress",
                )
                backup_ok = await self._create_backup()
                self._dismiss_notification("ha_auto_updater_backup_progress")
                self.hass.bus.async_fire(EVENT_BACKUP_COMPLETE, {"success": backup_ok, "entity_id": entity_id})
                if backup_ok:
                    _LOGGER.info("Auto Updater: backup completed successfully.")
                    self._send_status_notification(
                        "Backup complete",
                        "Backup created successfully. Installing update for {} now.".format(title),
                        notification_id="ha_auto_updater_backup_done",
                    )
                else:
                    if abort_on_backup_failure:
                        _LOGGER.error(
                            "Auto Updater: pre-update backup failed and strict backup mode is enabled — aborting update for %s.",
                            entity_id,
                        )
                        self._send_status_notification(
                            "Backup Failed — Update Aborted",
                            "Pre-update backup failed. Update of {} aborted due to strict backup requirement.".format(title),
                            notification_id="ha_auto_updater_backup_failed",
                        )
                        return False
                    _LOGGER.warning("Auto Updater: backup failed or unavailable, proceeding anyway.")

            self._dismiss_notification("ha_auto_updater_backup_done")
            retry_delay = int(self.options.get(CONF_RETRY_DELAY, DEFAULT_RETRY_DELAY))

            # --- Install with retry ---
            outcome = await self._install_with_retry(
                entity_id, title, latest, is_system_update, retry_delay
            )
            success = outcome != "failed"
            pending = outcome == "pending"
            self.hass.bus.async_fire(
                EVENT_ITEM_COMPLETE,
                {
                    "entity_id": entity_id, "title": title, "success": success,
                    "pending_verification": pending, "from": installed, "to": latest,
                },
            )
            if pending:
                _LOGGER.info(
                    "Auto Updater: %s install triggered (%s → %s, manual) — outcome "
                    "will be verified on the next scan.", title, installed, latest,
                )
                self._send_status_notification(
                    "Update triggered",
                    "{} {} → {} has been handed to the Supervisor. Home Assistant may restart; "
                    "the result is confirmed on the next scan.".format(title, installed, latest),
                    notification_id="ha_auto_updater_pending_verification",
                )
            elif success:
                _LOGGER.info("Auto Updater: ✓ %s  %s → %s (manual update)", title, installed, latest)

            run_time = dt_util.now()
            notify_success: bool = self.options.get(CONF_NOTIFY_SUCCESS, DEFAULT_NOTIFY_SUCCESS)
            item = {
                "entity_id": entity_id, "title": title,
                "from": installed, "to": latest, "release_url": release_url,
            }

            if success and pending:
                triggered = {**item, "triggered_at": run_time.isoformat()}
                self._pending_verification.append(triggered)
                await self._save_run_state(None)
                await self._append_and_save_history(
                    self._build_history_entry(
                        run_time, [], [], 0, triggered=[triggered],
                        note=f"Manual update of {title} triggered",
                    )
                )
            elif success:
                await self._append_and_save_history(
                    self._build_history_entry(
                        run_time, [item], [], 0, note=f"Manual update of {title}"
                    )
                )
                if notify_success:
                    self._send_success_notification([item])
                    self._send_push_notification("Updates Installed", f"{title} updated successfully to {latest}.")
            else:
                await self._append_and_save_history(
                    self._build_history_entry(
                        run_time, [], [item], 0, note=f"Manual update failed for {title}"
                    )
                )
                await self._handle_failures([item])

            await self._async_scan_pending(None)

            # Auto-restart if needed
            auto_restart: bool = self.options.get(CONF_AUTO_RESTART, DEFAULT_AUTO_RESTART)
            if success and not is_system_update and auto_restart and restart_required:
                _LOGGER.info("Auto Updater: restarting HA — %s update requires restart.", title)
                self._send_status_notification(
                    "Restarting…",
                    f"HA is restarting to apply update for {title}.",
                )
                await asyncio.sleep(3)
                await self.hass.services.async_call("homeassistant", "restart")

            return success
        finally:
            self._is_running = False
            self._run_task = None
            self._notify_listeners()

    async def _async_run_updates_inner(self, resume: bool = False) -> None:
        _LOGGER.info(
            "Auto Updater: checking for available updates%s…",
            " (follow-up pass)" if resume else "",
        )

        # --- Safe Mode Guard ---
        if getattr(self.hass.config, "safe_mode", False):
            _LOGGER.warning("Auto Updater: Home Assistant is running in Safe Mode — aborting update run.")
            self.last_run_status = "Aborted (Safe Mode)"
            self._notify_listeners()
            return

        # --- Disk Space Guard ---
        min_disk_gb = float(self.options.get(CONF_MIN_DISK_SPACE_GB, DEFAULT_MIN_DISK_SPACE_GB))
        space_ok, free_gb = self._check_disk_space(min_disk_gb)
        if not space_ok:
            _LOGGER.error(
                "Auto Updater: insufficient disk space (%.2f GB available, %.2f GB required) — aborting update run.",
                free_gb, min_disk_gb,
            )
            self._send_status_notification(
                "Storage Warning — Run Aborted",
                "Available disk space ({:.2f} GB) is below the configured minimum ({:.2f} GB). Update run aborted.".format(
                    free_gb, min_disk_gb
                ),
                notification_id="ha_auto_updater_low_storage",
            )
            self.last_run_status = "Aborted (Low Storage)"
            self._notify_listeners()
            return

        backup_enabled: bool = self.options.get(CONF_BACKUP_BEFORE_UPDATE, DEFAULT_BACKUP_BEFORE_UPDATE)
        abort_on_backup_failure: bool = self.options.get(
            CONF_ABORT_ON_BACKUP_FAILURE, DEFAULT_ABORT_ON_BACKUP_FAILURE
        )
        pre_notify_delay: int = int(self.options.get(CONF_PRE_NOTIFY_DELAY, DEFAULT_PRE_NOTIFY_DELAY))
        stagger_delay: int = int(self.options.get(CONF_STAGGER_DELAY, DEFAULT_STAGGER_DELAY))
        retry_delay: int = int(self.options.get(CONF_RETRY_DELAY, DEFAULT_RETRY_DELAY))
        max_updates: int = int(self.options.get(CONF_MAX_UPDATES_PER_RUN, DEFAULT_MAX_UPDATES_PER_RUN))
        run_start = dt_util.now()

        # --- 1. Find and filter available updates ---
        _LOGGER.debug(
            "Auto Updater: %d total update entities found.",
            len(self.hass.states.async_all("update")),
        )
        available = self._filter_available_updates(log_skips=True)

        # Sort available updates so non-system updates execute first and system updates
        # (Core, OS, Supervisor) execute last, preventing background restarts from interrupting other updates.
        available.sort(key=lambda e: 1 if e.entity_id in _HA_SYSTEM_UPDATE_ENTITIES else 0)

        # Apply max-updates-per-run cap (0 = unlimited)
        if max_updates > 0 and len(available) > max_updates:
            _LOGGER.info(
                "Auto Updater: capping run to %d of %d available update(s).",
                max_updates, len(available),
            )
            available = available[:max_updates]

        self.pending_count = len(available)
        self.pending_updates = self._build_pending_list(available)
        self._notify_listeners()

        # --- 2. Nothing to do ---
        if not available:
            _LOGGER.info("Auto Updater: no updates available.")
            self.last_run = run_start
            self.last_run_count = 0
            self.last_run_failed = 0
            self.last_run_status = "No updates"
            self.failed_updates = []
            await self._append_and_save_history({
                "timestamp": run_start.isoformat(),
                "updated": [],
                "failed": [],
                "total_updated": 0,
                "total_failed": 0,
                "duration_seconds": 0,
                "note": "No updates available",
            })
            self.hass.bus.async_fire(EVENT_RUN_COMPLETE, {"total_updated": 0, "total_failed": 0})
            self.hass.bus.async_fire(EVENT_RUN_FINISHED, {"total_updated": 0, "total_failed": 0, "duration_seconds": 0})
            self._notify_listeners()
            return

        titles = [e.attributes.get("title", e.entity_id) for e in available]
        _LOGGER.info("Auto Updater: %d update(s) available — %s", len(available), titles)

        self.hass.bus.async_fire(
            EVENT_UPDATE_START,
            {"available_count": len(available), "titles": titles},
        )

        # --- 3. Backup ---
        if backup_enabled and resume:
            _LOGGER.info("Auto Updater: follow-up pass — reusing the backup taken before the interrupted run.")
        elif backup_enabled:
            _LOGGER.info("Auto Updater: creating backup before updates…")
            self.hass.bus.async_fire(EVENT_BACKUP_START, {})
            self._send_status_notification(
                "Creating backup…",
                "A backup is being created before installing updates.",
                notification_id="ha_auto_updater_backup_progress",
            )
            backup_ok = await self._create_backup()
            self._dismiss_notification("ha_auto_updater_backup_progress")
            self.hass.bus.async_fire(EVENT_BACKUP_COMPLETE, {"success": backup_ok})
            if backup_ok:
                _LOGGER.info("Auto Updater: backup completed successfully.")
                self._send_status_notification(
                    "Backup complete",
                    "Backup created successfully. Installing updates now.",
                    notification_id="ha_auto_updater_backup_done",
                )
            else:
                if abort_on_backup_failure:
                    _LOGGER.error(
                        "Auto Updater: pre-update backup failed and strict backup mode is enabled — aborting update run."
                    )
                    self._send_status_notification(
                        "Backup Failed — Run Aborted",
                        "Pre-update backup failed. Update run aborted due to strict backup requirement.",
                        notification_id="ha_auto_updater_backup_failed",
                    )
                    self.last_run = dt_util.now()
                    self.last_run_status = "Aborted (Backup Failed)"
                    self._notify_listeners()
                    return
                _LOGGER.warning("Auto Updater: backup failed or unavailable, proceeding anyway.")

        # --- 4. Pre-update notification + delay ---
        if pre_notify_delay > 0 and not resume:
            _LOGGER.info(
                "Auto Updater: sending pre-update notice, waiting %d min before installing…",
                pre_notify_delay,
            )
            self._send_pre_update_notification(titles, pre_notify_delay)
            # Sleep in short slices so flipping the Enabled switch aborts promptly
            # instead of only being noticed once the full delay has elapsed.
            remaining = pre_notify_delay * 60
            while remaining > 0 and self.enabled:
                step = min(15, remaining)
                await asyncio.sleep(step)
                remaining -= step

            # Abort if disabled during the wait
            if not self.enabled:
                _LOGGER.info("Auto Updater: disabled during pre-update wait — aborting.")
                self.last_run = dt_util.now()
                self.last_run_status = "Aborted"
                self._notify_listeners()
                return

        # --- 5. Install updates ---
        updated_items: list[dict] = []
        failed_items: list[dict] = []
        triggered_items: list[dict] = []   # system updates awaiting verification
        deferred_items: list[dict] = []    # queued behind a system update restart
        restart_required_map: dict[str, bool] = {}  # entity_id -> requires restart
        system_updates_triggered = False

        # Dismiss pre-update and backup notifications — installs are starting now
        self._dismiss_notification("ha_auto_updater_pre_update")
        self._dismiss_notification("ha_auto_updater_backup_done")

        # Persist a run marker so a restart mid-run (Core/OS update, crash,
        # reload) can be reconstructed into history on the next startup.
        run_marker: dict = {
            "started": run_start.isoformat(),
            "resume": resume,
            "items": self._build_pending_list(available),
            "updated": [],
            "failed": [],
            "triggered": [],
            "deferred": [],
            "current": None,
        }
        self._deferred = []  # this run supersedes any queued follow-up
        await self._save_run_state(run_marker)

        for i, entity in enumerate(available):
            entity_id = entity.entity_id
            attrs = entity.attributes
            title = attrs.get("title") or entity_id
            installed = attrs.get("installed_version", "?")
            latest = attrs.get("latest_version", "?")
            release_url = attrs.get("release_url")
            item = {
                "entity_id": entity_id, "title": title,
                "from": installed, "to": latest, "release_url": release_url,
            }
            # Capture restart_required before install while state is still "on"
            restart_required_map[entity_id] = attrs.get("restart_required", True)

            # Stagger delay before each update (except the first)
            if i > 0 and stagger_delay > 0:
                _LOGGER.debug("Auto Updater: stagger — waiting %ds before next update…", stagger_delay)
                await asyncio.sleep(stagger_delay)

            # Re-check state — entity may have been updated since the initial scan
            current_state = self.hass.states.get(entity_id)
            if current_state is None or current_state.state != "on":
                _LOGGER.info("Auto Updater: skipping %s — already up to date.", title)
                continue

            is_system_update = entity_id in _HA_SYSTEM_UPDATE_ENTITIES

            run_marker["current"] = item
            await self._save_run_state(run_marker)

            outcome = await self._install_with_retry(
                entity_id, title, latest, is_system_update, retry_delay
            )

            run_marker["current"] = None
            if outcome == "success":
                updated_items.append(item)
                run_marker["updated"].append(item)
                self.hass.bus.async_fire(
                    EVENT_ITEM_COMPLETE,
                    {"entity_id": entity_id, "title": title, "success": True, "from": installed, "to": latest},
                )
                _LOGGER.info("Auto Updater: ✓ %s  %s → %s", title, installed, latest)
            elif outcome == "pending":
                triggered = {**item, "triggered_at": dt_util.now().isoformat()}
                triggered_items.append(triggered)
                run_marker["triggered"].append(triggered)
                self.hass.bus.async_fire(
                    EVENT_ITEM_COMPLETE,
                    {
                        "entity_id": entity_id, "title": title, "success": True,
                        "pending_verification": True, "from": installed, "to": latest,
                    },
                )
                _LOGGER.info(
                    "Auto Updater: %s install triggered (%s → %s) — outcome will be "
                    "verified on the next scan.", title, installed, latest,
                )
            else:
                failed_items.append(item)
                run_marker["failed"].append(item)
                self.hass.bus.async_fire(
                    EVENT_ITEM_COMPLETE,
                    {"entity_id": entity_id, "title": title, "success": False, "from": installed, "to": latest},
                )

            if is_system_update and outcome != "failed":
                system_updates_triggered = True
                _LOGGER.info(
                    "Auto Updater: %s is a system-level update — HA or the Supervisor "
                    "may restart. Remaining updates are deferred to a follow-up run.",
                    title,
                )
                deferred_items = self._build_pending_list(available[i + 1:])
                run_marker["deferred"] = deferred_items
                await self._save_run_state(run_marker)
                break

            await self._save_run_state(run_marker)

        # --- 6. Persist results ---
        run_end = dt_util.now()
        duration = int((run_end - run_start).total_seconds())
        failed_names = [f["title"] for f in failed_items]
        self.last_run_duration = duration
        self.last_run = run_end
        self.last_run_count = len(updated_items)
        self.last_run_failed = len(failed_items)
        self.last_run_status = self._derive_status(len(updated_items), len(failed_items))
        if deferred_items and self.last_run_status in ("Success", "No updates"):
            self.last_run_status = "Success (deferred)"
        self.pending_count = len(failed_items) + len(deferred_items)
        keep_ids = {f["entity_id"] for f in failed_items} | {d["entity_id"] for d in deferred_items}
        self.pending_updates = [u for u in self.pending_updates if u["entity_id"] in keep_ids]
        self.failed_updates = [
            {"title": f["title"], "entity_id": f["entity_id"]} for f in failed_items
        ]
        if triggered_items:
            self._pending_verification.extend(triggered_items)
        # Persisted with the run state so the follow-up pass still happens if
        # the system update restarts HA before the in-process timer fires.
        self._deferred = list(deferred_items)

        await self._append_and_save_history(
            self._build_history_entry(
                run_end, updated_items, failed_items, duration,
                triggered=triggered_items, deferred=deferred_items,
                note="Resumed run" if resume else None,
            )
        )
        await self._save_run_state(None)

        self.hass.bus.async_fire(
            EVENT_RUN_COMPLETE,
            {
                "updated": [u["title"] for u in updated_items],
                "failed": failed_names,
                "pending_verification": [t["title"] for t in triggered_items],
                "deferred": [d["title"] for d in deferred_items],
                "total_updated": len(updated_items),
                "total_failed": len(failed_items),
                "duration_seconds": duration,
            },
        )
        self.hass.bus.async_fire(
            EVENT_RUN_FINISHED,
            {
                "total_updated": len(updated_items),
                "total_failed": len(failed_items),
                "total_pending_verification": len(triggered_items),
                "total_deferred": len(deferred_items),
                "duration_seconds": duration,
            },
        )
        self._notify_listeners()

        # --- 7. Notifications & Auto-Quarantine ---
        notify_success: bool = self.options.get(CONF_NOTIFY_SUCCESS, DEFAULT_NOTIFY_SUCCESS)

        if updated_items and notify_success:
            self._send_success_notification(updated_items)
            self._send_push_notification(
                "Updates Installed",
                "{} update(s) installed successfully.".format(len(updated_items)),
            )

        if failed_items:
            await self._handle_failures(failed_items)

        # --- 8. Restart HA if enabled and any installed update requires it ---
        auto_restart: bool = self.options.get(CONF_AUTO_RESTART, DEFAULT_AUTO_RESTART)
        non_system_updates = [
            u for u in updated_items if u["entity_id"] not in _HA_SYSTEM_UPDATE_ENTITIES
        ]
        needs_restart = any(restart_required_map.get(u["entity_id"], True) for u in non_system_updates)

        if system_updates_triggered:
            _LOGGER.info(
                "Auto Updater: system-level update (OS/Supervisor/Core) was triggered in this run. "
                "Skipping explicit HA restart call so the system update process completes natively."
            )
            if deferred_items:
                self._schedule_resume_run(deferred_items)
        elif auto_restart and non_system_updates and needs_restart:
            _LOGGER.info(
                "Auto Updater: restarting HA — %d non-system update(s) require a restart.",
                len(non_system_updates),
            )
            self._send_status_notification(
                "Restarting…",
                "HA is restarting to apply {} update(s): {}".format(
                    len(non_system_updates),
                    ", ".join(u["title"] for u in non_system_updates),
                ),
            )
            await asyncio.sleep(3)  # brief pause so notifications are saved first
            await self.hass.services.async_call("homeassistant", "restart")

    # ------------------------------------------------------------------
    # Run bookkeeping helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_history_entry(
        when: datetime,
        updated_items: list[dict],
        failed_items: list[dict],
        duration: int,
        triggered: list[dict] | None = None,
        deferred: list[dict] | None = None,
        note: str | None = None,
    ) -> dict:
        """Build a history entry. Titles are kept for display; entity ids are the key."""
        entry = {
            "timestamp": when.isoformat(),
            "updated": [
                "{} ({} → {})".format(u["title"], u.get("from", "?"), u.get("to", "?"))
                for u in updated_items
            ],
            "failed": [f["title"] for f in failed_items],
            "updated_entities": [
                {"entity_id": u["entity_id"], "title": u["title"], "from": u.get("from"), "to": u.get("to")}
                for u in updated_items
            ],
            "failed_entities": [
                {"entity_id": f["entity_id"], "title": f["title"], "from": f.get("from"), "to": f.get("to")}
                for f in failed_items
            ],
            "total_updated": len(updated_items),
            "total_failed": len(failed_items),
            "duration_seconds": duration,
        }
        if triggered:
            entry["pending_verification"] = [
                "{} ({} → {})".format(t["title"], t.get("from", "?"), t.get("to", "?")) for t in triggered
            ]
        if deferred:
            entry["deferred"] = [d["title"] for d in deferred]
        if note:
            entry["note"] = note
        return entry

    async def _handle_failures(self, failed_items: list[dict]) -> None:
        """Notify about failed items and auto-quarantine repeat offenders."""
        notify_failure: bool = self.options.get(CONF_NOTIFY_FAILURE, DEFAULT_NOTIFY_FAILURE)
        auto_quarantine: bool = self.options.get(CONF_AUTO_QUARANTINE, DEFAULT_AUTO_QUARANTINE)
        failed_names = [f["title"] for f in failed_items]
        escalated_items = [
            f for f in failed_items
            if self._consecutive_failures(f["title"], f["entity_id"]) >= FAILURE_ESCALATION_THRESHOLD
        ]
        escalated = [f["title"] for f in escalated_items]

        if notify_failure:
            self._send_failure_notification(failed_names, escalated)
            push_msg = "{} update(s) failed: {}".format(len(failed_names), ", ".join(failed_names))
            if escalated:
                push_msg += "\n⚠️ Repeatedly failing ({}+ runs): {}".format(
                    FAILURE_ESCALATION_THRESHOLD, ", ".join(escalated)
                )
            self._send_push_notification("Update Failures", push_msg)

        if auto_quarantine and escalated_items:
            for f in escalated_items:
                _LOGGER.warning(
                    "Auto Updater: auto-quarantining %s (%s) after %d consecutive failures — snoozing for %d days.",
                    f["title"], f["entity_id"], FAILURE_ESCALATION_THRESHOLD, DEFAULT_SNOOZE_DAYS,
                )
                await self.async_snooze_update(f["entity_id"], DEFAULT_SNOOZE_DAYS)

    async def _install_with_retry(
        self, entity_id: str, title: str, latest: str, is_system_update: bool, retry_delay: int
    ) -> str:
        """Call update.install with one retry.

        Returns "success", "pending" (system update triggered — result is
        verified on a later scan) or "failed".
        """
        for attempt in range(2):
            try:
                _LOGGER.debug(
                    "Auto Updater: calling update.install for %s (attempt %d, blocking=%s)",
                    title, attempt + 1, not is_system_update,
                )
                # Supervisor and OS updates restart their own process during
                # install, so blocking=True would time out — use non-blocking.
                await asyncio.wait_for(
                    self.hass.services.async_call(
                        "update",
                        "install",
                        {"entity_id": entity_id},
                        blocking=not is_system_update,
                    ),
                    timeout=300,  # 5-minute per-update timeout
                )
            except Exception as exc:  # noqa: BLE001
                if attempt == 0:
                    _LOGGER.warning(
                        "Auto Updater: attempt 1 failed for %s — %s  Retrying in %ds…",
                        title, exc, retry_delay,
                    )
                    await asyncio.sleep(retry_delay)
                    continue
                _LOGGER.error("Auto Updater: ✗ %s failed after retry — %s", title, exc)
                return "failed"
            if is_system_update:
                # Non-blocking call reports nothing — watch the entity to see
                # whether the install actually started.
                return await self._watch_system_update(entity_id, latest)
            return "success"
        return "failed"

    async def _watch_system_update(self, entity_id: str, latest: str) -> str:
        """Observe a non-blocking Core/OS/Supervisor install.

        Returns "success" once the entity reports the new version installed, or
        "pending" when the outcome can't be confirmed yet (install underway, or
        HA/Supervisor restarting). Pending items are verified by later scans and
        turned into a success or failure history entry there — so a rejected
        system update is no longer reported as a success.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SYSTEM_UPDATE_WATCH_SECONDS
        while True:
            st = self.hass.states.get(entity_id)
            if st is None or st.state in ("unavailable", "unknown"):
                return "pending"
            if st.state == "off" or str(st.attributes.get("installed_version")) == str(latest):
                return "success"
            if loop.time() >= deadline:
                if not st.attributes.get("in_progress"):
                    _LOGGER.warning(
                        "Auto Updater: %s shows no install progress after %ds — will verify later.",
                        entity_id, SYSTEM_UPDATE_WATCH_SECONDS,
                    )
                return "pending"
            await asyncio.sleep(SYSTEM_UPDATE_POLL_SECONDS)

    async def _async_verify_pending(self) -> None:
        """Resolve system updates that were triggered but not yet confirmed."""
        if not self._pending_verification:
            return
        now = dt_util.now()
        still_pending: list[dict] = []
        verified_ok: list[dict] = []
        verified_failed: list[dict] = []
        for item in self._pending_verification:
            eid = item.get("entity_id")
            st = self.hass.states.get(eid) if eid else None
            triggered_at = dt_util.parse_datetime(item.get("triggered_at", "")) or now
            if triggered_at.tzinfo is None:
                triggered_at = dt_util.as_utc(triggered_at)
            age = (now - triggered_at).total_seconds()
            if st is None or st.state in ("unavailable", "unknown"):
                # Entity not (re)loaded yet — HA is probably still starting up.
                if age > 86400:
                    verified_failed.append(item)
                else:
                    still_pending.append(item)
                continue
            installed = str(st.attributes.get("installed_version"))
            if st.state == "off" or installed == str(item.get("to")):
                verified_ok.append(item)
            elif item.get("from") not in (None, "?") and installed != str(item.get("from")):
                # Moved off the version we started from (e.g. a newer release
                # landed in between) — the install we triggered did happen.
                verified_ok.append(item)
            elif st.attributes.get("in_progress") and age < 86400:
                still_pending.append(item)
            else:
                # Still sitting on the original version with nothing in flight:
                # the Supervisor rejected or dropped the install.
                verified_failed.append(item)

        self._pending_verification = still_pending
        if not verified_ok and not verified_failed:
            return
        await self._save_run_state(None)

        for u in verified_ok:
            _LOGGER.info("Auto Updater: ✓ verified %s  %s → %s", u["title"], u.get("from"), u.get("to"))
        for f in verified_failed:
            _LOGGER.error(
                "Auto Updater: ✗ %s did not install (%s still at %s).",
                f["title"], f["entity_id"], f.get("from"),
            )
        await self._append_and_save_history(
            self._build_history_entry(
                now, verified_ok, verified_failed, 0, note="Verified system update(s)"
            )
        )
        if verified_ok and self.options.get(CONF_NOTIFY_SUCCESS, DEFAULT_NOTIFY_SUCCESS):
            self._send_success_notification(verified_ok)
        if verified_failed:
            failed_ids = {v["entity_id"] for v in verified_failed}
            self.failed_updates = [
                {"title": f["title"], "entity_id": f["entity_id"]} for f in verified_failed
            ] + [f for f in self.failed_updates if f.get("entity_id") not in failed_ids]
            await self._handle_failures(verified_failed)
        self._notify_listeners()

    def _schedule_resume_run(self, deferred: list[dict]) -> None:
        """Queue a follow-up run for updates deferred behind a system update."""
        if self._unsub_resume is not None:
            self._unsub_resume()
            self._unsub_resume = None
        if not self.enabled:
            return

        async def _resume(_now) -> None:
            self._unsub_resume = None
            if not self.enabled:
                _LOGGER.info("Auto Updater: disabled — skipping follow-up pass; deferred updates wait for the next run.")
                return
            if self._is_running:
                # A manual run is in flight; try again shortly rather than
                # dropping the deferred items on the floor.
                self._schedule_resume_run(deferred)
                return
            _LOGGER.info(
                "Auto Updater: running follow-up pass for %d deferred update(s).", len(deferred)
            )
            await self.async_run_updates(resume=True)

        _LOGGER.info(
            "Auto Updater: %d update(s) deferred — follow-up run in %d minutes.",
            len(deferred), RESUME_RUN_DELAY_MINUTES,
        )
        self._unsub_resume = async_call_later(
            self.hass, timedelta(minutes=RESUME_RUN_DELAY_MINUTES), _resume
        )

    async def _async_reconcile_interrupted_run(self) -> None:
        """On startup, turn a leftover run marker into a history entry.

        A Core/OS update restarts HA part-way through a run, so the normal
        end-of-run bookkeeping never happens. Without this, the partial run is
        lost: successes go unreported and failures never count toward
        quarantine.
        """
        marker = await self._load_run_state()
        if marker is not None:
            _LOGGER.warning("Auto Updater: previous run was interrupted — reconstructing its result.")
            await self._async_finalize_run_marker(
                note="Run interrupted by restart (reconstructed on startup)", marker=marker
            )
        # Either the reconstruction above or a completed run that ended with a
        # system update left a follow-up queue behind; the restart killed the
        # in-process timer, so re-arm it here.
        if self._deferred:
            self._schedule_resume_run(list(self._deferred))

    async def _async_finalize_run_marker(
        self, note: str, marker: dict | None = None
    ) -> list[dict]:
        """Write history from a run marker and clear it. Returns items never attempted."""
        if marker is None:
            marker = await self._load_run_state()
        if marker is None:
            return []
        updated = [i for i in marker.get("updated", []) if isinstance(i, dict)]
        failed = [i for i in marker.get("failed", []) if isinstance(i, dict)]
        triggered = [i for i in marker.get("triggered", []) if isinstance(i, dict)]
        current = marker.get("current")
        if isinstance(current, dict):
            # Whatever was mid-install when we stopped — verify it by entity state
            # rather than guessing.
            triggered.append({**current, "triggered_at": dt_util.now().isoformat()})
        attempted = {i["entity_id"] for i in updated + failed + triggered if "entity_id" in i}
        remaining = [
            i for i in marker.get("items", [])
            if isinstance(i, dict) and i.get("entity_id") not in attempted
        ]
        remaining += [i for i in marker.get("deferred", []) if isinstance(i, dict)]

        started = dt_util.parse_datetime(marker.get("started", "")) or dt_util.now()
        if started.tzinfo is None:
            started = dt_util.as_utc(started)
        duration = max(0, int((dt_util.now() - started).total_seconds()))
        self._pending_verification.extend(triggered)
        self._deferred = list(remaining)
        await self._append_and_save_history(
            self._build_history_entry(
                dt_util.now(), updated, failed, duration,
                triggered=triggered, deferred=remaining, note=note,
            )
        )
        await self._save_run_state(None)
        self.last_run = dt_util.now()
        self.last_run_count = len(updated)
        self.last_run_failed = len(failed)
        self.last_run_duration = duration
        self.last_run_status = "Interrupted"
        self.failed_updates = [{"title": f["title"], "entity_id": f["entity_id"]} for f in failed]
        if failed:
            await self._handle_failures(failed)
        return remaining

    async def _load_run_state(self) -> dict | None:
        """Load the run marker (and pending verification list). Returns marker or None."""
        path = self.hass.config.path(RUN_STATE_FILE)
        try:
            data = await self._read_json_file(path, {})
        except (json.JSONDecodeError, OSError) as exc:
            _LOGGER.warning("Auto Updater: could not load run state — %s", exc)
            return None
        if not isinstance(data, dict):
            return None
        pending = data.get("pending_verification", [])
        if isinstance(pending, list):
            known = {p.get("entity_id") for p in self._pending_verification}
            self._pending_verification.extend(
                p for p in pending if isinstance(p, dict) and p.get("entity_id") not in known
            )
        deferred = data.get("deferred", [])
        if isinstance(deferred, list) and deferred and not self._deferred:
            self._deferred = [d for d in deferred if isinstance(d, dict)]
        run = data.get("run")
        return run if isinstance(run, dict) else None

    async def _save_run_state(self, marker: dict | None) -> None:
        """Persist the in-progress run marker, pending verification list and deferred queue."""
        path = self.hass.config.path(RUN_STATE_FILE)
        tmp = path + ".tmp"
        data = {
            "run": marker,
            "pending_verification": list(self._pending_verification),
            "deferred": list(self._deferred),
        }

        def _write():
            if marker is None and not data["pending_verification"] and not data["deferred"]:
                if os.path.exists(path):
                    os.remove(path)
                return
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp, path)

        try:
            await self.hass.async_add_executor_job(_write)
        except OSError as exc:
            _LOGGER.error("Auto Updater: could not save run state — %s", exc)

    # ------------------------------------------------------------------
    # Backup
    # ------------------------------------------------------------------

    async def _create_backup(self) -> bool:
        name = "pre_update_{}".format(dt_util.now().strftime("%Y%m%d_%H%M"))

        # HA 2025.1+ backup manager. The backup.create service returns no id and
        # there is no backup.delete service, so on modern installs the auto-purge
        # feature silently never had anything to delete. Going through the manager
        # lets us name the backup, find its id, and delete it later.
        manager = self._get_backup_manager()
        if manager is not None:
            try:
                ref = await asyncio.wait_for(
                    self._create_backup_via_manager(manager, name),
                    timeout=BACKUP_TIMEOUT_SECONDS,
                )
            except (TypeError, AttributeError, NotImplementedError) as exc:
                # API shape differs from what we expect — use the service instead.
                _LOGGER.debug("Auto Updater: backup manager API unavailable (%s) — using service.", exc)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Auto Updater: backup via backup manager failed — %s", exc)
                return False
            else:
                if ref is None:
                    _LOGGER.debug("Auto Updater: backup created but its id could not be determined.")
                await self._record_backup("backup", name, ref)
                await self._purge_old_backups()
                return True

        # Try modern backup domain first, then Supervisor (HA OS)
        for domain, service, data in [
            ("backup", "create", {}),
            ("hassio", "backup_full", {"name": name}),
        ]:
            if not self.hass.services.has_service(domain, service):
                continue
            try:
                ref = await self._call_backup_service(domain, service, data)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Auto Updater: backup via %s.%s failed — %s", domain, service, exc)
                return False
            await self._record_backup(domain, name, ref)
            await self._purge_old_backups()
            return True
        _LOGGER.warning("Auto Updater: no backup service found — skipping backup.")
        return False

    def _get_backup_manager(self):
        """Return the HA backup manager (2025.1+) or None if unavailable."""
        try:
            from homeassistant.components.backup.const import DATA_MANAGER
        except Exception:  # noqa: BLE001
            DATA_MANAGER = "backup"  # HassKey is a str subclass, so this matches too
        try:
            data = self.hass.data
            manager = data.get(DATA_MANAGER) if hasattr(data, "get") else None
        except Exception:  # noqa: BLE001
            return None
        if manager is None or not hasattr(manager, "async_create_backup"):
            return None
        return manager

    @staticmethod
    def _local_backup_agent_ids(manager) -> list[str]:
        local = getattr(manager, "local_backup_agents", None)
        if local:
            return [a for a in local if isinstance(a, str)]
        agents = getattr(manager, "backup_agents", None) or {}
        return [
            a for a in agents
            if isinstance(a, str) and (a.startswith("backup.") or a.startswith("hassio."))
        ]

    async def _create_backup_via_manager(self, manager, name: str):
        """Create a named backup through the backup manager. Returns its id or None."""
        agent_ids = self._local_backup_agent_ids(manager)
        if not agent_ids:
            raise NotImplementedError("no local backup agent registered")
        # On HA OS / Supervised the whole point of the pre-update backup is to
        # cover the add-ons we are about to update, so include them there.
        include_addons = "hassio" in getattr(self.hass.config, "components", ())
        _LOGGER.debug(
            "Auto Updater: creating backup '%s' via backup manager (agents=%s, addons=%s)",
            name, agent_ids, include_addons,
        )
        await manager.async_create_backup(
            agent_ids=agent_ids,
            include_addons=None,
            include_all_addons=include_addons,
            include_database=True,
            include_folders=None,
            include_homeassistant=True,
            name=name,
            password=None,
        )
        # async_create_backup awaits completion on current HA, but guard against
        # a version that only initiates: wait until the manager is idle or the
        # backup is listed under our name.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + BACKUP_TIMEOUT_SECONDS
        while True:
            ref = await self._find_backup_id_by_name(manager, name)
            state = getattr(manager, "state", None)
            idle = state is None or str(getattr(state, "value", state)).lower().endswith("idle")
            if ref is not None or idle:
                return ref
            if loop.time() >= deadline:
                raise TimeoutError("backup did not finish in time")
            await asyncio.sleep(5)

    @staticmethod
    async def _find_backup_id_by_name(manager, name: str):
        try:
            result = await manager.async_get_backups()
        except Exception:  # noqa: BLE001
            return None
        backups = result[0] if isinstance(result, tuple) else result
        if not isinstance(backups, dict):
            return None
        for backup_id, b in backups.items():
            if getattr(b, "name", None) == name:
                return getattr(b, "backup_id", backup_id)
        return None

    async def _call_backup_service(self, domain: str, service: str, data: dict):
        """Create a backup. Return its slug/id if the service can report one."""
        if self._service_supports_response(domain, service):
            resp = await asyncio.wait_for(
                self.hass.services.async_call(
                    domain, service, data, blocking=True, return_response=True
                ),
                timeout=BACKUP_TIMEOUT_SECONDS,
            )
            if isinstance(resp, dict):
                return (
                    resp.get("slug")
                    or resp.get("backup_id")
                    or (resp.get("backup") or {}).get("backup_id")
                )
            return None
        await asyncio.wait_for(
            self.hass.services.async_call(domain, service, data, blocking=True),
            timeout=BACKUP_TIMEOUT_SECONDS,
        )
        return None

    def _service_supports_response(self, domain: str, service: str) -> bool:
        """Return True if the service can return response data (newer HA)."""
        from homeassistant.core import SupportsResponse

        svc = self.hass.services.async_services().get(domain, {}).get(service)
        if svc is None:
            return False
        return getattr(svc, "supports_response", SupportsResponse.NONE) in (
            SupportsResponse.OPTIONAL,
            SupportsResponse.ONLY,
        )

    async def _record_backup(self, domain: str, name: str, ref) -> None:
        """Track a backup we created so it can be auto-purged later."""
        self._tracked_backups.append({
            "ref": ref,
            "domain": domain,
            "name": name,
            "created": dt_util.now().isoformat(),
        })
        await self._save_backup_state()

    async def _purge_old_backups(self) -> None:
        """Delete pre-update backups older than the configured retention period."""
        if not self.options.get(CONF_BACKUP_CLEANUP, DEFAULT_BACKUP_CLEANUP):
            return
        keep_days = int(self.options.get(CONF_BACKUP_KEEP_DAYS, DEFAULT_BACKUP_KEEP_DAYS))
        cutoff = dt_util.now() - timedelta(days=keep_days)
        remaining: list[dict] = []
        purged = 0
        for b in self._tracked_backups:
            created = dt_util.parse_datetime(b.get("created", ""))
            ref = b.get("ref")
            if created is not None and created.tzinfo is None:
                created = dt_util.as_utc(created)
            if created is not None and created < cutoff:
                if not ref:
                    # Never learned an id for it (older HA) — nothing we can
                    # delete, so stop carrying it around forever.
                    continue
                if await self._delete_backup(b.get("domain", ""), ref):
                    purged += 1
                    continue  # drop from tracking
                # delete failed — keep tracking so we retry next time
            remaining.append(b)
        changed = len(remaining) != len(self._tracked_backups)
        self._tracked_backups = remaining
        if purged:
            _LOGGER.info("Auto Updater: purged %d old pre-update backup(s).", purged)
        if changed:
            await self._save_backup_state()

    async def _delete_backup(self, domain: str, ref) -> bool:
        """Best-effort delete of a single backup by slug/id."""
        manager = self._get_backup_manager()
        if manager is not None and hasattr(manager, "async_delete_backup"):
            try:
                errors = await asyncio.wait_for(manager.async_delete_backup(ref), timeout=120)
            except (TypeError, AttributeError):
                pass  # unexpected API shape — try the services below
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Auto Updater: failed to delete backup %s via backup manager — %s", ref, exc)
                return False
            else:
                if isinstance(errors, dict) and errors:
                    _LOGGER.warning(
                        "Auto Updater: backup %s could not be deleted from all agents — %s", ref, errors
                    )
                    return False
                return True
        for d, s, data in [
            ("backup", "delete", {"backup_id": ref}),
            ("hassio", "backup_remove", {"slug": ref}),
            ("hassio", "remove_backup", {"slug": ref}),
        ]:
            if self.hass.services.has_service(d, s):
                try:
                    await asyncio.wait_for(
                        self.hass.services.async_call(d, s, data, blocking=True),
                        timeout=120,
                    )
                    return True
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning(
                        "Auto Updater: failed to delete backup %s via %s.%s — %s", ref, d, s, exc
                    )
                    return False
        _LOGGER.debug("Auto Updater: no delete service available for backup %s.", ref)
        return False

    async def _load_backup_state(self) -> None:
        path = self.hass.config.path(BACKUP_STATE_FILE)
        try:
            data = await self._read_json_file(path, {})
            backups = data.get("backups", []) if isinstance(data, dict) else []
            self._tracked_backups = [b for b in backups if isinstance(b, dict)]
        except (json.JSONDecodeError, OSError) as exc:
            _LOGGER.warning("Auto Updater: could not load backup state — %s", exc)

    async def _save_backup_state(self) -> None:
        path = self.hass.config.path(BACKUP_STATE_FILE)
        tmp = path + ".tmp"
        data = {"backups": list(self._tracked_backups)}

        def _write():
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp, path)

        try:
            await self.hass.async_add_executor_job(_write)
        except OSError as exc:
            _LOGGER.error("Auto Updater: could not save backup state — %s", exc)

    # ------------------------------------------------------------------
    # History persistence
    # ------------------------------------------------------------------

    async def _read_json_file(self, path: str, default):
        """Read and return parsed JSON from path, or default if missing/unreadable."""
        def _read():
            if not os.path.exists(path):
                return default
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return await self.hass.async_add_executor_job(_read)

    async def _load_history(self) -> None:
        path = self.hass.config.path(HISTORY_FILE)

        try:
            raw = await self._read_json_file(path, [])
            self.history = raw if isinstance(raw, list) else []
            _LOGGER.info("Auto Updater: loaded %d history entries.", len(self.history))
            if self.history:
                last = self.history[-1]
                restored = dt_util.parse_datetime(last.get("timestamp", ""))
                if restored is not None:
                    self.last_run = restored
                    self.last_run_count = last.get("total_updated", 0)
                    self.last_run_failed = last.get("total_failed", 0)
                    self.last_run_duration = last.get("duration_seconds", 0)
                    # Restore failed_updates list so FailedUpdatesSensor is accurate
                    failed_entities = last.get("failed_entities")
                    if isinstance(failed_entities, list) and failed_entities:
                        self.failed_updates = [
                            {"title": f.get("title", f.get("entity_id")), "entity_id": f.get("entity_id")}
                            for f in failed_entities if isinstance(f, dict)
                        ]
                    else:
                        failed_names = last.get("failed", [])
                        self.failed_updates = [
                            {"title": n, "entity_id": n} for n in failed_names
                        ]
                    # Restore last_run_status
                    self.last_run_status = self._derive_status(
                        self.last_run_count, self.last_run_failed
                    )
                    _LOGGER.info(
                        "Auto Updater: last run restored as %s (%d updated, %d failed, status=%s)",
                        self.last_run, self.last_run_count, self.last_run_failed, self.last_run_status,
                    )
                    # Re-post notifications if the last run was recent — OS/Supervisor
                    # updates reboot HA, wiping persistent notifications before the
                    # user can see the results.
                    age = (dt_util.now() - restored).total_seconds()
                    if age < 7200:  # within 2 hours
                        updated = last.get("updated", [])
                        failed = last.get("failed", [])
                        if updated:
                            rows = "\n".join(f"- {u}" for u in updated)
                            async_create(
                                self.hass,
                                "**Successfully updated ({}):**\n{}\n\n"
                                "*(Restored after restart)*".format(len(updated), rows),
                                title="Auto Updater — Updates installed",
                                notification_id="ha_auto_updater_success",
                            )
                        if failed:
                            rows = "\n".join(f"- {n}" for n in failed)
                            async_create(
                                self.hass,
                                "**Failed to update ({}):**\n{}\n\nCheck logs for details.\n\n"
                                "*(Restored after restart)*".format(len(failed), rows),
                                title="Auto Updater — Update failures",
                                notification_id="ha_auto_updater_failure",
                            )
        except (json.JSONDecodeError, OSError) as exc:
            _LOGGER.warning("Auto Updater: could not load history — %s", exc)
            self.history = []

    async def _load_digest_state(self) -> None:
        path = self.hass.config.path(DIGEST_STATE_FILE)

        try:
            data = await self._read_json_file(path, {})
            ts = data.get("last_digest_sent")
            if ts:
                self._last_digest_sent = dt_util.parse_datetime(ts)
        except (json.JSONDecodeError, OSError) as exc:
            _LOGGER.warning("Auto Updater: could not load digest state — %s", exc)

    async def _save_digest_state(self) -> None:
        path = self.hass.config.path(DIGEST_STATE_FILE)
        tmp = path + ".tmp"
        ts = self._last_digest_sent.isoformat() if self._last_digest_sent else None

        def _write():
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"last_digest_sent": ts}, f)
            os.replace(tmp, path)

        try:
            await self.hass.async_add_executor_job(_write)
        except OSError as exc:
            _LOGGER.error("Auto Updater: could not save digest state — %s", exc)

    async def _save_history(self) -> None:
        path = self.hass.config.path(HISTORY_FILE)
        tmp_path = path + ".tmp"
        data = self.history

        def _write():
            # Write to a temp file first, then atomically replace the real file.
            # This prevents a corrupt history file if HA crashes mid-write.
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp_path, path)

        try:
            await self.hass.async_add_executor_job(_write)
        except OSError as exc:
            _LOGGER.error("Auto Updater: could not save history — %s", exc)

    async def _append_and_save_history(self, entry: dict) -> None:
        self.history.append(entry)
        if len(self.history) > MAX_HISTORY_ENTRIES:
            self.history = self.history[-MAX_HISTORY_ENTRIES:]
        await self._save_history()

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def _send_success_notification(self, updated_items: list[dict]) -> None:
        rows = "\n".join(self._format_updated_row(u) for u in updated_items)
        async_create(
            self.hass,
            "**Successfully updated ({}):**\n{}".format(len(updated_items), rows),
            title="Auto Updater — Updates installed",
            notification_id="ha_auto_updater_success",
        )

    @staticmethod
    def _format_updated_row(u: dict) -> str:
        """Format one updated item, linking to release notes when available."""
        base = "- {} ({} → {})".format(u["title"], u["from"], u["to"])
        url = u.get("release_url")
        if url:
            base += " — [release notes]({})".format(url)
        return base

    def _send_failure_notification(self, failed: list[str], escalated: list[str] | None = None) -> None:
        escalated = escalated or []
        rows = "\n".join(
            "- {}{}".format(n, "  ⚠️ repeatedly failing" if n in escalated else "")
            for n in failed
        )
        message = "**Failed to update ({}):**\n{}\n\nCheck logs for details.".format(len(failed), rows)
        if escalated:
            message += (
                "\n\n⚠️ **{} component(s) have failed {}+ runs in a row** and may need "
                "manual attention or exclusion: {}".format(
                    len(escalated), FAILURE_ESCALATION_THRESHOLD, ", ".join(escalated)
                )
            )
        async_create(
            self.hass,
            message,
            title="Auto Updater — Update failures",
            notification_id="ha_auto_updater_failure",
        )

    def _send_pre_update_notification(self, titles: list[str], delay_minutes: int) -> None:
        rows = "\n".join("- {}".format(t) for t in titles)
        async_create(
            self.hass,
            "The following updates will install in **{} minute{}**:\n{}".format(
                delay_minutes, "s" if delay_minutes != 1 else "", rows
            ),
            title="Auto Updater — Updates starting soon",
            notification_id="ha_auto_updater_pre_update",
        )

    def _send_status_notification(
        self, title: str, message: str, notification_id: str = "ha_auto_updater_status"
    ) -> None:
        async_create(
            self.hass,
            message,
            title="Auto Updater — {}".format(title),
            notification_id=notification_id,
        )

    def _dismiss_notification(self, notification_id: str) -> None:
        self.hass.async_create_task(
            self.hass.services.async_call(
                "persistent_notification",
                "dismiss",
                {"notification_id": notification_id},
            )
        )

    # ------------------------------------------------------------------
    # Weekly digest
    # ------------------------------------------------------------------

    async def _async_weekly_digest(self, _now) -> None:
        """Send a weekly summary of update activity over the past 7 days."""
        if not self.options.get(CONF_WEEKLY_DIGEST, DEFAULT_WEEKLY_DIGEST):
            return
        week_ago = dt_util.now() - timedelta(days=7)
        recent = []
        for e in self.history:
            ts = dt_util.parse_datetime(e.get("timestamp", ""))
            if ts is None or ts < week_ago:
                continue
            if e.get("total_updated", 0) > 0 or e.get("total_failed", 0) > 0:
                recent.append(e)
        total_updated = sum(e.get("total_updated", 0) for e in recent)
        total_failed = sum(e.get("total_failed", 0) for e in recent)

        if total_updated == 0 and total_failed == 0:
            message = "No updates were installed in the past week."
        else:
            all_updated = [item for e in recent for item in e.get("updated", [])]
            all_failed = [item for e in recent for item in e.get("failed", [])]
            run_count = len(recent)
            avg_duration = (
                sum(e.get("duration_seconds", 0) for e in recent) // run_count
                if run_count else 0
            )
            rows_updated = "\n".join("- {}".format(u) for u in all_updated) if all_updated else ""
            rows_failed = "\n".join("- {}".format(f) for f in all_failed) if all_failed else ""
            message = (
                "**Runs this week:** {runs}  |  **Updated:** {updated}  |  **Failed:** {failed}"
                "  |  **Avg duration:** {avg}s"
                "{updated_items}"
                "{failed_items}"
            ).format(
                runs=run_count,
                updated=total_updated,
                failed=total_failed,
                avg=avg_duration,
                updated_items="\n\n**Updated:**\n" + rows_updated if rows_updated else "",
                failed_items="\n\n**Failed:**\n" + rows_failed if rows_failed else "",
            )

        async_create(
            self.hass,
            message,
            title="Auto Updater — Weekly Digest",
            notification_id="ha_auto_updater_weekly_digest",
        )
        self._send_push_notification("Weekly Digest", message.replace("**", ""))
        self._last_digest_sent = dt_util.now()
        await self._save_digest_state()
        _LOGGER.info("Auto Updater: weekly digest sent (%d updated, %d failed).", total_updated, total_failed)

    # ------------------------------------------------------------------
    # Push notification helper
    # ------------------------------------------------------------------

    def _send_push_notification(self, title: str, message: str) -> None:
        """Forward a notification to a user-configured notify service (e.g. mobile app)."""
        notify_service: str = self.options.get(CONF_NOTIFY_SERVICE, DEFAULT_NOTIFY_SERVICE)
        if not notify_service or "." not in notify_service:
            return
        domain, service = notify_service.split(".", 1)
        self.hass.async_create_task(
            self.hass.services.async_call(
                domain,
                service,
                {"title": "Auto Updater — {}".format(title), "message": message},
            )
        )

    # ------------------------------------------------------------------
    # Update source helper
    # ------------------------------------------------------------------

    def _get_update_source(self, entity_id: str) -> str:
        """Categorise an update entity as HA System, Add-on, HACS, Firmware, or Custom."""
        if entity_id in _HA_SYSTEM_UPDATE_ENTITIES:
            return "HA System"
        registry = er.async_get(self.hass)
        entry = registry.async_get(entity_id)
        platform = entry.platform if entry is not None else None
        if platform == "hassio":
            return "Add-on"
        if platform == "hacs":
            return "HACS"
        # Firmware: trust the entity's own device_class first (any integration
        # that ships device firmware sets it — Shelly, Tasmota, WLED, UniFi,
        # Zigbee2MQTT via MQTT, …), then fall back to known device platforms
        # that don't always set it.
        state = self.hass.states.get(entity_id)
        device_class = state.attributes.get("device_class") if state is not None else None
        if device_class == FIRMWARE_DEVICE_CLASS or platform in FIRMWARE_PLATFORMS:
            return "Firmware"
        return "Custom"

    # ------------------------------------------------------------------
    # Snooze (per-update temporary skip)
    # ------------------------------------------------------------------

    def _is_snoozed(self, entity_id: str) -> bool:
        until = self._snoozed.get(entity_id)
        if not until:
            return False
        dt = dt_util.parse_datetime(until)
        if dt is None:
            self._snoozed.pop(entity_id, None)
            return False
        if dt.tzinfo is None:
            dt = dt_util.as_utc(dt)
        if dt <= dt_util.now():
            self._snoozed.pop(entity_id, None)  # expired — lazy cleanup
            return False
        return True

    def snoozed_summary(self) -> list:
        """Active snoozes for sensor display."""
        now = dt_util.now()
        out = []
        for eid, until in self._snoozed.items():
            dt = dt_util.parse_datetime(until)
            if dt is None:
                continue
            if dt.tzinfo is None:
                dt = dt_util.as_utc(dt)
            if dt > now:
                out.append({"entity_id": eid, "until": until})
        return out

    async def async_snooze_update(self, entity_id: str, days: int = DEFAULT_SNOOZE_DAYS) -> None:
        """Skip a specific update entity for the given number of days."""
        until = dt_util.now() + timedelta(days=days)
        self._snoozed[entity_id] = until.isoformat()
        _LOGGER.info("Auto Updater: snoozed %s for %d day(s) (until %s).", entity_id, days, until)
        await self._save_snooze()
        await self._async_scan_pending(None)

    async def async_clear_snooze(self, entity_id: str | None = None) -> None:
        """Clear a single snooze, or all snoozes when entity_id is None."""
        if entity_id:
            self._snoozed.pop(entity_id, None)
            _LOGGER.info("Auto Updater: cleared snooze for %s.", entity_id)
        else:
            self._snoozed.clear()
            _LOGGER.info("Auto Updater: cleared all snoozes.")
        await self._save_snooze()
        await self._async_scan_pending(None)

    async def _load_snooze(self) -> None:
        path = self.hass.config.path(SNOOZE_STATE_FILE)
        try:
            data = await self._read_json_file(path, {})
            if isinstance(data, dict):
                self._snoozed = {k: v for k, v in data.items() if isinstance(v, str)}
        except (json.JSONDecodeError, OSError) as exc:
            _LOGGER.warning("Auto Updater: could not load snooze state — %s", exc)

    async def _save_snooze(self) -> None:
        path = self.hass.config.path(SNOOZE_STATE_FILE)
        tmp = path + ".tmp"
        data = dict(self._snoozed)

        def _write():
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, path)

        try:
            await self.hass.async_add_executor_job(_write)
        except OSError as exc:
            _LOGGER.error("Auto Updater: could not save snooze state — %s", exc)

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    def _consecutive_failures(self, title: str, entity_id: str | None = None) -> int:
        """Count how many of the most recent runs failed this component in a row.

        Stops counting at the first run where the component updated successfully.
        Runs that did not involve the component at all are skipped.

        Newer entries carry entity ids, which are the reliable key (two devices
        can share a title, and a title can change between releases). Older
        entries only have titles, so fall back to those.
        """
        count = 0
        for entry in reversed(self.history):
            failed_entities = entry.get("failed_entities")
            updated_entities = entry.get("updated_entities")
            if entity_id and (failed_entities is not None or updated_entities is not None):
                failed_ids = {
                    f.get("entity_id") for f in (failed_entities or []) if isinstance(f, dict)
                }
                updated_ids = {
                    u.get("entity_id") for u in (updated_entities or []) if isinstance(u, dict)
                }
                if entity_id in failed_ids:
                    count += 1
                elif entity_id in updated_ids:
                    break
                continue
            if title in entry.get("failed", []):
                count += 1
            elif any(str(u).startswith(title) for u in entry.get("updated", [])):
                break
        return count

    @staticmethod
    def _derive_status(updated: int, failed: int) -> str:
        """Return a human-readable status string for a completed run."""
        if updated == 0 and failed == 0:
            return "No updates"
        if failed == 0:
            return "Success"
        if updated == 0:
            return "All failed"
        return "Partial failure"

    @staticmethod
    def _is_major_bump(attrs: dict) -> bool:
        inst_str = attrs.get("installed_version")
        late_str = attrs.get("latest_version")
        if not inst_str or not late_str or str(inst_str) in ("?", "") or str(late_str) in ("?", ""):
            return False
        try:
            inst_ver = AwesomeVersion(str(inst_str))
            late_ver = AwesomeVersion(str(late_str))
            # Calendar versioning (year >= 2000, e.g. 2026.3.0).
            # Year-to-year increments are routine HA releases — never treat as major bump.
            if late_ver.strategy == AwesomeVersionStrategy.CALVER or (late_ver.section(0) and late_ver.section(0) >= 2000):
                return False
            if late_ver.section(0) is not None and inst_ver.section(0) is not None:
                return int(late_ver.section(0)) > int(inst_ver.section(0))
        except Exception:
            pass

        # Fallback to string split logic stripping leading 'v'
        try:
            installed = str(inst_str).lstrip("vV").split(".")[0]
            latest = str(late_str).lstrip("vV").split(".")[0]
            if int(latest) >= 2000:
                return False
            return int(latest) > int(installed)
        except (ValueError, IndexError):
            return False

    @staticmethod
    def _is_prerelease(version: str) -> bool:
        if not version or str(version) in ("?", ""):
            return False
        try:
            ver = AwesomeVersion(str(version))
            if ver.modifier is not None or ver.modifier_type is not None:
                return True
        except Exception:
            pass
        return bool(_PRERELEASE_RE.search(str(version)))

    # ------------------------------------------------------------------
    # Listener pattern
    # ------------------------------------------------------------------

    def async_add_listener(self, callback) -> callable:
        self._listeners.append(callback)

        def _remove():
            self._listeners.remove(callback)

        return _remove

    def _notify_listeners(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Auto Updater: listener error — %s", exc)
