"""Media player platform for Crestron AV zones.

Each audio zone becomes a media_player entity with volume, mute, and
source-select controls.  Join numbers are derived from the Xpanel
project's Subpage Reference List mapping:

  Zone N (0-indexed):
    volume   = analog_start  + N * increment        (analog)
    mute     = digital_start + N * increment         (digital)
    name     = serial_start  + N * increment         (serial)
    source k = digital_start + N * increment + k     (digital, k=1..3)
    src name = serial_start  + N * increment + k     (serial,  k=1..3)
"""

import logging

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .cip import CipClient
from .const import (
    CONF_ANALOG_START,
    CONF_DEVICE_NAME,
    CONF_DIGITAL_START,
    CONF_IPID,
    CONF_JOIN_INCREMENT,
    CONF_NUM_ZONES,
    CONF_SERIAL_START,
    DEFAULT_ANALOG_START,
    DEFAULT_DEVICE_NAME,
    DEFAULT_DIGITAL_START,
    DEFAULT_JOIN_INCREMENT,
    DEFAULT_NUM_ZONES,
    DEFAULT_SERIAL_START,
    DOMAIN,
    NUM_SOURCES_PER_ZONE,
    SIGNAL_STATE_UPDATE,
    VOLUME_MAX,
    VOLUME_STEP_PCT,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Crestron media player zones from a config entry."""
    client: CipClient = hass.data[DOMAIN][entry.entry_id]
    cfg = {**entry.data, **entry.options}

    num_zones = cfg.get(CONF_NUM_ZONES, DEFAULT_NUM_ZONES)
    device_name = cfg.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME)
    digital_start = cfg.get(CONF_DIGITAL_START, DEFAULT_DIGITAL_START)
    analog_start = cfg.get(CONF_ANALOG_START, DEFAULT_ANALOG_START)
    serial_start = cfg.get(CONF_SERIAL_START, DEFAULT_SERIAL_START)
    increment = cfg.get(CONF_JOIN_INCREMENT, DEFAULT_JOIN_INCREMENT)
    host = entry.data[CONF_HOST]
    ipid = entry.data[CONF_IPID]

    entities = []
    for idx in range(num_zones):
        entities.append(
            CrestronZone(
                client=client,
                entry_id=entry.entry_id,
                zone_index=idx,
                device_name=device_name,
                host=host,
                ipid=ipid,
                volume_join=analog_start + idx * increment,
                mute_join=digital_start + idx * increment,
                name_join=serial_start + idx * increment,
                source_digital_joins=[
                    digital_start + idx * increment + k
                    for k in range(1, NUM_SOURCES_PER_ZONE + 1)
                ],
                source_name_joins=[
                    serial_start + idx * increment + k
                    for k in range(1, NUM_SOURCES_PER_ZONE + 1)
                ],
            )
        )

    async_add_entities(entities)


class CrestronZone(MediaPlayerEntity):
    """A single Crestron audio zone as a media_player entity."""

    _attr_has_entity_name = True
    _attr_supported_features = (
        MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.SELECT_SOURCE
    )
    _attr_icon = "mdi:speaker"

    def __init__(
        self,
        client: CipClient,
        entry_id: str,
        zone_index: int,
        device_name: str,
        host: str,
        ipid: int,
        volume_join: int,
        mute_join: int,
        name_join: int,
        source_digital_joins: list[int],
        source_name_joins: list[int],
    ) -> None:
        self._client = client
        self._entry_id = entry_id
        self._zone_index = zone_index
        self._device_name = device_name
        self._host = host
        self._ipid = ipid
        self._volume_join = volume_join
        self._mute_join = mute_join
        self._name_join = name_join
        self._source_digital_joins = source_digital_joins
        self._source_name_joins = source_name_joins

        self._attr_unique_id = f"crestron_{host}_{ipid}_zone_{zone_index}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._host}_{self._ipid}")},
            name=self._device_name,
            manufacturer="Crestron",
            model="AV Processor",
        )

    @property
    def name(self) -> str:
        zone_label = self._client.serial_states.get(self._name_join, "")
        if zone_label:
            return zone_label
        return f"Zone {self._zone_index + 1}"

    @property
    def available(self) -> bool:
        return self._client.connected

    @property
    def state(self) -> MediaPlayerState:
        if not self._client.connected:
            return MediaPlayerState.OFF
        if self.is_volume_muted:
            return MediaPlayerState.IDLE
        return MediaPlayerState.ON

    @property
    def volume_level(self) -> float | None:
        raw = self._client.analog_states.get(self._volume_join)
        if raw is None:
            return None
        return raw / VOLUME_MAX

    @property
    def is_volume_muted(self) -> bool | None:
        return self._client.digital_states.get(self._mute_join)

    @property
    def source(self) -> str | None:
        for i, dj in enumerate(self._source_digital_joins):
            if self._client.digital_states.get(dj):
                return self._source_name(i)
        return None

    @property
    def source_list(self) -> list[str]:
        return [self._source_name(i) for i in range(NUM_SOURCES_PER_ZONE)]

    def _source_name(self, index: int) -> str:
        sj = self._source_name_joins[index]
        name = self._client.serial_states.get(sj, "")
        return name if name else f"Source {index + 1}"

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "zone_index": self._zone_index,
            "volume_join": self._volume_join,
            "mute_join": self._mute_join,
            "name_join": self._name_join,
        }

    # ── commands ──────────────────────────────────────────────────

    async def async_set_volume_level(self, volume: float) -> None:
        raw = int(volume * VOLUME_MAX)
        raw = max(0, min(VOLUME_MAX, raw))
        await self._client.set_analog(self._volume_join, raw)

    async def async_volume_up(self) -> None:
        current = (self.volume_level or 0.0) + VOLUME_STEP_PCT
        await self.async_set_volume_level(min(1.0, current))

    async def async_volume_down(self) -> None:
        current = (self.volume_level or 0.0) - VOLUME_STEP_PCT
        await self.async_set_volume_level(max(0.0, current))

    async def async_mute_volume(self, mute: bool) -> None:
        await self._client.set_digital(self._mute_join, mute)

    async def async_select_source(self, source: str) -> None:
        for i in range(NUM_SOURCES_PER_ZONE):
            if self._source_name(i) == source:
                await self._client.pulse_digital(
                    self._source_digital_joins[i]
                )
                return
        _LOGGER.warning("Unknown source '%s' for zone %d", source, self._zone_index)

    # ── state updates ─────────────────────────────────────────────

    async def async_added_to_hass(self) -> None:
        signal = f"{SIGNAL_STATE_UPDATE}_{self._entry_id}"
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal, self._async_state_changed
            )
        )

    @callback
    def _async_state_changed(self) -> None:
        self.async_write_ha_state()
