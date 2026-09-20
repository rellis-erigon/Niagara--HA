"""DataUpdateCoordinator for Niagara BMS.

Polls the Niagara BMS add-on's REST API for enabled points and their
current values. The add-on handles the oBIX connection, point discovery,
and management UI; this coordinator just creates native HA entities.
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_ADDON_URL,
    CONF_AREA_DEPTH,
    CONF_DEVICE_NAME,
    DEFAULT_AREA_DEPTH,
    DEFAULT_DEVICE_NAME,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_NIAGARA_ESCAPE_RE = re.compile(r"\$([0-9a-fA-F]{2})")


def decode_niagara_name(name: str) -> str:
    return _NIAGARA_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), name)


def stable_id(path: str) -> str:
    return hashlib.md5(path.encode()).hexdigest()[:12]


SKIP_SEGMENTS = frozenset({
    "config", "Drivers", "NiagaraNetwork", "ObixNetwork",
    "points", "out", "exports", "obix", "",
})


@dataclass
class NiagaraPoint:
    path: str
    name: str
    value: str | None = None
    point_type: str = "unknown"
    unit: str | None = None
    writable: bool = False
    enum_range: list[str] = field(default_factory=list)
    group: str = "Ungrouped"
    status: str | None = None


class NiagaraCoordinator(DataUpdateCoordinator[dict[str, NiagaraPoint]]):
    """Coordinator that polls the Niagara BMS add-on for point data."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.addon_url = entry.data[CONF_ADDON_URL].rstrip("/")
        self.device_name = entry.options.get(
            CONF_DEVICE_NAME, entry.data.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME)
        )
        self.area_depth = entry.options.get(
            CONF_AREA_DEPTH, entry.data.get(CONF_AREA_DEPTH, DEFAULT_AREA_DEPTH)
        )

        scan_interval = entry.options.get(
            "scan_interval", entry.data.get("scan_interval", DEFAULT_POLL_INTERVAL)
        )

        self.points: dict[str, NiagaraPoint] = {}
        self.device_folders: list[str] = []

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )

    @property
    def host(self) -> str:
        return self.addon_url

    async def async_setup(self) -> None:
        """Initial point discovery from the add-on."""
        try:
            data = await self._fetch_points()
            self.points = data["points"]
            self.device_folders = data["device_folders"]
            _LOGGER.info(
                "Loaded %d enabled points from add-on (%d total, %d device folders)",
                len(self.points),
                data.get("total", 0),
                len(self.device_folders),
            )
        except Exception as err:
            _LOGGER.error("Failed to load points from add-on: %s", err)
            raise

    async def _async_update_data(self) -> dict[str, NiagaraPoint]:
        """Poll the add-on for current values."""
        try:
            values = await self._fetch_values()
            for path, val in values.items():
                if path in self.points:
                    self.points[path].value = val
            return self.points
        except Exception as err:
            raise UpdateFailed(f"Error polling add-on: {err}") from err

    async def _fetch_points(self) -> dict[str, Any]:
        """Fetch full point list from the add-on."""
        import aiohttp

        url = f"{self.addon_url}/api/integration/points"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
        except Exception as err:
            raise UpdateFailed(f"Cannot reach add-on at {url}: {err}") from err

        points: dict[str, NiagaraPoint] = {}
        for p in data.get("points", []):
            path = p["path"]
            unit = p.get("unit", "") or None
            if unit and unit.lower() == "null":
                unit = None
            points[path] = NiagaraPoint(
                path=path,
                name=p.get("name", path.rstrip("/").split("/")[-1]),
                value=p.get("value"),
                point_type=p.get("type", "unknown"),
                unit=unit,
                writable=p.get("writable", False),
                enum_range=p.get("enum_range", []),
                group=p.get("group", "Ungrouped"),
            )

        return {
            "points": points,
            "device_folders": data.get("device_folders", []),
            "total": data.get("total", 0),
        }

    async def _fetch_values(self) -> dict[str, str]:
        """Fetch current values from the add-on (lightweight poll)."""
        import aiohttp

        url = f"{self.addon_url}/api/integration/values"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    resp.raise_for_status()
                    return await resp.json()
        except Exception as err:
            raise UpdateFailed(f"Cannot reach add-on at {url}: {err}") from err

    def _clean_path_parts(self, path: str) -> list[str]:
        return [p for p in path.strip("/").split("/") if p not in SKIP_SEGMENTS]

    def get_group(self, point: NiagaraPoint) -> str:
        """Return the device group for a point (provided by the add-on)."""
        return point.group

    def get_area(self, point: NiagaraPoint) -> str | None:
        """Map a point to an HA area based on its path hierarchy."""
        parts = self._clean_path_parts(point.path)
        if len(parts) > self.area_depth:
            return decode_niagara_name(parts[self.area_depth - 1])
        return None

    async def async_shutdown(self) -> None:
        """Clean up on unload."""
        pass
