"""Config flow for Crestron AV Control integration."""

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .cip import CipClient
from .const import (
    CONF_ANALOG_START,
    CONF_DEVICE_NAME,
    CONF_DIGITAL_START,
    CONF_IPID,
    CONF_JOIN_INCREMENT,
    CONF_NUM_ZONES,
    CONF_SERIAL_START,
    CONF_USE_SSL,
    DEFAULT_ANALOG_START,
    DEFAULT_DEVICE_NAME,
    DEFAULT_DIGITAL_START,
    DEFAULT_IPID,
    DEFAULT_JOIN_INCREMENT,
    DEFAULT_NUM_ZONES,
    DEFAULT_SERIAL_START,
    DEFAULT_SSL_PORT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class CrestronConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Crestron AV Control."""

    VERSION = 1

    def __init__(self) -> None:
        self._user_input: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: connection details."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            ipid = user_input[CONF_IPID]
            use_ssl = user_input.get(CONF_USE_SSL, False)

            await self.async_set_unique_id(f"{host}:{port}:{ipid}")
            self._abort_if_unique_id_configured()

            client = CipClient(
                host=host, port=port, ipid=ipid, use_ssl=use_ssl
            )
            try:
                ok = await client.connect()
                if not ok:
                    errors["base"] = "cannot_connect"
                await client.disconnect()
            except Exception:
                _LOGGER.exception("Connection test failed")
                errors["base"] = "cannot_connect"

            if not errors:
                self._user_input = user_input
                return await self.async_step_zones()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_PORT, default=DEFAULT_SSL_PORT): int,
                    vol.Required(CONF_IPID, default=DEFAULT_IPID): int,
                    vol.Optional(CONF_USE_SSL, default=True): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_zones(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: zone configuration and join mapping."""
        if user_input is not None:
            data = {**self._user_input, **user_input}
            title = (
                f"{data.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME)} "
                f"({self._user_input[CONF_HOST]})"
            )
            return self.async_create_entry(title=title, data=data)

        return self.async_show_form(
            step_id="zones",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_DEVICE_NAME, default=DEFAULT_DEVICE_NAME
                    ): str,
                    vol.Optional(
                        CONF_NUM_ZONES, default=DEFAULT_NUM_ZONES
                    ): vol.All(int, vol.Range(min=1, max=64)),
                    vol.Optional(
                        CONF_DIGITAL_START, default=DEFAULT_DIGITAL_START
                    ): int,
                    vol.Optional(
                        CONF_ANALOG_START, default=DEFAULT_ANALOG_START
                    ): int,
                    vol.Optional(
                        CONF_SERIAL_START, default=DEFAULT_SERIAL_START
                    ): int,
                    vol.Optional(
                        CONF_JOIN_INCREMENT, default=DEFAULT_JOIN_INCREMENT
                    ): vol.All(int, vol.Range(min=1, max=100)),
                }
            ),
            description_placeholders={
                "host": self._user_input.get(CONF_HOST, ""),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CrestronOptionsFlow(config_entry)


class CrestronOptionsFlow(OptionsFlow):
    """Handle options for Crestron AV Control."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self._config_entry.data, **self._config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_DEVICE_NAME,
                        default=current.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME),
                    ): str,
                    vol.Optional(
                        CONF_NUM_ZONES,
                        default=current.get(CONF_NUM_ZONES, DEFAULT_NUM_ZONES),
                    ): vol.All(int, vol.Range(min=1, max=64)),
                }
            ),
        )
