"""HA Auto Updater — automatically installs all available HA updates."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import (
    DATA_COORDINATOR,
    DEFAULT_SNOOZE_DAYS,
    DOMAIN,
    PLATFORMS,
    SERVICE_CLEAR_SKIPPED,
    SERVICE_CLEAR_SNOOZE,
    SERVICE_DRY_RUN,
    SERVICE_INSTALL_SINGLE,
    SERVICE_RUN_UPDATES,
    SERVICE_SKIP_VERSION,
    SERVICE_SNOOZE_UPDATE,
)
from .coordinator import AutoUpdaterCoordinator

_LOGGER = logging.getLogger(__name__)

# No YAML configuration: the integration is set up from config entries only.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    coordinator = AutoUpdaterCoordinator(hass, entry)
    integration = await async_get_integration(hass, DOMAIN)
    coordinator.version = str(integration.version) if integration.version else None
    hass.data[DOMAIN][entry.entry_id] = {DATA_COORDINATOR: coordinator}

    await coordinator.async_setup()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # React to options changes without a full entry reload
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    # Manual trigger service
    async def _handle_run_updates(call: ServiceCall) -> None:
        # A service call is an explicit request, so it ignores blocking entities
        # unless the caller (e.g. a scheduling automation) opts in.
        await coordinator.async_run_updates(
            manual=not call.data.get("respect_blocking_entities", False)
        )

    async def _handle_dry_run(call: ServiceCall) -> ServiceResponse:
        report = await coordinator.async_dry_run()
        if call.return_response:
            return report
        coordinator.send_dry_run_notification(report)
        return None

    async def _handle_install_single(call: ServiceCall) -> None:
        await coordinator.async_install_single(call.data["entity_id"])

    async def _handle_snooze_update(call: ServiceCall) -> None:
        await coordinator.async_snooze_update(
            call.data["entity_id"],
            int(call.data.get("days", DEFAULT_SNOOZE_DAYS)),
        )

    async def _handle_clear_snooze(call: ServiceCall) -> None:
        await coordinator.async_clear_snooze(call.data.get("entity_id"))

    async def _handle_skip_version(call: ServiceCall) -> None:
        await coordinator.async_skip_version(call.data["entity_id"])

    async def _handle_clear_skipped(call: ServiceCall) -> None:
        await coordinator.async_clear_skipped_version(call.data.get("entity_id"))

    hass.services.async_register(
        DOMAIN,
        SERVICE_RUN_UPDATES,
        _handle_run_updates,
        schema=vol.Schema({
            vol.Optional("respect_blocking_entities", default=False): cv.boolean,
        }),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DRY_RUN,
        _handle_dry_run,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_INSTALL_SINGLE,
        _handle_install_single,
        schema=vol.Schema({vol.Required("entity_id"): cv.entity_domain("update")}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SNOOZE_UPDATE,
        _handle_snooze_update,
        schema=vol.Schema({
            vol.Required("entity_id"): cv.entity_domain("update"),
            vol.Optional("days", default=DEFAULT_SNOOZE_DAYS): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=365)
            ),
        }),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_SNOOZE,
        _handle_clear_snooze,
        schema=vol.Schema({vol.Optional("entity_id"): cv.entity_domain("update")}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SKIP_VERSION,
        _handle_skip_version,
        schema=vol.Schema({vol.Required("entity_id"): cv.entity_domain("update")}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_SKIPPED,
        _handle_clear_skipped,
        schema=vol.Schema({vol.Optional("entity_id"): cv.entity_domain("update")}),
    )

    def _remove_services() -> None:
        hass.services.async_remove(DOMAIN, SERVICE_RUN_UPDATES)
        hass.services.async_remove(DOMAIN, SERVICE_DRY_RUN)
        hass.services.async_remove(DOMAIN, SERVICE_INSTALL_SINGLE)
        hass.services.async_remove(DOMAIN, SERVICE_SNOOZE_UPDATE)
        hass.services.async_remove(DOMAIN, SERVICE_CLEAR_SNOOZE)
        hass.services.async_remove(DOMAIN, SERVICE_SKIP_VERSION)
        hass.services.async_remove(DOMAIN, SERVICE_CLEAR_SKIPPED)

    entry.async_on_unload(_remove_services)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator: AutoUpdaterCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]

    # Platforms first: if they refuse to unload, the entry stays loaded and
    # its coordinator must keep its timers.
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await coordinator.async_unload()
        hass.data[DOMAIN].pop(entry.entry_id)

    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Respond to options changes without a full entry reload.

    Switches already handle their own side-effects in _on_changed.
    This listener handles schedule changes made via the config flow dialog.
    """
    coordinator: AutoUpdaterCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    coordinator._apply_log_level()
    if coordinator.enabled and coordinator.unloaded:
        # Turned back on from the Configure dialog after the master switch had
        # unloaded the scheduler: restore the timer, scans and notification buttons.
        await coordinator.async_setup()
        return
    await coordinator._async_reschedule()
    await coordinator._async_scan_pending(None)
