"""Config flow and options flow for HA Auto Updater."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    CONF_BACKUP_KEEP_DAYS,
    CONF_DAY_OF_WEEK,
    CONF_ENABLED,
    CONF_EXCLUDED_ENTITIES,
    CONF_FREQUENCY,
    CONF_INCLUDE_MAJOR,
    CONF_MAX_UPDATES_PER_RUN,
    CONF_NOTIFY_ON_NEW_UPDATES,
    CONF_NOTIFY_SERVICE,
    CONF_PRE_NOTIFY_DELAY,
    CONF_RETRY_DELAY,
    CONF_STAGGER_DELAY,
    CONF_TIME_OF_DAY,
    CONF_WEEKLY_DIGEST,
    DAYS_OF_WEEK,
    DEFAULT_BACKUP_KEEP_DAYS,
    DEFAULT_DAY_OF_WEEK,
    DEFAULT_ENABLED,
    DEFAULT_FREQUENCY,
    DEFAULT_INCLUDE_MAJOR,
    DEFAULT_MAX_UPDATES_PER_RUN,
    DEFAULT_NOTIFY_ON_NEW_UPDATES,
    DEFAULT_NOTIFY_SERVICE,
    DEFAULT_PRE_NOTIFY_DELAY,
    DEFAULT_RETRY_DELAY,
    DEFAULT_STAGGER_DELAY,
    DEFAULT_TIME_OF_DAY,
    DEFAULT_WEEKLY_DIGEST,
    DOMAIN,
    FREQUENCY_OPTIONS,
)


def _build_schema(options: dict, update_entity_map: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_ENABLED,
                default=options.get(CONF_ENABLED, DEFAULT_ENABLED),
            ): bool,
            vol.Required(
                CONF_FREQUENCY,
                default=options.get(CONF_FREQUENCY, DEFAULT_FREQUENCY),
            ): vol.In(FREQUENCY_OPTIONS),
            vol.Required(
                CONF_TIME_OF_DAY,
                default=options.get(CONF_TIME_OF_DAY, DEFAULT_TIME_OF_DAY),
            ): str,
            vol.Optional(
                CONF_DAY_OF_WEEK,
                default=options.get(CONF_DAY_OF_WEEK, DEFAULT_DAY_OF_WEEK),
            ): vol.In(list(DAYS_OF_WEEK.keys())),
            vol.Required(
                CONF_PRE_NOTIFY_DELAY,
                default=options.get(CONF_PRE_NOTIFY_DELAY, DEFAULT_PRE_NOTIFY_DELAY),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=60, step=1,
                    unit_of_measurement="min",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_STAGGER_DELAY,
                default=options.get(CONF_STAGGER_DELAY, DEFAULT_STAGGER_DELAY),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=300, step=1,
                    unit_of_measurement="sec",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_RETRY_DELAY,
                default=options.get(CONF_RETRY_DELAY, DEFAULT_RETRY_DELAY),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=300, step=10,
                    unit_of_measurement="sec",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_INCLUDE_MAJOR,
                default=options.get(CONF_INCLUDE_MAJOR, DEFAULT_INCLUDE_MAJOR),
            ): bool,
            vol.Required(
                CONF_MAX_UPDATES_PER_RUN,
                default=options.get(CONF_MAX_UPDATES_PER_RUN, DEFAULT_MAX_UPDATES_PER_RUN),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=50, step=1,
                    unit_of_measurement="updates",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_BACKUP_KEEP_DAYS,
                default=options.get(CONF_BACKUP_KEEP_DAYS, DEFAULT_BACKUP_KEEP_DAYS),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=90, step=1,
                    unit_of_measurement="days",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_NOTIFY_SERVICE,
                default=options.get(CONF_NOTIFY_SERVICE, DEFAULT_NOTIFY_SERVICE),
            ): str,
            vol.Required(
                CONF_WEEKLY_DIGEST,
                default=options.get(CONF_WEEKLY_DIGEST, DEFAULT_WEEKLY_DIGEST),
            ): bool,
            vol.Required(
                CONF_NOTIFY_ON_NEW_UPDATES,
                default=options.get(CONF_NOTIFY_ON_NEW_UPDATES, DEFAULT_NOTIFY_ON_NEW_UPDATES),
            ): bool,
            vol.Optional(
                CONF_EXCLUDED_ENTITIES,
                default=options.get(CONF_EXCLUDED_ENTITIES, []),
            ): cv.multi_select(update_entity_map),
        }
    )


class AutoUpdaterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial setup UI."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(title="HA Auto Updater", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=_build_schema({}, self._get_update_entity_map()),
        )

    def _get_update_entity_map(self) -> dict:
        return {
            e.entity_id: e.attributes.get("title", e.entity_id)
            for e in self.hass.states.async_all("update")
        }

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return AutoUpdaterOptionsFlow()


class AutoUpdaterOptionsFlow(config_entries.OptionsFlow):
    """Handle the settings / configure UI."""

    async def async_step_init(self, user_input: dict | None = None):
        current = {**self.config_entry.data, **self.config_entry.options}

        if user_input is not None:
            return self.async_create_entry(title="", data={**current, **user_input})

        return self.async_show_form(
            step_id="init",
            data_schema=_build_schema(current, self._get_update_entity_map()),
        )

    def _get_update_entity_map(self) -> dict:
        return {
            e.entity_id: e.attributes.get("title", e.entity_id)
            for e in self.hass.states.async_all("update")
        }
