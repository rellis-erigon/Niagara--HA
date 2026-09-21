"""Base entity for Niagara BMS integration."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import (
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
        self._attr_name = decode_niagara_name(point.name)

        group = coordinator.get_group(point)
        group_parts = [decode_niagara_name(p) for p in group.split("/")]
        short_group = " / ".join(group_parts[-3:]) if len(group_parts) > 3 else " / ".join(group_parts)
        device_id = f"niagara_{stable_id(coordinator.host + '/' + group)}"

        device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=f"{coordinator.device_name} — {short_group}",
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
        if not self.coordinator.last_update_success:
            return False
        if not self.coordinator.data:
            return False
        point = self.coordinator.data.get(self._point.path)
        if point is None:
            return False
        if point.status and point.status not in ("ok", ""):
            return point.status != "fault"
        return True

    @property
    def _current_point(self) -> NiagaraPoint | None:
        if self.coordinator.data:
            return self.coordinator.data.get(self._point.path)
        return self._point
