"""HA Auto Updater — automatically installs all available HA updates."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    DATA_COORDINATOR,
    DEFAULT_SNOOZE_DAYS,
    DOMAIN,
    PLATFORMS,
    SERVICE_CLEAR_SNOOZE,
    SERVICE_RUN_UPDATES,
    SERVICE_SNOOZE_UPDATE,
)
from .coordinator import AutoUpdaterCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    coordinator = AutoUpdaterCoordinator(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = {DATA_COORDINATOR: coordinator}

    await coordinator.async_setup()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # React to options changes without a full entry reload
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    # Manual trigger service
    async def _handle_run_updates(call: ServiceCall) -> None:  # noqa: ARG001
        await coordinator.async_run_updates()

    async def _handle_snooze_update(call: ServiceCall) -> None:
        await coordinator.async_snooze_update(
            call.data["entity_id"],
            int(call.data.get("days", DEFAULT_SNOOZE_DAYS)),
        )

    async def _handle_clear_snooze(call: ServiceCall) -> None:
        await coordinator.async_clear_snooze(call.data.get("entity_id"))

    hass.services.async_register(DOMAIN, SERVICE_RUN_UPDATES, _handle_run_updates)
    hass.services.async_register(
        DOMAIN,
        SERVICE_SNOOZE_UPDATE,
        _handle_snooze_update,
        schema=vol.Schema({
            vol.Required("entity_id"): cv.entity_id,
            vol.Optional("days", default=DEFAULT_SNOOZE_DAYS): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=365)
            ),
        }),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_SNOOZE,
        _handle_clear_snooze,
        schema=vol.Schema({vol.Optional("entity_id"): cv.entity_id}),
    )

    def _remove_services() -> None:
        hass.services.async_remove(DOMAIN, SERVICE_RUN_UPDATES)
        hass.services.async_remove(DOMAIN, SERVICE_SNOOZE_UPDATE)
        hass.services.async_remove(DOMAIN, SERVICE_CLEAR_SNOOZE)

    entry.async_on_unload(_remove_services)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator: AutoUpdaterCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    await coordinator.async_unload()

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Respond to options changes without a full entry reload.

    Switches already handle their own side-effects in _on_changed.
    This listener handles schedule changes made via the config flow dialog.
    """
    coordinator: AutoUpdaterCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    coordinator._apply_log_level()
    await coordinator._async_reschedule()
    await coordinator._async_scan_pending(None)
