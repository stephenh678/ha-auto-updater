"""Switch entities for HA Auto Updater."""
from __future__ import annotations

from homeassistant.components.persistent_notification import async_create
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_AUTO_RESTART,
    CONF_BACKUP_BEFORE_UPDATE,
    CONF_BACKUP_CLEANUP,
    CONF_BACKUP_KEEP_DAYS,
    CONF_DEBUG,
    CONF_ENABLED,
    CONF_NOTIFY_FAILURE,
    CONF_NOTIFY_ON_NEW_UPDATES,
    CONF_NOTIFY_SUCCESS,
    CONF_SKIP_BETA,
    CONF_WEEKLY_DIGEST,
    DATA_COORDINATOR,
    DEFAULT_AUTO_RESTART,
    DEFAULT_BACKUP_BEFORE_UPDATE,
    DEFAULT_BACKUP_CLEANUP,
    DEFAULT_BACKUP_KEEP_DAYS,
    DEFAULT_DEBUG,
    DEFAULT_ENABLED,
    DEFAULT_NOTIFY_FAILURE,
    DEFAULT_NOTIFY_ON_NEW_UPDATES,
    DEFAULT_NOTIFY_SUCCESS,
    DEFAULT_SKIP_BETA,
    DEFAULT_WEEKLY_DIGEST,
    DOMAIN,
)
from .coordinator import AutoUpdaterCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AutoUpdaterCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(
        [
            AutoUpdaterSwitch(coordinator, entry),
            AutoRestartSwitch(coordinator, entry),
            BackupSwitch(coordinator, entry),
            BackupCleanupSwitch(coordinator, entry),
            SkipBetaSwitch(coordinator, entry),
            DebugLoggingSwitch(coordinator, entry),
            NotifySuccessSwitch(coordinator, entry),
            NotifyFailureSwitch(coordinator, entry),
            WeeklyDigestSwitch(coordinator, entry),
            NotifyOnNewUpdatesSwitch(coordinator, entry),
        ]
    )


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class _FeatureSwitch(SwitchEntity):
    """Base for all Auto Updater feature toggle switches."""

    _attr_should_poll = False
    _conf_key: str
    _default: bool

    def __init__(
        self,
        coordinator: AutoUpdaterCoordinator,
        entry: ConfigEntry,
        name: str,
        unique_suffix: str,
        icon: str,
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}"
        self._attr_icon = icon

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "HA Auto Updater",
            "manufacturer": "Custom",
            "model": "Auto Updater",
            "entry_type": "service",
        }

    @property
    def is_on(self) -> bool:
        return self._coordinator.options.get(self._conf_key, self._default)

    async def async_turn_on(self, **kwargs) -> None:
        await self._set_value(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set_value(False)

    async def _set_value(self, value: bool) -> None:
        new_options = {**self._entry.options, self._conf_key: value}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        await self._on_changed(value)
        self.async_write_ha_state()

    async def _on_changed(self, value: bool) -> None:  # noqa: ARG002
        """Override to add side effects when the value changes."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _notify(hass, notification_id: str, title: str, message: str) -> None:
    async_create(hass, message, title=f"Auto Updater — {title}", notification_id=notification_id)

def _dismiss(hass, notification_id: str) -> None:
    hass.async_create_task(
        hass.services.async_call(
            "persistent_notification",
            "dismiss",
            {"notification_id": notification_id},
        )
    )


# ---------------------------------------------------------------------------
# Concrete switches
# ---------------------------------------------------------------------------

class AutoUpdaterSwitch(_FeatureSwitch):
    """Master on/off — also starts/stops the scheduler."""

    _conf_key = CONF_ENABLED
    _default = DEFAULT_ENABLED

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "Auto Updater", "switch", "mdi:update")

    async def _on_changed(self, value: bool) -> None:
        if value:
            await self._coordinator.async_setup()
            _dismiss(self.hass, "ha_auto_updater_disabled")
        else:
            await self._coordinator.async_unload()
            _notify(
                self.hass,
                "ha_auto_updater_disabled",
                "⚠️ Auto Updates Disabled",
                "Automatic updates have been **turned off**. "
                "No updates will install until you re-enable Auto Updater.",
            )


class BackupSwitch(_FeatureSwitch):
    """Create a full backup before installing updates."""

    _conf_key = CONF_BACKUP_BEFORE_UPDATE
    _default = DEFAULT_BACKUP_BEFORE_UPDATE

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "Backup Before Updating", "backup_switch", "mdi:backup-restore")

    async def _on_changed(self, value: bool) -> None:
        if value:
            _dismiss(self.hass, "ha_auto_updater_no_backup")
        else:
            _notify(
                self.hass,
                "ha_auto_updater_no_backup",
                "⚠️ Backup Disabled",
                "Backup before updating is **turned off**. "
                "Updates will install without creating a backup first.",
            )


class BackupCleanupSwitch(_FeatureSwitch):
    """Auto-purge old pre-update backups created by this integration."""

    _conf_key = CONF_BACKUP_CLEANUP
    _default = DEFAULT_BACKUP_CLEANUP

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "Auto-Purge Old Backups", "backup_cleanup_switch", "mdi:delete-clock")

    async def _on_changed(self, value: bool) -> None:
        if value:
            days = self._coordinator.options.get(CONF_BACKUP_KEEP_DAYS, DEFAULT_BACKUP_KEEP_DAYS)
            _notify(
                self.hass,
                "ha_auto_updater_backup_cleanup_on",
                "🧹 Backup Auto-Purge Enabled",
                "Pre-update backups created by Auto Updater older than **{} day{}** "
                "will be deleted automatically after each new backup. "
                "Only backups this integration created are touched — your manual "
                "backups are never removed.".format(days, "s" if days != 1 else ""),
            )
        else:
            _dismiss(self.hass, "ha_auto_updater_backup_cleanup_on")


class AutoRestartSwitch(_FeatureSwitch):
    """Restart HA after updates that require it (HACS / custom integrations)."""

    _conf_key = CONF_AUTO_RESTART
    _default = DEFAULT_AUTO_RESTART

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "Restart After Updates", "auto_restart_switch", "mdi:restart")

    async def _on_changed(self, value: bool) -> None:
        if value:
            _notify(
                self.hass,
                "ha_auto_updater_auto_restart",
                "🔄 Auto Restart Enabled",
                "HA will **automatically restart** after installing updates that require it "
                "(HACS integrations, custom components). "
                "Make sure nothing time-sensitive is running when updates are scheduled.",
            )
        else:
            _dismiss(self.hass, "ha_auto_updater_auto_restart")


class SkipBetaSwitch(_FeatureSwitch):
    """Skip beta and release-candidate versions."""

    _conf_key = CONF_SKIP_BETA
    _default = DEFAULT_SKIP_BETA

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "Skip Beta/RC Versions", "skip_beta_switch", "mdi:flask-off")


class DebugLoggingSwitch(_FeatureSwitch):
    """Enable verbose debug logging."""

    _conf_key = CONF_DEBUG
    _default = DEFAULT_DEBUG

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "Debug Logging", "debug_switch", "mdi:bug")

    async def _on_changed(self, value: bool) -> None:
        self._coordinator._apply_log_level()
        if value:
            _notify(
                self.hass,
                "ha_auto_updater_debug",
                "🐛 Debug Logging Enabled",
                "Verbose debug logging is **on**. Check **Settings → System → Logs** "
                "for detailed output. Turn off when done to reduce log noise.",
            )
        else:
            _dismiss(self.hass, "ha_auto_updater_debug")


class NotifySuccessSwitch(_FeatureSwitch):
    """Send a notification when updates install successfully."""

    _conf_key = CONF_NOTIFY_SUCCESS
    _default = DEFAULT_NOTIFY_SUCCESS

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "Notify on Success", "notify_success_switch", "mdi:bell-check")


class NotifyFailureSwitch(_FeatureSwitch):
    """Send a notification when one or more updates fail."""

    _conf_key = CONF_NOTIFY_FAILURE
    _default = DEFAULT_NOTIFY_FAILURE

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "Notify on Failure", "notify_failure_switch", "mdi:bell-alert")


class WeeklyDigestSwitch(_FeatureSwitch):
    """Send a weekly summary of update activity."""

    _conf_key = CONF_WEEKLY_DIGEST
    _default = DEFAULT_WEEKLY_DIGEST

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "Weekly Digest", "weekly_digest_switch", "mdi:calendar-week")

    async def _on_changed(self, value: bool) -> None:
        if value:
            _notify(
                self.hass,
                "ha_auto_updater_weekly_digest_on",
                "📅 Weekly Digest Enabled",
                "A weekly summary of update activity will be sent every 7 days.",
            )
        else:
            _dismiss(self.hass, "ha_auto_updater_weekly_digest_on")


class NotifyOnNewUpdatesSwitch(_FeatureSwitch):
    """Send a push notification when new updates are detected during a background scan."""

    _conf_key = CONF_NOTIFY_ON_NEW_UPDATES
    _default = DEFAULT_NOTIFY_ON_NEW_UPDATES

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "Notify on New Updates", "notify_new_updates_switch", "mdi:bell-plus")
