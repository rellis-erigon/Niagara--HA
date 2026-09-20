"""DataUpdateCoordinator for Niagara BMS."""

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import requests
import urllib3
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from requests.auth import HTTPBasicAuth

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
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

OBIX_NS = "http://obix.org/ns/schema/1.0"

WATCH_POINTS_PER_BATCH = 500
WATCH_LEASE_MULTIPLIER = 3

OBIX_UNIT_MAP = {
    "celsius": "°C",
    "fahrenheit": "°F",
    "percent": "%",
    "kilowatt": "kW",
    "watt": "W",
    "kilowatt_hour": "kWh",
    "watt_hour": "Wh",
    "volt": "V",
    "ampere": "A",
    "hertz": "Hz",
    "pascal": "Pa",
    "kilopascal": "kPa",
    "pounds_per_square_inch": "psi",
    "cubic_feet_per_minute": "cfm",
    "liters_per_second": "l/s",
    "gallons_per_minute": "gpm",
    "revolutions_per_minute": "rpm",
    "inches_of_water": "in. w.c.",
    "millibar": "mbar",
    "bar": "bar",
    "degree": "°",
    "cubic_meter": "m³",
    "cubic_meters_per_hour": "m³/h",
    "liter": "L",
    "liters_per_hour": "L/h",
    "gallon": "gal",
    "cubic_foot": "ft³",
    "megawatt_hour": "MWh",
    "gigajoule": "GJ",
    "megajoule": "MJ",
    "british_thermal_unit": "BTU",
    "cubic_feet_per_hour": "ft³/h",
    "kilowatt_hours": "kWh",
    "megawatt_hours": "MWh",
    "watt_hours": "Wh",
    "kilowatts": "kW",
    "watts": "W",
    "volts": "V",
    "amperes": "A",
    "amps": "A",
    "degrees_celsius": "°C",
    "degrees_fahrenheit": "°F",
}

SKIP_POINT_NAMES = frozenset({
    "stationName", "hostName", "hostId",
    "platformVersion", "niagaraVersion", "softwareVersion",
    "osName", "osVersion", "osArch",
    "vmName", "vmVersion", "vmVendor",
    "timeZoneId", "stationStartTime",
    "vendorName", "modelName", "serialNumber",
    "firmwareVersion", "hardwareVersion",
    "healthStatus", "health", "faultCause",
    "deviceName", "driverName",
    "pollFrequency", "pollEnabled", "pollRate",
    "tuningPolicyRef", "tuningPolicyName", "tuningPolicy",
    "proxyExt", "conversion", "deviceFacets",
    "facets", "icon", "href",
    "enabled", "overridden", "actions",
    "watchCount", "lease",
    "type", "is", "display", "displayName",
    "readValue", "writeValue",
    "subscriptionStatus", "pointId",
    "fallbackValue", "statusText",
    "priorityArray", "inAlarm", "ackState",
    "in1", "in2", "in3", "in4", "in5", "in6", "in7", "in8",
    "in9", "in10", "in11", "in12", "in13", "in14", "in15", "in16",
})


def _normalize_unit(raw: str) -> str | None:
    unit_name = raw.rsplit("/", 1)[-1] if "/" in raw else raw
    unit_name = unit_name.replace("obix:", "").strip()
    if not unit_name or unit_name.lower() == "null":
        return None
    key = unit_name.lower().replace(" ", "_")
    return OBIX_UNIT_MAP.get(unit_name, OBIX_UNIT_MAP.get(key, unit_name))


_NIAGARA_ESCAPE_RE = re.compile(r"\$([0-9a-fA-F]{2})")


def decode_niagara_name(name: str) -> str:
    return _NIAGARA_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), name)


def stable_id(path: str) -> str:
    return hashlib.md5(path.encode()).hexdigest()[:12]


@dataclass
class NiagaraPoint:
    path: str
    name: str
    value: str | None = None
    status: str | None = None
    display: str | None = None
    point_type: str = "unknown"
    unit: str | None = None
    writable: bool = False
    enum_range: list[str] = field(default_factory=list)


