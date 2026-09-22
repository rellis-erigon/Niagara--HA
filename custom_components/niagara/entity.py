"""Base entity for Niagara BMS integration."""

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import (
    BAD_STATUSES,
    NiagaraCoordinator,
    NiagaraPoint,
    decode_niagara_name,
    stable_id,
)


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

        group = coordinator.get_group(point)
        group_parts = [
            decode_niagara_name(p) for p in group.split("/") if p
        ]
        # Name a device by the tail of its path. The full path plus the
        # integration name ran to about 60 characters — "Mercure BMS —
        # MercureBMS / Electrical / DB-B3-2-Loadingdock-Light" — which every
        # dashboard truncated, and it repeats on every entity underneath.
        depth = min(coordinator.device_name_depth, len(group_parts))
        device_label = " / ".join(group_parts[-depth:]) or coordinator.device_name
        device_id = f"niagara_{stable_id(coordinator.host + '/' + group)}"

        device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=device_label,
            manufacturer="Tridium",
            model="Niagara 4",
            sw_version="oBIX",
        )
        area = coordinator.get_area(point)
        if area:
            device_info["suggested_area"] = area
        self._attr_device_info = device_info

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
        point = self._current_point
        if point is None:
            return None
        attrs: dict[str, Any] = {"niagara_path": point.path}
        if point.status:
            attrs["niagara_status"] = point.status
        if point.age is not None:
            attrs["seconds_since_update"] = point.age
        return attrs

    @property
    def _current_point(self) -> NiagaraPoint | None:
        if self.coordinator.data:
            return self.coordinator.data.get(self._point.path)
        return self._point
