"""DataUpdateCoordinator for Niagara BMS.

Polls the Niagara BMS add-on's REST API for enabled points and their
current values. The add-on handles the oBIX connection, point discovery,
and management UI; this coordinator just creates native HA entities.
"""

import asyncio
import hashlib
import logging
import re
from time import monotonic
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_ADDON_URL,
    CONF_AREA_DEPTH,
    CONF_DEVICE_NAME,
    CONF_DEVICE_NAME_DEPTH,
    DEFAULT_AREA_DEPTH,
    DEFAULT_DEVICE_NAME_DEPTH,
    DEFAULT_DEVICE_NAME,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_NIAGARA_ESCAPE_RE = re.compile(r"\$([0-9a-fA-F]{2})")

POINT_REFRESH_INTERVAL = 10

# How long the add-on may hold a value request open. Under its own 45s
# ceiling, so the add-on is the one that decides when to answer.
LONG_POLL_SECONDS = 40.0
LISTEN_BACKOFF_START = 2
LISTEN_BACKOFF_MAX = 60


def decode_niagara_name(name: str) -> str:
    return _NIAGARA_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), name)


def stable_id(path: str) -> str:
    return hashlib.md5(path.encode()).hexdigest()[:12]


SKIP_SEGMENTS = frozenset({
    "config", "Drivers", "NiagaraNetwork", "ObixNetwork",
    "points", "out", "exports", "obix", "",
})

def _parent_path(path: str) -> str:
    parts = [p for p in path.strip("/").split("/") if p not in SKIP_SEGMENTS]
    if len(parts) < 2:
        return ""
    return "/".join(parts[:-1])


# A value older than this many poll intervals is treated as stale. Three
# cycles tolerates a single missed poll and a slow one without flapping.
STALE_INTERVAL_MULTIPLIER = 3

# Statuses the add-on reports that mean the reading cannot be trusted.
BAD_STATUSES = frozenset({"fault", "down", "stale", "disabled", "unknown"})


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
    age: float | None = None
    precision: int | None = None
    # Set when the point fills a slot on a published device.
    slot: str | None = None
    slot_name: str | None = None
    slot_device_class: str | None = None
    slot_state_class: str | None = None
    slot_units: list[str] = field(default_factory=list)
    # The template that typed the point's device, e.g. "fcu". Only set for
    # points on a published device.
    device_type: str | None = None
    # The area the add-on's rules map this point's folder to. None means no
    # rule claimed it, and the depth-based guess below is used instead.
    area: str | None = None