class ObixClient:
    """Synchronous oBIX REST client for Niagara 4."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 443,
        use_https: bool = True,
        verify_ssl: bool = False,
    ):
        scheme = "https" if use_https else "http"
        self._base_url = f"{scheme}://{host}:{port}/obix"
        self._session = requests.Session()
        self._session.auth = HTTPBasicAuth(username, password)
        self._session.verify = verify_ssl
        if not verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self._session.headers.update({"Accept": "text/xml", "Content-Type": "text/xml"})
        self._watch_uri: str | None = None
        self._watch_points: set[str] = set()
        self._has_watch_service = False
        self._has_batch_service = False

    def test_connection(self) -> bool:
        resp = self._session.get(f"{self._base_url}/about/", timeout=10)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        if root.tag == f"{{{OBIX_NS}}}err":
            raise ConnectionError(root.get("display", "oBIX error"))
        self._detect_services()
        return True

    def _detect_services(self) -> None:
        try:
            root = self._get("/")
            for child in root:
                name = child.get("name", "")
                if name == "watchService" or "WatchService" in child.get("is", ""):
                    self._has_watch_service = True
                if name == "batch":
                    self._has_batch_service = True
        except Exception:
            pass

    def _get(self, path: str) -> ET.Element:
        url = f"{self._base_url}{path}"
        resp = self._session.get(url, timeout=30)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        if root.tag == f"{{{OBIX_NS}}}err":
            raise ConnectionError(root.get("display", "oBIX error"))
        return root

    def _post(self, path: str, body: str) -> ET.Element:
        url = f"{self._base_url}{path}"
        resp = self._session.post(url, data=body, timeout=30)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        if root.tag == f"{{{OBIX_NS}}}err":
            raise ConnectionError(root.get("display", "oBIX error"))
        return root

    def _post_url(self, url: str, body: str) -> ET.Element:
        resp = self._session.post(url, data=body, timeout=30)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        if root.tag == f"{{{OBIX_NS}}}err":
            raise ConnectionError(root.get("display", "oBIX error"))
        return root

    # -- Watch lifecycle ---------------------------------------------------

    def _create_watch(self, poll_interval: int) -> bool:
        try:
            root = self._post("/watchService/make/", "<obj/>")
            watch_href = root.get("href")
            if not watch_href:
                return False
            if watch_href.startswith("http"):
                self._watch_uri = watch_href
            elif watch_href.startswith("/obix"):
                self._watch_uri = f"{self._base_url.rsplit('/obix', 1)[0]}{watch_href}"
            else:
                self._watch_uri = f"{self._base_url}{watch_href}"
            self._watch_uri = self._watch_uri.rstrip("/") + "/"
            lease_val = f"PT{poll_interval * WATCH_LEASE_MULTIPLIER}S"
            try:
                self._session.put(
                    f"{self._watch_uri}lease/",
                    data=f'<reltime val="{lease_val}"/>',
                    timeout=10,
                )
            except requests.RequestException:
                pass
            self._watch_points.clear()
            return True
        except Exception:
            return False

    def _watch_add(self, paths: list[str]) -> dict[str, dict]:
        if not self._watch_uri:
            return {}
        results: dict[str, dict] = {}
        for i in range(0, len(paths), WATCH_POINTS_PER_BATCH):
            batch = paths[i : i + WATCH_POINTS_PER_BATCH]
            uri_elems = "".join(f'<uri val="{p}"/>' for p in batch)
            body = f'<obj is="obix:WatchIn"><list name="hrefs">{uri_elems}</list></obj>'
            try:
                root = self._post_url(f"{self._watch_uri}add/", body)
                for parsed in self._extract_values(root):
                    results[parsed["path"]] = parsed
                self._watch_points.update(batch)
            except Exception as exc:
                _LOGGER.debug("Watch.add batch failed: %s", exc)
        return results

    def _watch_poll_changes(self) -> dict[str, dict]:
        if not self._watch_uri:
            return {}
        try:
            root = self._post_url(f"{self._watch_uri}pollChanges/", "<obj/>")
            return {p["path"]: p for p in self._extract_values(root)}
        except Exception as exc:
            if "not found" in str(exc).lower() or "expired" in str(exc).lower():
                self._watch_uri = None
                self._watch_points.clear()
            return {}

    def _watch_delete(self) -> None:
        if not self._watch_uri:
            return
        try:
            self._post_url(f"{self._watch_uri}delete/", "<obj/>")
        except Exception:
            pass
        self._watch_uri = None
        self._watch_points.clear()

    def _watch_remove(self, paths: list[str]) -> None:
        if not self._watch_uri or not paths:
            return
        uri_elems = "".join(f'<uri val="{p}"/>' for p in paths)
        body = f'<obj is="obix:WatchIn"><list name="hrefs">{uri_elems}</list></obj>'
        try:
            self._post_url(f"{self._watch_uri}remove/", body)
            self._watch_points -= set(paths)
        except Exception:
            pass

    def _extract_values(self, root: ET.Element) -> list[dict]:
        results = []
        values_list = root.find(f".//{{{OBIX_NS}}}list[@name='values']")
        if values_list is None:
            values_list = root.find(f".//{{{OBIX_NS}}}list")
        if values_list is not None:
            for child in values_list:
                href = child.get("href", "")
                if href:
                    results.append({
                        "path": href,
                        "value": child.get("val"),
                        "status": child.get("status", "ok"),
                    })
        return results

    # -- Discovery ---------------------------------------------------------

    def discover_points(self, path_filter: str = "") -> list[NiagaraPoint]:
        points: list[NiagaraPoint] = []
        start_path = "/config/"
        if path_filter:
            start_path = path_filter if path_filter.startswith("/") else f"/{path_filter}"
            if not start_path.endswith("/"):
                start_path += "/"
        self._walk_tree(start_path, points, depth=0, max_depth=10)
        _LOGGER.info("Discovered %d points", len(points))
        return points

    def _walk_tree(
        self, path: str, points: list[NiagaraPoint], depth: int, max_depth: int,
    ) -> None:
        if depth > max_depth:
            return
        try:
            root = self._get(path)
        except Exception:
            return

        value_tags = ("real", "bool", "int", "str", "enum", "abstime", "reltime")
        children_by_name: dict[str, tuple[ET.Element, str]] = {}
        refs: list[tuple[str, str]] = []

        for child in root:
            tag = child.tag.replace(f"{{{OBIX_NS}}}", "")
            href = child.get("href", "")
            name = child.get("name", "")
            if tag in ("ref", "list"):
                child_path = self._resolve_href(path, href)
                if child_path:
                    refs.append((child_path, name))
            elif tag in value_tags:
                children_by_name[name] = (child, tag)

        if "out" in children_by_name:
            out_elem, out_tag = children_by_name["out"]
            parent_name = path.rstrip("/").split("/")[-1]
            if parent_name not in SKIP_POINT_NAMES:
                point = self._parse_point(out_elem, out_tag, path, parent_name)
                if point:
                    point.path = path
                    point.name = parent_name
                    points.append(point)
        else:
            for name, (child, tag) in children_by_name.items():
                point = self._parse_point(child, tag, path, name)
                if point:
                    points.append(point)

        for child_path, ref_name in refs:
            if ref_name in SKIP_POINT_NAMES:
                continue
            self._walk_tree(child_path, points, depth + 1, max_depth)

    def _parse_point(
        self, elem: ET.Element, tag: str, parent_path: str, name: str,
    ) -> NiagaraPoint | None:
        if not name or name in SKIP_POINT_NAMES:
            return None
        if name.startswith(("pslot:", "slot:", "n:")):
            return None

        href = elem.get("href", "")
        full_path = self._resolve_href(parent_path, href) or f"{parent_path}{name}"
        type_map = {
            "real": "numeric", "bool": "boolean", "int": "integer",
            "str": "string", "enum": "enum", "abstime": "datetime", "reltime": "duration",
        }
        raw_unit = elem.get("unit")
        unit = _normalize_unit(raw_unit) if raw_unit else None
        enum_range: list[str] = []
        if tag == "enum":
            range_attr = elem.get("range")
            if range_attr:
                enum_range = self._fetch_enum_range(range_attr)

        return NiagaraPoint(
            path=full_path,
            name=name,
            value=elem.get("val"),
            status=elem.get("status", "ok"),
            display=elem.get("display", ""),
            point_type=type_map.get(tag, "unknown"),
            unit=unit,
            writable=elem.get("writable", "false") == "true",
            enum_range=enum_range,
        )

    def _fetch_enum_range(self, range_href: str) -> list[str]:
        try:
            root = self._get(range_href)
            return [
                child.get("val", child.get("name", ""))
                for child in root
                if child.tag.replace(f"{{{OBIX_NS}}}", "") == "obj"
            ]
        except Exception:
            return []

    def _resolve_href(self, base: str, href: str) -> str | None:
        if not href:
            return None
        if href.startswith("/"):
            return href
        if href.startswith("http"):
            return None
        return base.rstrip("/") + "/" + href.lstrip("./")

    # -- Polling -----------------------------------------------------------

    def setup_watch(self, points: list[NiagaraPoint], poll_interval: int) -> bool:
        if not self._has_watch_service:
            return False
        self._watch_delete()
        if not self._create_watch(poll_interval):
            return False
        paths = [pt.path for pt in points]
        initial = self._watch_add(paths)
        if not initial and paths:
            self._watch_delete()
            return False
        for pt in points:
            data = initial.get(pt.path)
            if data:
                pt.value = data["value"]
                pt.status = data.get("status", "ok")
        return True

    def poll_watch(self, points: list[NiagaraPoint]) -> dict[str, dict]:
        current_paths = {pt.path for pt in points}
        new_paths = current_paths - self._watch_points
        removed_paths = self._watch_points - current_paths
        if new_paths:
            self._watch_add(list(new_paths))
        if removed_paths:
            self._watch_remove(list(removed_paths))
        return self._watch_poll_changes()

    def poll_legacy(self, points: list[NiagaraPoint]) -> dict[str, dict]:
        results: dict[str, dict] = {}
        for pt in points:
            try:
                root = self._get(pt.path)
                tag = root.tag.replace(f"{{{OBIX_NS}}}", "")
                if tag in ("real", "bool", "int", "str", "enum"):
                    results[pt.path] = {
                        "path": pt.path,
                        "value": root.get("val"),
                        "status": root.get("status", "ok"),
                    }
                else:
                    for vtag in ("real", "bool", "int", "str", "enum"):
                        out = root.find(f".//{{{OBIX_NS}}}{vtag}[@name='out']")
                        if out is not None:
                            results[pt.path] = {
                                "path": pt.path,
                                "value": out.get("val"),
                                "status": out.get("status", "ok"),
                            }
                            break
            except Exception:
                pass
        return results

    @property
    def has_watch(self) -> bool:
        return self._watch_uri is not None

    def close(self) -> None:
        self._watch_delete()
        self._session.close()


class NiagaraCoordinator(DataUpdateCoordinator[dict[str, NiagaraPoint]]):
    """Coordinator that polls a Niagara station and serves point data to entities."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        scan_interval = entry.options.get(
            "scan_interval", entry.data.get("scan_interval", DEFAULT_POLL_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = ObixClient(
            host=entry.data[CONF_HOST],
            username=entry.data[CONF_USERNAME],
            password=entry.data[CONF_PASSWORD],
            port=entry.data[CONF_PORT],
            use_https=entry.data.get(CONF_USE_HTTPS, True),
            verify_ssl=entry.data.get(CONF_VERIFY_SSL, False),
        )
        self.points: dict[str, NiagaraPoint] = {}
        self._watch_active = False
        self.device_name: str = entry.options.get(
            CONF_DEVICE_NAME, entry.data.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME)
        )
        self.device_depth: int = entry.options.get(
            CONF_DEVICE_DEPTH, entry.data.get(CONF_DEVICE_DEPTH, DEFAULT_DEVICE_DEPTH)
        )
        self.area_depth: int = entry.options.get(
            CONF_AREA_DEPTH, entry.data.get(CONF_AREA_DEPTH, DEFAULT_AREA_DEPTH)
        )
        self.host: str = entry.data[CONF_HOST]
        self.device_folders: list[str] = []
        self._store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.device_folders")

    async def async_setup(self) -> None:
        stored = await self._store.async_load()
        if stored and isinstance(stored, dict):
            self.device_folders = stored.get("folders", [])

        path_filter = self.entry.data.get(CONF_POINT_FILTER, "")
        discovered = await self.hass.async_add_executor_job(
            self.client.discover_points, path_filter
        )
        self.points = {pt.path: pt for pt in discovered}

        if not self.device_folders and self.device_depth == 0:
            self.device_depth = self._auto_device_depth()
            _LOGGER.info("Auto-detected device_depth=%d", self.device_depth)

        scan_interval = self.entry.options.get(
            "scan_interval", self.entry.data.get("scan_interval", DEFAULT_POLL_INTERVAL)
        )
        point_list = list(self.points.values())
        self._watch_active = await self.hass.async_add_executor_job(
            self.client.setup_watch, point_list, scan_interval
        )
        _LOGGER.info(
            "Niagara setup: %d points, %d devices, %d device folders, watch=%s",
            len(self.points),
            len({self.get_group(pt) for pt in self.points.values()}),
            len(self.device_folders),
            self._watch_active,
        )

    async def async_save_device_folders(self) -> None:
        await self._store.async_save({"folders": self.device_folders})

    async def async_toggle_device_folder(self, folder: str) -> str:
        if folder in self.device_folders:
            self.device_folders.remove(folder)
            action = "removed"
        else:
            self.device_folders.append(folder)
            action = "added"
        await self.async_save_device_folders()
        return action

    async def _async_update_data(self) -> dict[str, NiagaraPoint]:
        try:
            point_list = list(self.points.values())
            if self._watch_active and self.client.has_watch:
                changes = await self.hass.async_add_executor_job(
                    self.client.poll_watch, point_list
                )
            else:
                changes = await self.hass.async_add_executor_job(
                    self.client.poll_legacy, point_list
                )
            for path, data in changes.items():
                if path in self.points:
                    self.points[path].value = data["value"]
                    self.points[path].status = data.get("status", "ok")
            return self.points
        except Exception as exc:
            raise UpdateFailed(f"Error polling Niagara: {exc}") from exc

    async def async_shutdown(self) -> None:
        await self.hass.async_add_executor_job(self.client.close)

    _SKIP_SEGMENTS = frozenset({
        "config", "Drivers", "NiagaraNetwork", "ObixNetwork",
        "points", "out", "exports", "obix", "",
    })

    def _clean_path_parts(self, path: str) -> list[str]:
        return [p for p in path.strip("/").split("/") if p not in self._SKIP_SEGMENTS]

    def _auto_device_depth(self) -> int:
        """Find the shallowest depth that produces a reasonable number of devices.

        Targets between 20-200 unique groups. Walks depths 1..6 and picks
        the first that lands in range, or the one closest to 100.
        """
        if not self.points:
            return 3

        all_parts = [self._clean_path_parts(pt.path) for pt in self.points.values()]
        best_depth = 3
        best_distance = float("inf")

        for depth in range(1, 7):
            groups: set[str] = set()
            for parts in all_parts:
                if len(parts) < 2:
                    groups.add("Ungrouped")
                else:
                    groups.add("/".join(parts[:depth]))
            count = len(groups)
            if 20 <= count <= 200:
                _LOGGER.debug("device_depth=%d gives %d devices (in range)", depth, count)
                return depth
            dist = abs(count - 100)
            if dist < best_distance:
                best_distance = dist
                best_depth = depth
            _LOGGER.debug("device_depth=%d gives %d devices", depth, count)

        return best_depth

    def get_group(self, point: NiagaraPoint) -> str:
        parts = self._clean_path_parts(point.path)
        if len(parts) < 2:
            return "Ungrouped"
        point_path = "/".join(parts[:-1])
        if self.device_folders:
            best = ""
            for folder in self.device_folders:
                if point_path == folder or point_path.startswith(folder + "/"):
                    if len(folder) > len(best):
                        best = folder
            if best:
                return best
        return "/".join(parts[: self.device_depth])

    def get_area(self, point: NiagaraPoint) -> str | None:
        parts = self._clean_path_parts(point.path)
        if self.area_depth > 0 and len(parts) >= self.area_depth:
            return decode_niagara_name(parts[self.area_depth - 1])
        return None
