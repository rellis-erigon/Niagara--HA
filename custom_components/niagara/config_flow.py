"""Config flow for Niagara BMS integration."""

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_AREA_DEPTH,
    CONF_DEVICE_DEPTH,
    CONF_DEVICE_NAME,
    CONF_POINT_FILTER,
    CONF_USE_HTTPS,
    CONF_VERIFY_SSL,
    DEFAULT_AREA_DEPTH,
    DEFAULT_DEVICE_DEPTH,
    DEFAULT_DEVICE_NAME,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    DOMAIN,
)
from .coordinator import ObixClient

_LOGGER = logging.getLogger(__name__)


class NiagaraConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Niagara BMS."""

    VERSION = 1

    def __init__(self) -> None:
        self._user_input: dict[str, Any] = {}
        self._point_count: int = 0

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(
                f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
            )
            self._abort_if_unique_id_configured()

            client = ObixClient(
                host=user_input[CONF_HOST],
                username=user_input[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
                port=user_input[CONF_PORT],
                use_https=user_input.get(CONF_USE_HTTPS, True),
                verify_ssl=user_input.get(CONF_VERIFY_SSL, False),
            )
            try:
                result = await self.hass.async_add_executor_job(client.test_connection)
                if not result:
                    errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Connection test failed")
                errors["base"] = "cannot_connect"

            if not errors:
                try:
                    points = await self.hass.async_add_executor_job(
                        client.discover_points
                    )
                    self._point_count = len(points)
                except Exception:
                    self._point_count = 0

                self._user_input = user_input
                return await self.async_step_discovery()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_USE_HTTPS, default=True): bool,
                vol.Optional(CONF_VERIFY_SSL, default=False): bool,
            }),
            errors=errors,
        )

    async def async_step_discovery(
        self, user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        if user_input is not None:
            data = {**self._user_input, **user_input}
            return self.async_create_entry(
                title=f"Niagara ({self._user_input[CONF_HOST]})",
                data=data,
            )

        return self.async_show_form(
            step_id="discovery",
            data_schema=vol.Schema({
                vol.Optional(CONF_DEVICE_NAME, default=DEFAULT_DEVICE_NAME): str,
                vol.Optional("scan_interval", default=DEFAULT_POLL_INTERVAL): int,
                vol.Optional(CONF_POINT_FILTER, default=""): str,
                vol.Optional(CONF_DEVICE_DEPTH, default=DEFAULT_DEVICE_DEPTH): int,
                vol.Optional(CONF_AREA_DEPTH, default=DEFAULT_AREA_DEPTH): int,
            }),
            description_placeholders={
                "point_count": str(self._point_count),
                "host": self._user_input.get(CONF_HOST, ""),
            },
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
                    CONF_DEVICE_DEPTH,
                    default=current.get(CONF_DEVICE_DEPTH, DEFAULT_DEVICE_DEPTH),
                ): int,
                vol.Optional(
                    CONF_AREA_DEPTH,
                    default=current.get(CONF_AREA_DEPTH, DEFAULT_AREA_DEPTH),
                ): int,
            }),
        )
