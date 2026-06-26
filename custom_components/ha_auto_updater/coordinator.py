"""Auto Updater coordinator — scheduling, update execution, history, and debug."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta

from homeassistant.components.persistent_notification import async_create
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_point_in_time, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    BACKUP_STATE_FILE,
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
    CONF_NOTIFY_FAILURE,
    CONF_NOTIFY_ON_NEW_UPDATES,
    CONF_NOTIFY_SERVICE,
    CONF_NOTIFY_SUCCESS,
    CONF_PRE_NOTIFY_DELAY,
    CONF_RETRY_DELAY,
    CONF_SKIP_BETA,
    CONF_STAGGER_DELAY,
    CONF_TIME_OF_DAY,
    CONF_WEEKLY_DIGEST,
    DAYS_OF_WEEK,
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
    DEFAULT_WEEKLY_DIGEST,
    DIGEST_STATE_FILE,
    FAILURE_ESCALATION_THRESHOLD,
    FREQ_HOURLY,
    FREQ_WEEKLY,
    HISTORY_FILE,
    MAX_HISTORY_ENTRIES,
    SNOOZE_STATE_FILE,
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
    "update.home_assistant_core_update",
}


class AutoUpdaterCoordinator:
    """Manages scheduling, update execution, history, and debug logging."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._unsub_timer = None
        self._unsub_scan = None
        self._is_running = False
        self._listeners: list = []

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
        self._apply_log_level()
        await self._load_history()
        await self._load_digest_state()
        await self._load_snooze()
        await self._load_backup_state()
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
            hour, minute = map(int, time_str.split(":"))
        except (ValueError, AttributeError):
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
    # Shared update filter
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Background pending-update scan (read-only, no installs)
    # ------------------------------------------------------------------

    async def _async_scan_pending(self, _now) -> None:
        """Refresh pending_count and pending_updates without installing anything.

        Runs on startup and every 30 minutes so the sensor reflects the real-time
        state of update entities between scheduled install runs.
        """
        available = self._filter_available_updates(log_skips=False)

        self.pending_count = len(available)
        self.pending_updates = [
            {
                "title": e.attributes.get("title") or e.entity_id,
                "installed_version": e.attributes.get("installed_version", "?"),
                "latest_version": e.attributes.get("latest_version", "?"),
                "entity_id": e.entity_id,
                "source": self._get_update_source(e.entity_id),
                "release_url": e.attributes.get("release_url"),
                "release_summary": e.attributes.get("release_summary"),
            }
            for e in available
        ]
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

    async def async_run_updates(self) -> None:
        if self._is_running:
            _LOGGER.warning("Auto Updater: run already in progress — skipping.")
            return
        self._is_running = True
        self.last_run_status = "Running"
        self._notify_listeners()
        try:
            await self._async_run_updates_inner()
        finally:
            self._is_running = False

    async def _async_run_updates_inner(self) -> None:
        _LOGGER.info("Auto Updater: checking for available updates…")

        backup_enabled: bool = self.options.get(CONF_BACKUP_BEFORE_UPDATE, DEFAULT_BACKUP_BEFORE_UPDATE)
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

        # Apply max-updates-per-run cap (0 = unlimited)
        if max_updates > 0 and len(available) > max_updates:
            _LOGGER.info(
                "Auto Updater: capping run to %d of %d available update(s).",
                max_updates, len(available),
            )
            available = available[:max_updates]

        self.pending_count = len(available)
        self.pending_updates = [
            {
                "title": e.attributes.get("title") or e.entity_id,
                "installed_version": e.attributes.get("installed_version", "?"),
                "latest_version": e.attributes.get("latest_version", "?"),
                "entity_id": e.entity_id,
                "source": self._get_update_source(e.entity_id),
                "release_url": e.attributes.get("release_url"),
                "release_summary": e.attributes.get("release_summary"),
            }
            for e in available
        ]
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
            self._notify_listeners()
            return

        titles = [e.attributes.get("title", e.entity_id) for e in available]
        _LOGGER.info("Auto Updater: %d update(s) available — %s", len(available), titles)

        # --- 3. Backup ---
        if backup_enabled:
            _LOGGER.info("Auto Updater: creating backup before updates…")
            self._send_status_notification(
                "Creating backup…",
                "A backup is being created before installing updates.",
                notification_id="ha_auto_updater_backup_progress",
            )
            backup_ok = await self._create_backup()
            self._dismiss_notification("ha_auto_updater_backup_progress")
            if backup_ok:
                _LOGGER.info("Auto Updater: backup completed successfully.")
                self._send_status_notification(
                    "Backup complete",
                    "Backup created successfully. Installing updates now.",
                    notification_id="ha_auto_updater_backup_done",
                )
            else:
                _LOGGER.warning("Auto Updater: backup failed or unavailable, proceeding anyway.")

        # --- 4. Pre-update notification + delay ---
        if pre_notify_delay > 0:
            _LOGGER.info(
                "Auto Updater: sending pre-update notice, waiting %d min before installing…",
                pre_notify_delay,
            )
            self._send_pre_update_notification(titles, pre_notify_delay)
            await asyncio.sleep(pre_notify_delay * 60)

            # Abort if disabled during the wait
            if not self.enabled:
                _LOGGER.info("Auto Updater: disabled during pre-update wait — aborting.")
                self.last_run = dt_util.now()
                self.last_run_status = "Aborted"
                self._notify_listeners()
                return

        # --- 5. Install updates ---
        updated_items: list[dict] = []
        failed_names: list[str] = []
        restart_required_map: dict[str, bool] = {}  # title -> requires restart

        # Dismiss pre-update and backup notifications — installs are starting now
        self._dismiss_notification("ha_auto_updater_pre_update")
        self._dismiss_notification("ha_auto_updater_backup_done")

        for i, entity in enumerate(available):
            entity_id = entity.entity_id
            attrs = entity.attributes
            title = attrs.get("title") or entity_id
            installed = attrs.get("installed_version", "?")
            latest = attrs.get("latest_version", "?")
            release_url = attrs.get("release_url")
            # Capture restart_required before install while state is still "on"
            restart_required_map[title] = attrs.get("restart_required", True)

            # Stagger delay before each update (except the first)
            if i > 0 and stagger_delay > 0:
                _LOGGER.debug("Auto Updater: stagger — waiting %ds before next update…", stagger_delay)
                await asyncio.sleep(stagger_delay)

            # Re-check state — entity may have been updated since the initial scan
            current_state = self.hass.states.get(entity_id)
            if current_state is None or current_state.state != "on":
                _LOGGER.info("Auto Updater: skipping %s — already up to date.", title)
                continue

            # Supervisor and OS updates restart their own process during install,
            # so blocking=True will time out — use non-blocking for these.
            is_system_update = entity_id in _HA_SYSTEM_UPDATE_ENTITIES

            # Install with one retry on failure
            for attempt in range(2):
                try:
                    _LOGGER.debug(
                        "Auto Updater: calling update.install for %s (attempt %d, blocking=%s)",
                        title, attempt + 1, not is_system_update,
                    )
                    await asyncio.wait_for(
                        self.hass.services.async_call(
                            "update",
                            "install",
                            {"entity_id": entity_id},
                            blocking=not is_system_update,
                        ),
                        timeout=300,  # 5-minute per-update timeout
                    )
                    updated_items.append({
                        "title": title, "from": installed, "to": latest,
                        "release_url": release_url,
                    })
                    _LOGGER.info("Auto Updater: ✓ %s  %s → %s", title, installed, latest)
                    if is_system_update:
                        _LOGGER.info(
                            "Auto Updater: %s is a system-level update — install triggered "
                            "(non-blocking). HA may restart to complete it.",
                            title,
                        )
                    break
                except Exception as exc:  # noqa: BLE001
                    if attempt == 0:
                        _LOGGER.warning(
                            "Auto Updater: attempt 1 failed for %s — %s  Retrying in %ds…",
                            title, exc, retry_delay,
                        )
                        await asyncio.sleep(retry_delay)
                    else:
                        _LOGGER.error(
                            "Auto Updater: ✗ %s failed after retry — %s", title, exc
                        )
                        failed_names.append(title)

        # --- 6. Persist results ---
        run_end = dt_util.now()
        duration = int((run_end - run_start).total_seconds())
        self.last_run_duration = duration
        self.last_run = run_end
        self.last_run_count = len(updated_items)
        self.last_run_failed = len(failed_names)
        self.last_run_status = self._derive_status(len(updated_items), len(failed_names), "")
        self.pending_count = len(failed_names)
        self.pending_updates = [u for u in self.pending_updates if u["title"] in failed_names]
        self.failed_updates = [
            {
                "title": n,
                "entity_id": next(
                    (e.entity_id for e in available if e.attributes.get("title") == n), n
                ),
            }
            for n in failed_names
        ]

        await self._append_and_save_history({
            "timestamp": run_end.isoformat(),
            "updated": [
                "{} ({} → {})".format(u["title"], u["from"], u["to"]) for u in updated_items
            ],
            "failed": failed_names,
            "total_updated": len(updated_items),
            "total_failed": len(failed_names),
            "duration_seconds": duration,
        })

        self.hass.bus.async_fire(
            EVENT_RUN_COMPLETE,
            {
                "updated": [u["title"] for u in updated_items],
                "failed": failed_names,
                "total_updated": len(updated_items),
                "total_failed": len(failed_names),
                "duration_seconds": duration,
            },
        )
        self._notify_listeners()

        # --- 7. Notifications ---
        notify_success: bool = self.options.get(CONF_NOTIFY_SUCCESS, DEFAULT_NOTIFY_SUCCESS)
        notify_failure: bool = self.options.get(CONF_NOTIFY_FAILURE, DEFAULT_NOTIFY_FAILURE)

        if updated_items and notify_success:
            self._send_success_notification(updated_items)
            self._send_push_notification(
                "Updates Installed",
                "{} update(s) installed successfully.".format(len(updated_items)),
            )

        if failed_names and notify_failure:
            escalated = [
                n for n in failed_names
                if self._consecutive_failures(n) >= FAILURE_ESCALATION_THRESHOLD
            ]
            self._send_failure_notification(failed_names, escalated)
            push_msg = "{} update(s) failed: {}".format(len(failed_names), ", ".join(failed_names))
            if escalated:
                push_msg += "\n⚠️ Repeatedly failing ({}+ runs): {}".format(
                    FAILURE_ESCALATION_THRESHOLD, ", ".join(escalated)
                )
            self._send_push_notification("Update Failures", push_msg)

        # --- 8. Restart HA if enabled and any installed update requires it ---
        auto_restart: bool = self.options.get(CONF_AUTO_RESTART, DEFAULT_AUTO_RESTART)
        non_system_updates = [
            u for u in updated_items
            if next(
                (e.entity_id for e in available if (e.attributes.get("title") or e.entity_id) == u["title"]),
                None,
            ) not in _HA_SYSTEM_UPDATE_ENTITIES
        ]
        needs_restart = any(restart_required_map.get(u["title"], True) for u in non_system_updates)
        if auto_restart and non_system_updates and needs_restart:
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
    # Backup
    # ------------------------------------------------------------------

    async def _create_backup(self) -> bool:
        name = "pre_update_{}".format(dt_util.now().strftime("%Y%m%d_%H%M"))
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

    async def _call_backup_service(self, domain: str, service: str, data: dict):
        """Create a backup. Return its slug/id if the service can report one."""
        if self._service_supports_response(domain, service):
            resp = await asyncio.wait_for(
                self.hass.services.async_call(
                    domain, service, data, blocking=True, return_response=True
                ),
                timeout=600,  # backups can take up to 10 minutes
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
            timeout=600,
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
            if created is not None and created < cutoff and ref:
                if await self._delete_backup(b.get("domain", ""), ref):
                    purged += 1
                    continue  # drop from tracking
                # delete failed — keep tracking so we retry next time
            remaining.append(b)
        self._tracked_backups = remaining
        if purged:
            _LOGGER.info("Auto Updater: purged %d old pre-update backup(s).", purged)
            await self._save_backup_state()

    async def _delete_backup(self, domain: str, ref) -> bool:
        """Best-effort delete of a single backup by slug/id."""
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
                    failed_names = last.get("failed", [])
                    self.failed_updates = [
                        {"title": n, "entity_id": n} for n in failed_names
                    ]
                    # Restore last_run_status
                    self.last_run_status = self._derive_status(
                        self.last_run_count, self.last_run_failed, last.get("note", "")
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
        """Categorise an update entity as HA System, Add-on, HACS, or Custom."""
        if entity_id in _HA_SYSTEM_UPDATE_ENTITIES:
            return "HA System"
        registry = er.async_get(self.hass)
        entry = registry.async_get(entity_id)
        if entry is None:
            return "Custom"
        if entry.platform == "hassio":
            return "Add-on"
        if entry.platform == "hacs":
            return "HACS"
        return "Custom"

    # ------------------------------------------------------------------
    # Snooze (per-update temporary skip)
    # ------------------------------------------------------------------

    def _is_snoozed(self, entity_id: str) -> bool:
        until = self._snoozed.get(entity_id)
        if not until:
            return False
        dt = dt_util.parse_datetime(until)
        if dt is None or dt <= dt_util.now():
            self._snoozed.pop(entity_id, None)  # expired — lazy cleanup
            return False
        return True

    def snoozed_summary(self) -> list:
        """Active snoozes for sensor display."""
        now = dt_util.now()
        out = []
        for eid, until in self._snoozed.items():
            dt = dt_util.parse_datetime(until)
            if dt is not None and dt > now:
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

    def _consecutive_failures(self, title: str) -> int:
        """Count how many of the most recent runs failed this component in a row.

        Stops counting at the first run where the component updated successfully.
        Runs that did not involve the component at all are skipped.
        """
        count = 0
        for entry in reversed(self.history):
            if title in entry.get("failed", []):
                count += 1
            elif any(str(u).startswith(title) for u in entry.get("updated", [])):
                break
        return count

    @staticmethod
    def _derive_status(updated: int, failed: int, note: str) -> str:  # noqa: ARG004
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
        try:
            installed = str(attrs.get("installed_version", "0")).split(".")[0]
            latest = str(attrs.get("latest_version", "0")).split(".")[0]
            # Calendar versioning (major >= 2000 means it's a year, e.g. 2026.03.3).
            # Year-to-year increments are routine releases — never treat as major bump.
            if int(latest) >= 2000:
                return False
            return int(latest) > int(installed)
        except (ValueError, IndexError):
            return False

    @staticmethod
    def _is_prerelease(version: str) -> bool:
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
