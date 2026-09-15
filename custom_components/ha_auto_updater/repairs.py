"""Repairs flows for HA Auto Updater."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant

from .const import DATA_COORDINATOR, DOMAIN, ISSUE_QUARANTINED_PREFIX


class ClearQuarantineRepairFlow(RepairsFlow):
    """Clear an update's auto-quarantine so the next run tries it again."""

    def __init__(self, data: dict) -> None:
        self._data = {k: str(v) for k, v in (data or {}).items() if v is not None}

    async def async_step_init(self, user_input: dict | None = None):
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict | None = None):
        if user_input is not None:
            entity_id = self._data.get("entity_id", "")
            for entry_data in (self.hass.data.get(DOMAIN) or {}).values():
                coordinator = entry_data.get(DATA_COORDINATOR) if isinstance(entry_data, dict) else None
                if coordinator is not None:
                    await coordinator.async_clear_snooze(entity_id)
            return self.async_create_entry(data={})

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "title": self._data.get("title", self._data.get("entity_id", "")),
                "entity_id": self._data.get("entity_id", ""),
                "failures": self._data.get("failures", ""),
                "days": self._data.get("days", ""),
            },
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the fix flow for a Repairs issue raised by this integration."""
    if issue_id.startswith(ISSUE_QUARANTINED_PREFIX):
        details = dict(data or {})
        details.setdefault("entity_id", issue_id[len(ISSUE_QUARANTINED_PREFIX):])
        return ClearQuarantineRepairFlow(details)
    return ConfirmRepairFlow()
