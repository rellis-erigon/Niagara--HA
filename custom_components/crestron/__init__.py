"""Crestron AV Control integration for Home Assistant."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .cip import CipClient
from .const import CONF_IPID, CONF_USE_SSL, DOMAIN, SIGNAL_STATE_UPDATE

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.MEDIA_PLAYER]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Crestron AV from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    ipid = entry.data[CONF_IPID]
    use_ssl = entry.data.get(CONF_USE_SSL, False)

    def _on_state_change(*_args):
        async_dispatcher_send(hass, f"{SIGNAL_STATE_UPDATE}_{entry.entry_id}")

    client = CipClient(
        host=host,
        port=port,
        ipid=ipid,
        use_ssl=use_ssl,
        on_digital=_on_state_change,
        on_analog=_on_state_change,
        on_serial=_on_state_change,
        on_connect=lambda: _LOGGER.info("Crestron processor connected"),
        on_disconnect=lambda: (
            _LOGGER.warning("Crestron processor disconnected"),
            _on_state_change(),
        ),
    )

    connected = await client.connect()
    if not connected:
        _LOGGER.error(
            "Could not connect to Crestron processor at %s:%d", host, port
        )
        return False

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = client
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Crestron AV config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        client: CipClient = hass.data[DOMAIN].pop(entry.entry_id)
        await client.disconnect()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
