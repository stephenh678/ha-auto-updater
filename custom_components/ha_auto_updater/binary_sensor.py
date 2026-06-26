"""Binary sensor entities for HA Auto Updater."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import AutoUpdaterCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AutoUpdaterCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities([UpdatesAvailableBinarySensor(coordinator, entry)])


class UpdatesAvailableBinarySensor(BinarySensorEntity):
    """On when one or more updates are currently pending.

    Simpler for dashboard badges and automations than comparing the
    pending-count sensor against zero.
    """

    _attr_should_poll = False
    _attr_device_class = BinarySensorDeviceClass.UPDATE
    _attr_name = "Auto Updater Updates Available"

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_updates_available"

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

    @property
    def is_on(self) -> bool:
        return self._coordinator.pending_count > 0
