"""Sensor entities for HA Auto Updater."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import AutoUpdaterCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AutoUpdaterCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(
        [
            PendingUpdatesSensor(coordinator, entry),
            LastRunSensor(coordinator, entry),
            LastRunCountSensor(coordinator, entry),
            LastRunStatusSensor(coordinator, entry),
            LastRunDurationSensor(coordinator, entry),
            UpdateHistorySensor(coordinator, entry),
            NextScheduledRunSensor(coordinator, entry),
            FailedUpdatesSensor(coordinator, entry),
        ]
    )


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class _BaseAutoUpdaterSensor(SensorEntity):
    _attr_should_poll = False

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._entry = entry

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "HA Auto Updater",
            "manufacturer": "Custom",
            "model": "Auto Updater",
            "entry_type": "service",
        }


# ---------------------------------------------------------------------------
# Sensors
# ---------------------------------------------------------------------------

class PendingUpdatesSensor(_BaseAutoUpdaterSensor):
    """Count of updates currently available, with full list in attributes."""

    _attr_icon = "mdi:update"
    _attr_native_unit_of_measurement = "updates"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_name = "Auto Updater Pending Updates"
        self._attr_unique_id = f"{entry.entry_id}_pending_updates"

    @property
    def native_value(self) -> int:
        return self._coordinator.pending_count

    @property
    def extra_state_attributes(self) -> dict:
        updates = self._coordinator.pending_updates
        return {
            "updates": [
                "{} ({} → {}) [{}]".format(
                    u["title"], u["installed_version"], u["latest_version"],
                    u.get("source", ""),
                )
                for u in updates
            ],
            "entity_ids": [u["entity_id"] for u in updates],
            "release_notes": [
                {"title": u["title"], "url": u.get("release_url")}
                for u in updates
                if u.get("release_url")
            ],
            "snoozed": self._coordinator.snoozed_summary(),
        }


class LastRunSensor(_BaseAutoUpdaterSensor):
    """Timestamp of the last update run."""

    _attr_icon = "mdi:clock-check-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_name = "Auto Updater Last Run"
        self._attr_unique_id = f"{entry.entry_id}_last_run"

    @property
    def native_value(self):
        return self._coordinator.last_run


class LastRunCountSensor(_BaseAutoUpdaterSensor):
    """Number of items successfully updated in the last run."""

    _attr_icon = "mdi:counter"
    _attr_native_unit_of_measurement = "updates"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_name = "Auto Updater Last Run Count"
        self._attr_unique_id = f"{entry.entry_id}_last_run_count"

    @property
    def native_value(self) -> int:
        return self._coordinator.last_run_count


class LastRunStatusSensor(_BaseAutoUpdaterSensor):
    """Human-readable status of the last update run.

    Values: Never run | Running | No updates | Success | Partial failure | All failed | Aborted
    Useful for automation triggers and dashboard conditionals.
    """

    _attr_icon = "mdi:check-circle-outline"

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_name = "Auto Updater Last Run Status"
        self._attr_unique_id = f"{entry.entry_id}_last_run_status"

    @property
    def native_value(self) -> str:
        return self._coordinator.last_run_status

    @property
    def icon(self) -> str:
        icons = {
            "Running": "mdi:sync",
            "Success": "mdi:check-circle-outline",
            "Partial failure": "mdi:alert-circle-outline",
            "All failed": "mdi:close-circle-outline",
            "Aborted": "mdi:cancel",
            "No updates": "mdi:check-circle-outline",
            "Never run": "mdi:clock-outline",
        }
        return icons.get(self._coordinator.last_run_status, "mdi:help-circle-outline")


class LastRunDurationSensor(_BaseAutoUpdaterSensor):
    """Duration of the last update run in seconds."""

    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = "s"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_name = "Auto Updater Last Run Duration"
        self._attr_unique_id = f"{entry.entry_id}_last_run_duration"

    @property
    def native_value(self) -> int:
        return self._coordinator.last_run_duration


class UpdateHistorySensor(_BaseAutoUpdaterSensor):
    """Total runs recorded, with last 10 in attributes."""

    _attr_icon = "mdi:history"
    _attr_native_unit_of_measurement = "runs"

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_name = "Auto Updater History"
        self._attr_unique_id = f"{entry.entry_id}_history"

    @property
    def native_value(self) -> int:
        return len(self._coordinator.history)

    @property
    def extra_state_attributes(self) -> dict:
        recent = list(reversed(self._coordinator.history[-10:]))
        return {
            "recent_runs": [
                {
                    "timestamp": e["timestamp"],
                    "updated": e.get("updated", []),
                    "failed": e.get("failed", []),
                    "total_updated": e.get("total_updated", 0),
                    "total_failed": e.get("total_failed", 0),
                    "note": e.get("note", ""),
                }
                for e in recent
            ]
        }


class NextScheduledRunSensor(_BaseAutoUpdaterSensor):
    """Timestamp of the next scheduled update check."""

    _attr_icon = "mdi:calendar-clock"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_name = "Auto Updater Next Run"
        self._attr_unique_id = f"{entry.entry_id}_next_run"

    @property
    def native_value(self):
        return self._coordinator.next_run


class FailedUpdatesSensor(_BaseAutoUpdaterSensor):
    """Count of updates that failed in the last run, with details in attributes."""

    _attr_icon = "mdi:alert-circle-outline"
    _attr_native_unit_of_measurement = "updates"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_name = "Auto Updater Failed Updates"
        self._attr_unique_id = f"{entry.entry_id}_failed_updates"

    @property
    def native_value(self) -> int:
        return len(self._coordinator.failed_updates)

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "failed": [
                {"title": u["title"], "entity_id": u["entity_id"]}
                for u in self._coordinator.failed_updates
            ]
        }
