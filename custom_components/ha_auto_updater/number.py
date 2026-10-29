"""Number entities for HA Auto Updater."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_MIN_RELEASE_AGE_DAYS,
    DATA_COORDINATOR,
    DOMAIN,
    MAX_RELEASE_AGE_DAYS,
)
from .coordinator import AutoUpdaterCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AutoUpdaterCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities([ReleaseCooldownNumber(coordinator, entry)])


class ReleaseCooldownNumber(NumberEntity):
    """Days a new version must have been available before it installs (0 = off)."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Release cooldown"
    _attr_icon = "mdi:timer-sand"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_RELEASE_AGE_DAYS
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.DAYS

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_release_cooldown"

    async def async_added_to_hass(self) -> None:
        # Options saved from the Configure dialog trigger a rescan, which
        # notifies listeners, so the device page picks up that value too.
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def device_info(self) -> dict:
        return self._coordinator.device_info

    @property
    def native_value(self) -> float:
        return float(self._coordinator._min_release_age_days())

    async def async_set_native_value(self, value: float) -> None:
        # Saving options fires the update listener, which rescans pending
        # updates so the cooling_down list reflects the new value right away.
        new_options = {**self._entry.options, CONF_MIN_RELEASE_AGE_DAYS: int(value)}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        self.async_write_ha_state()
