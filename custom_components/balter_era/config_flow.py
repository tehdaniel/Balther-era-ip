"""Config flow for the Balter ERA IP integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from . import create_client
from .const import CONF_CLOUD_RING, CONF_RING_ENTITY, DOMAIN
from .pybalter import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    BalterAuthError,
    BalterConnectionError,
    BalterError,
)

_LOGGER = logging.getLogger(__name__)

RING_ENTITY_DOMAINS = ["binary_sensor", "event", "input_boolean", "switch"]
RING_ENTITY_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=RING_ENTITY_DOMAINS)
)
USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_RING_ENTITY): RING_ENTITY_SELECTOR,
        vol.Optional(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
    }
)
REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


async def _validate(data: dict[str, Any]) -> tuple[str | None, dict[str, str]]:
    """Try to log in; return (title, errors)."""
    try:
        devices = await create_client(data).async_validate()
    except BalterAuthError:
        return None, {"base": "invalid_auth"}
    except BalterConnectionError:
        return None, {"base": "cannot_connect"}
    except BalterError:
        _LOGGER.exception("Unexpected protocol error during login")
        return None, {"base": "unknown"}
    if not devices:
        return None, {"base": "no_devices"}
    return f"Balter ERA ({devices[0].name})", {}


class BalterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Balter ERA IP."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> BalterOptionsFlow:
        """Options: doorbell source."""
        return BalterOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the Balter account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME].strip().lower())
            self._abort_if_unique_id_configured()
            data = dict(user_input)
            options = {}
            if ring_entity := data.pop(CONF_RING_ENTITY, None):
                options[CONF_RING_ENTITY] = ring_entity
            title, errors = await _validate(data)
            if title:
                return self.async_create_entry(title=title, data=data, options=options)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start re-authentication after the password was rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the new password."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            title, errors = await _validate(data)
            if title:
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={CONF_USERNAME: entry.data[CONF_USERNAME]},
        )


class BalterOptionsFlow(OptionsFlow):
    """Choose where doorbell rings come from."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the options form."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        schema = vol.Schema(
            {
                vol.Optional(CONF_RING_ENTITY): RING_ENTITY_SELECTOR,
                vol.Optional(CONF_CLOUD_RING, default=False): bool,
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, dict(self.config_entry.options)
            ),
        )
