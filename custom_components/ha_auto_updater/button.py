"""Button entity — manually trigger an update run."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import AutoUpdaterCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AutoUpdaterCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities([
        RunUpdatesButton(coordinator, entry),
        ScanUpdatesButton(coordinator, entry),
    ])


class _BaseButton(ButtonEntity):
    """Base class for Auto Updater buttons — provides shared device_info."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._entry = entry

    @property
    def device_info(self) -> dict:
        return self._coordinator.device_info


class RunUpdatesButton(_BaseButton):
    """Press to immediately check for and install all available updates."""

    _attr_icon = "mdi:refresh"
    _attr_name = "Run updates now"

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_run_now"

    async def async_press(self) -> None:
        await self._coordinator.async_run_updates()


class ScanUpdatesButton(_BaseButton):
    """Press to refresh the pending updates count without installing anything."""

    _attr_icon = "mdi:magnify-scan"
    _attr_name = "Scan for updates"

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_scan_now"

    async def async_press(self) -> None:
        await self._coordinator.async_scan_pending()


