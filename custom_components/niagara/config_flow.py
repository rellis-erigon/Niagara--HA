"""Config flow for Niagara BMS integration."""

import logging
import os
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    ADDON_SLUG_SUFFIX,
    CONF_ADDON_URL,
    CONF_AREA_DEPTH,
    CONF_DEVICE_NAME,
    CONF_DEVICE_NAME_DEPTH,
    DEFAULT_ADDON_URL,
    DEFAULT_AREA_DEPTH,
    DEFAULT_DEVICE_NAME_DEPTH,
    DEFAULT_DEVICE_NAME,
    DEFAULT_INGRESS_PORT,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    SUPERVISOR_API,
)

_LOGGER = logging.getLogger(__name__)


async def async_discover_addon_url(hass: HomeAssistant) -> str | None:
    """Ask Supervisor where the Niagara BMS Bridge add-on is reachable.

    The add-on's hostname includes a per-install repository hash, so the only
    reliable way to know it is to ask. Returns None when not running under
    Supervisor, or when the add-on is not installed.
    """
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None

    headers = {"Authorization": f"Bearer {token}"}
    session = async_get_clientsession(hass)
    timeout = aiohttp.ClientTimeout(total=10)

    try:
        async with session.get(
            f"{SUPERVISOR_API}/addons", headers=headers, timeout=timeout,
        ) as resp:
            resp.raise_for_status()
            addons = (await resp.json()).get("data", {}).get("addons", [])

        slug = next(
            (a["slug"] for a in addons
             if a.get("slug", "").endswith(ADDON_SLUG_SUFFIX)),
            None,
        )
        if not slug:
            return None

        async with session.get(
            f"{SUPERVISOR_API}/addons/{slug}/info", headers=headers, timeout=timeout,
        ) as resp:
            resp.raise_for_status()
            info = (await resp.json()).get("data", {})
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        _LOGGER.debug("Add-on discovery via Supervisor failed: %s", err)
        return None

    host = info.get("hostname") or slug.replace("_", "-")
    port = info.get("ingress_port") or DEFAULT_INGRESS_PORT
    return f"http://{host}:{port}"


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
                session = async_get_clientsession(self.hass)
                async with session.get(
                    f"{addon_url}/api/integration/points",
                    timeout=aiohttp.ClientTimeout(total=30),
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

        suggested_url = await async_discover_addon_url(self.hass) or DEFAULT_ADDON_URL

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_ADDON_URL, default=suggested_url): str,
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
                vol.Optional(
                    CONF_DEVICE_NAME_DEPTH,
                    default=current.get(
                        CONF_DEVICE_NAME_DEPTH, DEFAULT_DEVICE_NAME_DEPTH,
                    ),
                ): vol.All(int, vol.Range(min=1, max=4)),
            }),
        )
