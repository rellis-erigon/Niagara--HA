"""Base entity for Niagara BMS integration."""

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import (
    BAD_STATUSES,
    NiagaraCoordinator,
    NiagaraDevice,
    NiagaraPoint,
    decode_niagara_name,
    stable_id,
)


def build_device_info(
    coordinator: NiagaraCoordinator, group: str, area: str | None = None,
) -> DeviceInfo:
    """Build the device registry entry for a Niagara device group.

    Shared by the per-point entities and the device-level ones (climate, fan)
    so both resolve to the same device. The identifier is derived from the
    host and group only — change how it is built and every device in the
    registry is orphaned.
    """
    group_parts = [decode_niagara_name(p) for p in group.split("/") if p]
    # Name a device by the tail of its path. The full path plus the
    # integration name ran to about 60 characters — roughly "Site BMS —
    # Station / Electrical / DB-1-Loadingdock-Light" — which every
    # dashboard truncated, and it repeats on every entity underneath.
    depth = min(coordinator.device_name_depth, len(group_parts))
    device_label = " / ".join(group_parts[-depth:]) or coordinator.device_name
    device_id = f"niagara_{stable_id(coordinator.host + '/' + group)}"

    info = DeviceInfo(
        identifiers={(DOMAIN, device_id)},
        name=device_label,
        manufacturer="Tridium",
        model="Niagara 4",
        sw_version="oBIX",
    )
    if area:
        info["suggested_area"] = area
    return info


def point_is_usable(
    coordinator: NiagaraCoordinator, point: NiagaraPoint | None,
) -> bool:
    """Whether a point's current reading can be trusted."""
    if point is None:
        return False
    live = coordinator.data.get(point.path) if coordinator.data else None
    if live is None:
        return False
    if live.value is None:
        return False
    if live.status and live.status.lower() in BAD_STATUSES:
        return False
    if live.age is not None and live.age > coordinator.stale_after:
        return False
    return True


class NiagaraEntity(CoordinatorEntity[NiagaraCoordinator]):
    """Base class for a Niagara BMS entity."""

    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: NiagaraCoordinator, point: NiagaraPoint) -> None:
        super().__init__(coordinator)
        self._point = point
        self._attr_unique_id = f"niagara_{stable_id(point.path)}"
        # A published device names its points by what they do — "Fan" rather
        # than "IndoorFanStatus". The unique id stays keyed on the path, so
        # renaming never orphans an entity.
        self._attr_name = point.slot_name or decode_niagara_name(point.name)

        self._attr_device_info = build_device_info(
            coordinator,
            coordinator.get_group(point),
            coordinator.get_area(point),
        )

    @property
    def available(self) -> bool:
        """A point is available only if its reading can be trusted.

        Previously any point present in the coordinator counted as available,
        so a reading frozen since the last successful poll was indistinguishable
        from a live one.
        """
        if not self.coordinator.last_update_success:
            return False
        if not self.coordinator.data:
            return False

        point = self.coordinator.data.get(self._point.path)
        if point is None:
            return False
        if point.value is None:
            return False
        if point.status and point.status.lower() in BAD_STATUSES:
            return False
        if point.age is not None and point.age > self.coordinator.stale_after:
            return False
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Attributes that change only when something really has.

        `seconds_since_update` used to be here, and it was quietly the most
        expensive line in the integration. Home Assistant writes a new
        `states` row whenever the state *or the attributes* change, so an
        attribute carrying the age made every entity write a row on every
        poll whether its reading moved or not. On this station that was
        2,256 entities every 30 seconds — 5.9 million rows a day and a
        44.7 million row, 9.9 GB database.

        Nothing is lost by dropping it. Staleness already decides
        `available`, and Home Assistant's own `last_updated` says when the
        state last changed, which is the question the attribute was
        answering badly.
        """
        point = self._current_point
        if point is None:
            return None
        attrs: dict[str, Any] = {"niagara_path": point.path}
        if point.status:
            attrs["niagara_status"] = point.status
        return attrs

    @property
    def _current_point(self) -> NiagaraPoint | None:
        if self.coordinator.data:
            return self.coordinator.data.get(self._point.path)
        return self._point


class NiagaraDeviceEntity(CoordinatorEntity[NiagaraCoordinator]):
    """Base for an entity representing a whole device rather than one point.

    Climate and fan entities are assembled from several points at once, so
    they key on the device group instead of a path and read their slots
    through the coordinator on every state access.
    """

    _attr_has_entity_name = True

    # Set by each subclass so two device-level entities on one device cannot
    # collide on a unique id.
    DOMAIN_KEY = "device"

    def __init__(self, coordinator: NiagaraCoordinator, device: NiagaraDevice) -> None:
        super().__init__(coordinator)
        self._device = device
        self._group = device.group
        self._attr_unique_id = (
            f"niagara_{self.DOMAIN_KEY}_{stable_id(coordinator.host + '/' + device.group)}"
        )
        sample = next(iter(device.slots.values()), None)
        area = coordinator.get_area(sample) if sample else None
        self._attr_device_info = build_device_info(coordinator, device.group, area)

    def _slot_point(self, slot: str) -> NiagaraPoint | None:
        """The live point filling a slot, or None if it is absent or faulted."""
        point = self._device.slots.get(slot)
        if point is None:
            return None
        if not self.coordinator.data:
            return None
        live = self.coordinator.data.get(point.path)
        if not point_is_usable(self.coordinator, live):
            return None
        return live

    def _number(self, slot: str) -> float | None:
        point = self._slot_point(slot)
        if point is None or point.value is None:
            return None
        try:
            return float(point.value)
        except (TypeError, ValueError):
            return None

    def _boolean(self, slot: str) -> bool | None:
        point = self._slot_point(slot)
        if point is None or point.value is None:
            return None
        return str(point.value).strip().lower() in {
            "true", "1", "on", "yes", "active", "running", "run",
        }

    def _text(self, slot: str) -> str | None:
        point = self._slot_point(slot)
        if point is None or point.value is None:
            return None
        return str(point.value).strip()

    @property
    def available(self) -> bool:
        """Available while any one of the device's slots is reporting.

        A device-level entity is a view over several points, so it should not
        vanish because one of its optional slots faulted — only when nothing
        it is built from can be read at all.
        """
        if not self.coordinator.last_update_success:
            return False
        return any(self._slot_point(slot) is not None for slot in self._device.slots)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs: dict[str, Any] = {"niagara_group": self._group}
        for slot, point in sorted(self._device.slots.items()):
            attrs[f"path_{slot}"] = point.path
        return attrs


def build_station_device_info(coordinator: NiagaraCoordinator) -> DeviceInfo:
    """The device representing the station itself.

    Everything until now hung off a folder, so there was nowhere to put
    something that is true of the whole station — an alarm count belongs to
    the station, not to any one air handler.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, f"niagara_station_{stable_id(coordinator.host)}")},
        name=coordinator.device_name,
        manufacturer="Tridium",
        model="Niagara 4 station",
        sw_version="oBIX",
    )


class NiagaraStationEntity(CoordinatorEntity[NiagaraCoordinator]):
    """Base for an entity describing the station rather than a point."""

    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = True

    # Distinguishes the station-level entities from each other.
    KEY = "station"

    def __init__(
        self, coordinator: NiagaraCoordinator, key: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"niagara_{key or self.KEY}_{stable_id(coordinator.host)}"
        )
        self._attr_device_info = build_station_device_info(coordinator)

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success