@dataclass
class NiagaraDevice:
    """A published device: its template and the points filling its slots."""

    group: str
    device_type: str
    slots: dict[str, NiagaraPoint] = field(default_factory=dict)

    def path(self, slot: str) -> str | None:
        point = self.slots.get(slot)
        return point.path if point else None


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
        self.device_name_depth = max(1, int(entry.options.get(
            CONF_DEVICE_NAME_DEPTH,
            entry.data.get(CONF_DEVICE_NAME_DEPTH, DEFAULT_DEVICE_NAME_DEPTH),
        )))

        scan_interval = entry.options.get(
            "scan_interval", entry.data.get("scan_interval", DEFAULT_POLL_INTERVAL)
        )
        self.scan_interval = scan_interval
        self.stale_after = scan_interval * STALE_INTERVAL_MULTIPLIER

        self.points: dict[str, NiagaraPoint] = {}
        self.device_folders: list[str] = []
        self.alarms: list[dict] = []
        self.alarm_summary: dict = {}
        self.alarms_supported = False
        # Alarms placed on their devices, rebuilt whenever the console or
        # the point list changes. Not computed on demand: every device's
        # alarm sensor reads it on every state update, and the lookup walks
        # the point list.
        self._alarms_by_group: dict[str, list[dict]] = {}
        self.history_sync = None
        self._last_history_sync = 0.0
        # The value cache sequence this coordinator has caught up to, and
        # the task holding a request open waiting for the next change.
        self._value_seq = 0
        self._listener_task: asyncio.Task | None = None
        self._listener_supported = True
        self._session: aiohttp.ClientSession | None = None
        self._poll_count = 0

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )

    @property
    def host(self) -> str:
        return self.addon_url

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    def _purge_orphaned_entities(self) -> int:
        """Remove entities whose point the add-on no longer serves.

        Entities were never removed, so anything that stopped being a point —
        a disabled selection, a renamed path, or the history-extension
        sub-points dropped in 3.2.0 — stayed in the registry forever as a dead
        row in the dashboard. On this station that was 1,128 of them.

        Only ever called after a successful fetch that returned points, so a
        transient add-on outage cannot wipe the registry.
        """
        if not self.points:
            return 0

        registry = er.async_get(self.hass)
        valid = {f"niagara_{stable_id(path)}" for path in self.points}
        valid |= self.device_entity_unique_ids()
        valid |= self.alarm_entity_unique_ids()
        stale = [
            entity.entity_id
            for entity in list(registry.entities.values())
            if entity.config_entry_id == self.entry.entry_id
            and entity.unique_id not in valid
        ]
        for entity_id in stale:
            registry.async_remove(entity_id)

        if stale:
            _LOGGER.info(
                "Removed %d entities whose points no longer exist", len(stale),
            )

        # Unconditionally, not only when something was removed: regrouping
        # empties a device without deleting a single entity. Moving the
        # meter totals onto their boards left nineteen devices called
        # "MeterTotal" holding nothing, and this never ran to clear them.
        self._purge_empty_devices(registry)
        return len(stale)

    def _purge_empty_devices(self, registry: er.EntityRegistry) -> None:
        """Drop devices left with no entities once their points went away."""
        devices = dr.async_get(self.hass)
        for device in list(devices.devices.values()):
            if self.entry.entry_id not in device.config_entries:
                continue
            if er.async_entries_for_device(
                registry, device.id, include_disabled_entities=True,
            ):
                continue
            devices.async_update_device(
                device.id, remove_config_entry_id=self.entry.entry_id,
            )

    async def async_setup(self) -> None:
        """Initial point discovery from the add-on."""
        try:
            data = await self._fetch_points()
            self.points = data["points"]
            self.device_folders = data["device_folders"]
            self._purge_orphaned_entities()
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
        self._poll_count += 1
        try:
            if self._poll_count % POINT_REFRESH_INTERVAL == 0:
                data = await self._fetch_points()
                new_points = data["points"]
                for path, pt in new_points.items():
                    if path in self.points and self.points[path].value is not None:
                        pt.value = self.points[path].value
                self.points = new_points
                self.device_folders = data["device_folders"]
                self._index_alarms()
                self._purge_orphaned_entities()
                # Point list refreshes are infrequent, and a device only
                # becomes published between two of them, so this is the
                # natural moment to reconcile the slower housekeeping.
                await self._async_housekeeping()

            await self._async_fetch_alarms()

            values = await self._fetch_values()
            self._apply_values(values)
            return self.points
        except Exception as err:
            raise UpdateFailed(f"Error polling add-on: {err}") from err

    async def _async_housekeeping(self) -> None:
        """Refresh repair issues and register newly published meters.

        Never allowed to fail a refresh: none of it is needed for entities
        to work, and losing every reading because the energy dashboard was
        unhappy would be a poor trade.
        """
        try:
            await self._async_sync_repairs()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not refresh repair issues: %s", err)

        try:
            await self._async_register_energy_meters()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not update the energy dashboard: %s", err)

        try:
            await self._async_sync_histories()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not sync trend logs: %s", err)

    async def _async_sync_histories(self) -> None:
        """Import trend logs, at most hourly.

        Housekeeping runs every few minutes, which is far too often for
        this: the statistics it writes are hourly, so a run that completes
        has nothing to do until the next hour begins. The interval is
        checked here rather than with a timer so it shares the
        housekeeping's guarantee of never failing a refresh.
        """
        now = monotonic()
        if self._last_history_sync and now - self._last_history_sync < 3600:
            return

        from .history import HistorySync

        if self.history_sync is None:
            self.history_sync = HistorySync(self.hass, self)
        result = await self.history_sync.async_run()
        # Only count it as done when it actually ran. A sync refused
        # because another was in flight must not push the next attempt an
        # hour away.
        if "skipped" not in result:
            self._last_history_sync = now
        if result.get("synced"):
            _LOGGER.info("Imported %d hours of trend data", result["synced"])

    async def _async_sync_repairs(self) -> None:
        from .repairs import async_set_statistics_issue, async_sync_issues
        from .statistics import async_find_unit_conflicts

        session = self._get_session()
        async with session.get(
            f"{self.addon_url}/api/diagnostics",
            timeout=aiohttp.ClientTimeout(total=60),
        ) as response:
            response.raise_for_status()
            report = await response.json()
        async_sync_issues(self.hass, report)

        conflicts = await async_find_unit_conflicts(self.hass)
        async_set_statistics_issue(self.hass, len(conflicts))

    async def _async_register_energy_meters(self) -> None:
        """Put published meters on the energy dashboard.

        Publishing a device is the act of saying it is real, so it is also
        the point at which a meter should appear where people look for it.
        """
        from .energy import GAS_SLOTS, GRID_SLOTS, SOLAR_SLOTS, WATER_SLOTS
        from .energy import async_add_published_meters

        known = GRID_SLOTS | SOLAR_SLOTS | WATER_SLOTS | GAS_SLOTS
        registry = er.async_get(self.hass)

        meters = []
        for path, point in self.points.items():
            if point.slot not in known:
                continue
            if point.slot_state_class != "total_increasing":
                continue
            entity_id = registry.async_get_entity_id(
                "sensor", DOMAIN, f"niagara_{stable_id(path)}"
            )
            if not entity_id:
                continue
            meters.append({
                "entity_id": entity_id,
                "slot": point.slot,
                "name": point.slot_name or point.name,
            })

        changes = await async_add_published_meters(self.hass, meters)
        if changes:
            _LOGGER.info("Energy dashboard updated: %s", "; ".join(changes))

    async def _async_fetch_alarms(self) -> None:
        """Refresh the station's alarm console.

        Never allowed to fail the refresh. The points are what most of a
        dashboard is built from, and losing every reading because the alarm
        service was slow would be a poor trade. An add-on predating the
        endpoint returns 404, which reads the same as a station with no
        alarm service: unsupported, so no alarm entities appear.
        """
        try:
            session = self._get_session()
            async with session.get(
                f"{self.addon_url}/api/integration/alarms",
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                if response.status == 404:
                    self.alarms_supported = False
                    return
                response.raise_for_status()
                payload = await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            _LOGGER.debug("Could not read the alarm console: %s", err)
            return

        self.alarms_supported = bool(payload.get("supported"))
        self.alarms = payload.get("records") or []
        self.alarm_summary = payload.get("summary") or {}
        self._index_alarms()

    def _build_point_indexes(self) -> tuple[dict, dict]:
        """Lookups from an alarm's source to a device group.

        Two, because an alarm names its source two different ways. By
        folder, for a source given as a path — an alarm often sits on a
        point nobody enabled, so the folder of its exported siblings is
        what places it. And by (station, point name), because Niagara's
        alarm records carry no path at all, only "Station:PointName".

        A name claimed by points in more than one group maps to None
        rather than to whichever was seen first: on this station a hundred
        rooms have a point called the same thing, and attaching an alarm to
        the wrong room is worse than leaving it station-level.
        """
        by_folder: dict[str, str] = {}
        by_name: dict[tuple[str, str], str | None] = {}
        for point in self.points.values():
            folder = _parent_path(point.path)
            if folder:
                by_folder.setdefault(folder, point.group)

            name = decode_niagara_name(point.name).lower()
            if not name:
                continue
            for station in ("", self._station_of(point.path)):
                key = (station, name)
                if key in by_name and by_name[key] != point.group:
                    by_name[key] = None
                else:
                    by_name.setdefault(key, point.group)
        return by_folder, by_name

    @staticmethod
    def _station_of(path: str) -> str:
        """The Niagara station a point path sits under, lowercased."""
        parts = [p for p in path.strip("/").split("/") if p]
        for marker in ("niagaranetwork", "drivers"):
            for index, part in enumerate(parts):
                if part.lower() == marker and index + 1 < len(parts):
                    return decode_niagara_name(parts[index + 1]).lower()
        return ""

    def _index_alarms(self) -> None:
        """Place each alarm on a device, once per fetch."""
        grouped: dict[str, list[dict]] = {}
        if self.alarms:
            by_folder, by_name = self._build_point_indexes()
            for record in self.alarms:
                group = self._place_alarm(record, by_folder, by_name)
                if group is not None:
                    grouped.setdefault(group, []).append(record)
        self._alarms_by_group = grouped

    def _place_alarm(self, record: dict, by_folder: dict, by_name: dict):
        """The device group an alarm belongs to, or None if it cannot be
        placed. An alarm that cannot be placed stays station-level rather
        than being attached to the wrong device."""
        source = record.get("source") or ""
        if source:
            point = self.points.get(source)
            if point is not None:
                return point.group
            folder = _parent_path(source)
            if folder and folder in by_folder:
                return by_folder[folder]

        name = (record.get("source_name") or "").strip().lower()
        if not name:
            return None
        station = (record.get("source_station") or "").strip().lower()
        # The station-qualified name first: it is the more specific claim,
        # and it is what separates one room's fault point from a hundred
        # identically named ones elsewhere.
        for key in ((station, name), ("", name)):
            if key in by_name:
                return by_name[key]
        return None

    def alarms_by_group(self) -> dict[str, list[dict]]:
        """Active alarms keyed by the device group they belong to."""
        return self._alarms_by_group

    def alarm_entity_unique_ids(self) -> set[str]:
        """Unique ids of the alarm entities, which are not keyed on a point.

        Included in the purge's valid set for the same reason the climate and
        fan ids are: without it they are stale by definition.
        """
        if not self.alarms_supported:
            return set()
        station = stable_id(self.host)
        ids = {
            f"niagara_alarms_active_{station}",
            f"niagara_alarms_unacked_{station}",
            f"niagara_alarm_event_{station}",
        }
        for device in self.devices().values():
            ids.add(
                f"niagara_device_alarm_{stable_id(self.host + '/' + device.group)}"
            )
        return ids

    async def _fetch_points(self) -> dict[str, Any]:
        """Fetch full point list from the add-on."""
        url = f"{self.addon_url}/api/integration/points"
        try:
            session = self._get_session()
            async with session.get(url) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Cannot reach add-on at {url}: {err}") from err

        points: dict[str, NiagaraPoint] = {}
        for p in data.get("points", []):
            path = p["path"]
            unit = p.get("unit", "") or None
            if unit and unit.lower() == "null":
                unit = None
            raw_value = p.get("value")
            points[path] = NiagaraPoint(
                path=path,
                name=p.get("name", path.rstrip("/").split("/")[-1]),
                value=str(raw_value) if raw_value is not None else None,
                point_type=p.get("type", "unknown"),
                unit=unit,
                writable=p.get("writable", False),
                enum_range=p.get("enum_range", []),
                group=p.get("group", "Ungrouped"),
                status=p.get("status"),
                age=p.get("age"),
                precision=p.get("precision"),
                slot=p.get("slot"),
                slot_name=p.get("slot_name"),
                slot_device_class=p.get("device_class"),
                slot_state_class=p.get("state_class"),
                slot_units=p.get("slot_units") or [],
                device_type=p.get("device_type"),
                area=p.get("area"),
            )

        return {
            "points": points,
            "device_folders": data.get("device_folders", []),
            "total": data.get("total", 0),
        }

    async def _fetch_values(self) -> dict[str, Any]:
        """Every current value from the add-on.

        The full fetch, used by the scheduled refresh. The listener below
        asks for changes only; this one stays whole so a refresh always
        reconciles, whatever the listener has or has not seen.
        """
        url = f"{self.addon_url}/api/integration/values"
        try:
            session = self._get_session()
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Cannot reach add-on at {url}: {err}") from err

    def _apply_values(self, values: dict[str, Any]) -> int:
        """Write fetched readings onto the points. Returns how many moved."""
        changed = 0
        for path, entry in values.items():
            point = self.points.get(path)
            if point is None:
                continue
            if isinstance(entry, dict):
                raw = entry.get("value")
                new_value = str(raw) if raw is not None else None
                status = entry.get("status")
                age = entry.get("age")
            else:
                # An add-on predating the timestamped format.
                new_value = str(entry) if entry is not None else None
                status, age = None, None
            if (point.value, point.status) != (new_value, status):
                changed += 1
            point.value = new_value
            point.status = status
            point.age = age
        return changed

    async def _fetch_value_changes(self, wait: float) -> dict[str, Any] | None:
        """Hold a request open until something changes, then take the delta.

        None means the add-on does not support it — an older one answers
        with the flat map and no sequence, and the caller stops listening
        rather than turning this into a hot loop of full fetches.
        """
        url = f"{self.addon_url}/api/integration/values"
        params = {"since": str(self._value_seq), "wait": str(wait)}
        session = self._get_session()
        async with session.get(
            url, params=params,
            # Comfortably past the add-on's own ceiling on how long it
            # will hold the request, so a timeout here means something is
            # actually wrong rather than the hold working as intended.
            timeout=aiohttp.ClientTimeout(total=wait + 30),
        ) as resp:
            resp.raise_for_status()
            payload = await resp.json()
        if not isinstance(payload, dict) or "seq" not in payload:
            return None
        return payload

    async def _async_listen(self) -> None:
        """Keep the entities current without waiting for the next refresh.

        Latency used to be the sum of two independent polls — the add-on's
        and this one's — so a reading could be the better part of a minute
        old before any dashboard saw it. This holds a request open at the
        add-on instead, which collapses the second poll's share of that to
        nothing and sends fewer requests, not more.

        Listeners are notified directly rather than through
        async_set_updated_data, which reschedules the refresh: on a station
        where something changes every few seconds that would defer the
        periodic housekeeping — point discovery, alarms, repairs, trend
        imports — indefinitely.
        """
        backoff = LISTEN_BACKOFF_START
        while True:
            try:
                payload = await self._fetch_value_changes(LONG_POLL_SECONDS)
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                _LOGGER.debug("Value listener retrying after %s", err)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, LISTEN_BACKOFF_MAX)
                continue

            backoff = LISTEN_BACKOFF_START

            if payload is None:
                self._listener_supported = False
                _LOGGER.info(
                    "The add-on does not serve value changes; falling back to "
                    "polling every %ss. Update the add-on to match this "
                    "integration for faster updates.", self.scan_interval,
                )
                return

            self._value_seq = int(payload.get("seq") or 0)
            if self._apply_values(payload.get("values") or {}):
                self.async_update_listeners()

    def async_start_listener(self) -> None:
        """Begin listening for value changes, if not already."""
        if self._listener_task is not None and not self._listener_task.done():
            return
        self._listener_task = self.hass.async_create_background_task(
            self._async_listen(), name=f"{DOMAIN}-value-listener",
        )

    async def async_stop_listener(self) -> None:
        if self._listener_task is None:
            return
        self._listener_task.cancel()
        try:
            await self._listener_task
        except asyncio.CancelledError:
            pass
        self._listener_task = None

    def _clean_path_parts(self, path: str) -> list[str]:
        return [p for p in path.strip("/").split("/") if p not in SKIP_SEGMENTS]

    def get_group(self, point: NiagaraPoint) -> str:
        """Return the device group for a point (provided by the add-on)."""
        return point.group

    def device_entity_unique_ids(self) -> set[str]:
        """Unique ids of the device-level entities (climate, fan).

        The purge below keys on the point path, so without this every
        climate and fan entity is stale by definition and gets deleted on
        the first refresh after it is created. The platform modules own the
        rule for which templates qualify, so they are asked rather than
        having it restated here.
        """
        from .climate import CLIMATE_TEMPLATES
        from .fan import FAN_TEMPLATES

        ids: set[str] = set()
        for device in self.devices().values():
            for key, table in (("climate", CLIMATE_TEMPLATES), ("fan", FAN_TEMPLATES)):
                roles = table.get(device.device_type)
                if roles is None:
                    continue
                required = roles.get("current_temperature") or roles.get("running")
                if required and required not in device.slots:
                    continue
                ids.add(
                    f"niagara_{key}_{stable_id(self.host + '/' + device.group)}"
                )
        return ids

    def devices(self) -> dict[str, NiagaraDevice]:
        """Group published points into devices by template.

        Keyed on the point's own group, which is what entity.py uses to build
        the device registry entry, so a device-level entity lands on the same
        HA device as the sensors it is built from rather than creating a
        second one beside it.
        """
        found: dict[str, NiagaraDevice] = {}
        for point in self.points.values():
            if not point.device_type or not point.slot:
                continue
            group = point.group
            device = found.get(group)
            if device is None:
                device = NiagaraDevice(group=group, device_type=point.device_type)
                found[group] = device
            device.slots[point.slot] = point
        return found

    def get_area(self, point: NiagaraPoint) -> str | None:
        """The area a point's device belongs to.

        The add-on's rules win where one claims the folder. Falling back to
        a depth number is kept only because it is what every station had
        before rules existed — on its own it is close to useless, since the
        segment that names a place sits at a different depth in every
        branch. On the reference station it put 275 of 323 devices into one
        area named after the station.
        """
        if point.area:
            return point.area
        parts = self._clean_path_parts(point.path)
        if len(parts) > self.area_depth:
            return decode_niagara_name(parts[self.area_depth - 1])
        return None

    async def async_shutdown(self) -> None:
        """Clean up on unload."""
        await self.async_stop_listener()
        if self._session and not self._session.closed:
            await self._session.close()
