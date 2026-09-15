"""Diagnostics support for HA Auto Updater."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_NOTIFY_SERVICE, DATA_COORDINATOR, DOMAIN
from .coordinator import AutoUpdaterCoordinator

TO_REDACT = {
    CONF_NOTIFY_SERVICE,
    "notify_service",
    "password",
    "token",
    "secret",
    "api_key",
    "access_token",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: AutoUpdaterCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]

    # Gather disk space metric safely
    space_ok, free_gb = coordinator._check_disk_space(0.0)

    diag_data = {
        "entry_id": entry.entry_id,
        "domain": DOMAIN,
        "version": entry.version,
        "options": dict(entry.options),
        "coordinator_state": {
            "is_running": coordinator._is_running,
            "last_run": coordinator.last_run.isoformat() if coordinator.last_run else None,
            "last_run_status": coordinator.last_run_status,
            "last_run_count": coordinator.last_run_count,
            "last_run_failed": coordinator.last_run_failed,
            "last_run_duration": coordinator.last_run_duration,
            "pending_count": coordinator.pending_count,
            "pending_updates": coordinator.pending_updates,
            "failed_updates": coordinator.failed_updates,
            "snoozed": coordinator._snoozed,
            "snoozed_summary": coordinator.snoozed_summary(),
            "tracked_backups": coordinator._tracked_backups,
            "pending_verification": coordinator._pending_verification,
            "deferred": coordinator._deferred,
            "next_run": coordinator.next_run.isoformat() if coordinator.next_run else None,
        },
        "system_info": {
            "free_disk_space_gb": free_gb,
            "disk_space_ok": space_ok,
            "safe_mode": getattr(hass.config, "safe_mode", False),
        },
        "history_summary": coordinator.history[-10:],
    }

    return async_redact_data(diag_data, TO_REDACT)
