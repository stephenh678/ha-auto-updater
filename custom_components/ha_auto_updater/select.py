"""Select entity for HA Auto Updater — scheduled run time picker."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_TIME_OF_DAY, DATA_COORDINATOR, DEFAULT_TIME_OF_DAY, DOMAIN
from .coordinator import AutoUpdaterCoordinator

# 24 hourly options in 12-hour AM/PM format, stored internally as "HH:00"
_HOUR_OPTIONS: list[str] = [
    "12:00 AM", "1:00 AM",  "2:00 AM",  "3:00 AM",
    "4:00 AM",  "5:00 AM",  "6:00 AM",  "7:00 AM",
    "8:00 AM",  "9:00 AM",  "10:00 AM", "11:00 AM",
    "12:00 PM", "1:00 PM",  "2:00 PM",  "3:00 PM",
    "4:00 PM",  "5:00 PM",  "6:00 PM",  "7:00 PM",
    "8:00 PM",  "9:00 PM",  "10:00 PM", "11:00 PM",
]


def _label_to_24h(label: str) -> str:
    """Convert '2:00 AM' → '02:00', '12:00 PM' → '12:00', etc."""
    parts = label.split()          # ['2:00', 'AM']
    h, _ = parts[0].split(":")
    hour = int(h)
    if parts[1] == "AM":
        hour = 0 if hour == 12 else hour
    else:
        hour = 12 if hour == 12 else hour + 12
    return f"{hour:02d}:00"


def _24h_to_label(time_str: str) -> str:
    """Convert '02:00' → '2:00 AM', '12:00' → '12:00 PM', etc."""
    try:
        hour = int(time_str.split(":")[0])
    except (ValueError, IndexError):
        hour = 2
    if hour == 0:
        return "12:00 AM"
    if hour < 12:
        return f"{hour}:00 AM"
    if hour == 12:
        return "12:00 PM"
    return f"{hour - 12}:00 PM"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AutoUpdaterCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities([RunTimeSelectEntity(coordinator, entry)])


class RunTimeSelectEntity(SelectEntity):
    """Dropdown to pick the hour the scheduled update run fires each day.

    Selecting a new time saves it immediately and reschedules the next run
    without needing to open the Configure dialog.
    """

    _attr_should_poll = False
    _attr_icon = "mdi:clock-outline"
    _attr_name = "Run Time"
    _attr_options = _HOUR_OPTIONS

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_run_time_select"

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
    def current_option(self) -> str:
        time_str = self._coordinator.options.get(CONF_TIME_OF_DAY, DEFAULT_TIME_OF_DAY)
        return _24h_to_label(time_str)

    async def async_select_option(self, option: str) -> None:
        """Save the selected time and reschedule the next run immediately."""
        time_str = _label_to_24h(option)
        new_options = {**self._entry.options, CONF_TIME_OF_DAY: time_str}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        await self._coordinator._async_reschedule()
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )
