"""Config flow for Niagara BMS integration."""

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_ADDON_URL,
    CONF_AREA_DEPTH,
    CONF_DEVICE_NAME,
    DEFAULT_ADDON_URL,
    DEFAULT_AREA_DEPTH,
    DEFAULT_DEVICE_NAME,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class NiagaraConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Niagara BMS."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            addon_url = user_input[CONF_ADDON_URL].rstrip("/")
            await self.async_set_unique_id(addon_url)
            self._abort_if_unique_id_configured()

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{addon_url}/api/integration/points",
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        resp.raise_for_status()
                        data = await resp.json()
                        point_count = data.get("enabled", 0)
                        total = data.get("total", 0)
            except Exception:
                _LOGGER.exception("Failed to connect to add-on")
                errors["base"] = "cannot_connect"

            if not errors:
                return self.async_create_entry(
                    title=f"Niagara BMS ({point_count} of {total} points enabled)",
                    data={
                        CONF_ADDON_URL: addon_url,
                        CONF_DEVICE_NAME: user_input.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME),
                        CONF_AREA_DEPTH: user_input.get(CONF_AREA_DEPTH, DEFAULT_AREA_DEPTH),
                        "scan_interval": user_input.get("scan_interval", DEFAULT_POLL_INTERVAL),
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_ADDON_URL, default=DEFAULT_ADDON_URL): str,
                vol.Optional(CONF_DEVICE_NAME, default=DEFAULT_DEVICE_NAME): str,
                vol.Optional("scan_interval", default=DEFAULT_POLL_INTERVAL): int,
                vol.Optional(CONF_AREA_DEPTH, default=DEFAULT_AREA_DEPTH): int,
            }),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return NiagaraOptionsFlow(config_entry)


class NiagaraOptionsFlow(OptionsFlow):
    """Handle options for Niagara BMS."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self._config_entry.data, **self._config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    "scan_interval",
                    default=current.get("scan_interval", DEFAULT_POLL_INTERVAL),
                ): int,
                vol.Optional(
                    CONF_DEVICE_NAME,
                    default=current.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME),
                ): str,
                vol.Optional(
                    CONF_AREA_DEPTH,
                    default=current.get(CONF_AREA_DEPTH, DEFAULT_AREA_DEPTH),
                ): int,
            }),
        )
