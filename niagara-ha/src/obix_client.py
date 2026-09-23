"""oBIX client for Niagara 4 stations.

Connects over HTTP/HTTPS using Basic auth and reads/writes points
via the oBIX REST interface (XML over HTTP).

Supports oBIX Watches for efficient change-only polling (1 request per cycle
instead of N individual GETs) and Batch reads for bulk operations.
"""

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

import requests
import urllib3
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)

OBIX_NS = "http://obix.org/ns/schema/1.0"
NS = {"o": OBIX_NS}

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
    "minute": "min",
    "minutes": "min",
    "hour": "h",
    "second": "s",
    "kilovolt_ampere": "kVA",
    "kilovolt_amperes": "kVA",
    "volt_ampere": "VA",
    "kilovolt_ampere_reactive": "kvar",
}


def _normalize_unit(raw: str) -> str | None:
    unit_name = raw.rsplit("/", 1)[-1] if "/" in raw else raw
    unit_name = unit_name.replace("obix:", "").strip()
    if not unit_name or unit_name.lower() == "null":
        return None
    key = unit_name.lower().replace(" ", "_")
    return OBIX_UNIT_MAP.get(unit_name, OBIX_UNIT_MAP.get(key, unit_name))


# oBIX value-object tags. Anything else in a Watch values list (notably <err>)
# is not a reading and must not be counted as one.
WATCH_VALUE_TAGS = frozenset({
    "real", "int", "bool", "str", "enum",
    "abstime", "reltime", "date", "time", "uri",
})

# If more than this fraction of subscribed points come back rejected, the Watch
# service is not usable for this station and legacy polling is used instead.
WATCH_REJECT_THRESHOLD = 0.5

# Niagara reports a point's status inside its display string as a brace
# group — "22.7 °C {ok}", "0.00 °C {stale}" — and leaves the oBIX status
# attribute empty. Reading only that attribute made every point look healthy,
# including dozens of rooms frozen at 0.0 °C because their BACnet proxy had
# stopped updating.
_DISPLAY_STATUS_RE = re.compile(r"\{([^}]*)\}")

# Ordered worst-first: a point can carry several flags at once.
_STATUS_PRECEDENCE = (
    "fault", "down", "disabled", "stale", "overridden", "alarm", "ok",
)


def _status_from_display(display: str) -> Optional[str]:
    """Extract the worst status flag Niagara put in a display string."""
    if not display:
        return None
    match = _DISPLAY_STATUS_RE.search(display)
    if not match:
        return None
    flags = {f.strip().lower() for f in match.group(1).split(",") if f.strip()}
    if not flags:
        return None
    for candidate in _STATUS_PRECEDENCE:
        if candidate in flags:
            return candidate
    return sorted(flags)[0]


# Niagara facets carry the station's own display precision, e.g.
# "units=u:celsius;°C;(K);+273.15;|precision=i:1". Without it a float32 value
# reaches Home Assistant as 22.700000762939453.
_PRECISION_RE = re.compile(r"precision=i:(\d+)")


def _precision_from_facets(facets: str) -> Optional[int]:
    if not facets:
        return None
    match = _PRECISION_RE.search(facets)
    if not match:
        return None
    try:
        precision = int(match.group(1))
    except ValueError:
        return None
    return precision if 0 <= precision <= 6 else None


# Niagara distinguishes a commandable point from a read-only one by its
# contract, not by an oBIX writable attribute — control:NumericWritable
# against control:NumericPoint. No point on this station carries a writable
# attribute at all, so reading only that marked every point writable.
_WRITABLE_CONTRACT = "Writable"


def _is_writable(contract: str | None, elem: ET.Element) -> bool:
    if elem.get("writable") == "true":
        return True
    return _WRITABLE_CONTRACT in (contract or "")


SKIP_POINT_NAMES = frozenset({
    # Station metadata
    "stationName", "hostName", "hostId",
    "platformVersion", "niagaraVersion", "softwareVersion",
    "osName", "osVersion", "osArch",
    "vmName", "vmVersion", "vmVendor",
    "timeZoneId", "stationStartTime",
    # Device/driver metadata
    "vendorName", "modelName", "serialNumber",
    "firmwareVersion", "hardwareVersion",
    "healthStatus", "health", "faultCause",
    "deviceName", "driverName",
    # Polling/tuning config
    "pollFrequency", "pollEnabled", "pollRate",
    "tuningPolicyRef", "tuningPolicyName", "tuningPolicy",
    # Point internal properties
    "proxyExt", "conversion", "deviceFacets",
    "facets", "icon", "href",
    "enabled", "overridden", "actions",
    "watchCount", "lease",
    "type", "is", "display", "displayName",
    # Point sub-properties (metadata, not real BMS values)
    "readValue", "writeValue",
    "subscriptionStatus", "pointId",
    "fallbackValue", "statusText",
    "priorityArray", "inAlarm", "ackState",
    # Priority array inputs
    "in1", "in2", "in3", "in4", "in5", "in6", "in7", "in8",
    "in9", "in10", "in11", "in12", "in13", "in14", "in15", "in16",
})


@dataclass
class NiagaraPoint:
    path: str
    name: str
    value: Optional[str] = None
    status: Optional[str] = None
    display: Optional[str] = None
    point_type: str = "unknown"
    unit: Optional[str] = None
    writable: bool = False
    precision: Optional[int] = None
    enum_range: list[str] = field(default_factory=list)


class ObixError(Exception):
    pass


class ObixClient:
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
        # The server-root path the oBIX service is mounted at.
        self._obix_prefix = "/obix"
        self._auth = HTTPBasicAuth(username, password)
        self._session = requests.Session()
        self._session.auth = self._auth
        self._session.verify = verify_ssl
        if not verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self._session.headers.update({"Accept": "text/xml", "Content-Type": "text/xml"})
        self._connected = False
        self._watch_uri: Optional[str] = None
        self._watch_points: set[str] = set()
        self._has_watch_service = False
        self._has_batch_service = False
        self._consecutive_full_failures = 0
        self._watch_rejected = 0

    @property
    def connected(self) -> bool:
        return self._connected

    def test_connection(self) -> bool:
        try:
            resp = self._session.get(f"{self._base_url}/about/", timeout=10)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            if root.tag == f"{{{OBIX_NS}}}err":
                raise ObixError(root.get("display", "Unknown oBIX error"))
            self._connected = True
            logger.info("Connected to Niagara at %s", self._base_url)
            self._detect_services()
            return True
        except requests.RequestException as e:
            self._connected = False
            logger.error("Connection failed: %s", e)
            return False

    def _detect_services(self) -> None:
        try:
            root = self._get("/")
            for child in root:
                tag = child.tag.replace(f"{{{OBIX_NS}}}", "")
                name = child.get("name", "")
                if name == "watchService" or "WatchService" in child.get("is", ""):
                    self._has_watch_service = True
                if name == "batch":
                    self._has_batch_service = True
            logger.info(
                "oBIX services: Watch=%s, Batch=%s",
                self._has_watch_service, self._has_batch_service,
            )
        except (ObixError, requests.RequestException) as e:
            logger.debug("Could not detect services from lobby: %s", e)

    # -- Low-level HTTP ------------------------------------------------

    def _obix_href(self, path: str) -> str:
        """Stored path to the href the oBIX server names it by.

        Points are stored relative to the oBIX root ("/config/..."), because
        every GET is base_url + path and base_url already ends in /obix. A
        Watch URI is resolved against the server root instead, so it needs the
        prefix — without it Niagara rejects every subscription with BadUriErr,
        which is what made the Watch look unsupported here.
        """
        if path.startswith(self._obix_prefix):
            return path
        return self._obix_prefix + path

    def _stored_path(self, href: str) -> str:
        """The inverse: an href from a Watch response back to a stored path."""
        for prefix in (f"{self._base_url}", self._obix_prefix):
            if href.startswith(prefix):
                return href[len(prefix):] or "/"
        return href

    def _get(self, path: str) -> ET.Element:
        url = f"{self._base_url}{path}"
        resp = self._session.get(url, timeout=30)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        if root.tag == f"{{{OBIX_NS}}}err":
            raise ObixError(root.get("display", "Unknown oBIX error"))
        return root

    def _post(self, path: str, body: str) -> ET.Element:
        url = f"{self._base_url}{path}"
        resp = self._session.post(url, data=body, timeout=30)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        if root.tag == f"{{{OBIX_NS}}}err":
            raise ObixError(root.get("display", "Unknown oBIX error"))
        return root

    def _post_url(self, url: str, body: str) -> ET.Element:
        resp = self._session.post(url, data=body, timeout=30)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        if root.tag == f"{{{OBIX_NS}}}err":
            raise ObixError(root.get("display", "Unknown oBIX error"))
        return root

    # -- Watch Service -------------------------------------------------

    def _create_watch(self, poll_interval: int = 30) -> bool:
        try:
            root = self._post("/watchService/make/", '<obj/>')
            watch_href = root.get("href")
            if not watch_href:
                logger.warning("Watch created but no href returned")
                return False

            if watch_href.startswith("http"):
                self._watch_uri = watch_href
            elif watch_href.startswith("/obix"):
                self._watch_uri = f"{self._base_url.rsplit('/obix', 1)[0]}{watch_href}"
            else:
                self._watch_uri = f"{self._base_url}{watch_href}"

            self._watch_uri = self._watch_uri.rstrip("/") + "/"

            lease_seconds = poll_interval * WATCH_LEASE_MULTIPLIER
            lease_val = f"PT{lease_seconds}S"
            try:
                self._session.put(
                    f"{self._watch_uri}lease/",
                    data=f'<reltime val="{lease_val}"/>',
                    timeout=10,
                )
            except requests.RequestException:
                pass

            self._watch_points.clear()
            logger.info("Created oBIX Watch at %s (lease=%s)", watch_href, lease_val)
            return True
        except (ObixError, requests.RequestException) as e:
            logger.warning("Failed to create Watch: %s", e)
            return False

    def _watch_add(self, paths: list[str]) -> dict[str, dict]:
        if not self._watch_uri:
            return {}

        results = {}
        for i in range(0, len(paths), WATCH_POINTS_PER_BATCH):
            batch = paths[i:i + WATCH_POINTS_PER_BATCH]
            uri_elements = "".join(
                f'<uri val="{self._obix_href(p)}"/>' for p in batch
            )
            body = f'<obj is="obix:WatchIn"><list name="hrefs">{uri_elements}</list></obj>'

            try:
                root = self._post_url(f"{self._watch_uri}add/", body)
                values_list = root.find(f".//{{{OBIX_NS}}}list[@name='values']")
                if values_list is None:
                    values_list = root.find(f".//{{{OBIX_NS}}}list")
                if values_list is not None:
                    for child in values_list:
                        parsed = self._parse_watch_element(child)
                        if parsed:
                            results[parsed["path"]] = parsed
                self._watch_points.update(batch)
                logger.debug("Watch.add: added %d points (batch %d)", len(batch), i // WATCH_POINTS_PER_BATCH + 1)
            except (ObixError, requests.RequestException) as e:
                logger.warning("Watch.add failed for batch %d: %s", i // WATCH_POINTS_PER_BATCH + 1, e)

        return results

    def _watch_poll_changes(self) -> dict[str, dict]:
        if not self._watch_uri:
            return {}

        try:
            root = self._post_url(f"{self._watch_uri}pollChanges/", '<obj/>')
            results = {}
            values_list = root.find(f".//{{{OBIX_NS}}}list[@name='values']")
            if values_list is None:
                values_list = root.find(f".//{{{OBIX_NS}}}list")
            if values_list is not None:
                for child in values_list:
                    parsed = self._parse_watch_element(child)
                    if parsed:
                        results[parsed["path"]] = parsed
            return results
        except (ObixError, requests.RequestException) as e:
            logger.warning("Watch.pollChanges failed: %s", e)
            if "not found" in str(e).lower() or "expired" in str(e).lower():
                logger.info("Watch appears expired — will recreate")
                self._watch_uri = None
                self._watch_points.clear()
            return {}

    def _watch_poll_refresh(self) -> dict[str, dict]:
        if not self._watch_uri:
            return {}

        try:
            root = self._post_url(f"{self._watch_uri}pollRefresh/", '<obj/>')
            results = {}
            values_list = root.find(f".//{{{OBIX_NS}}}list[@name='values']")
            if values_list is None:
                values_list = root.find(f".//{{{OBIX_NS}}}list")
            if values_list is not None:
                for child in values_list:
                    parsed = self._parse_watch_element(child)
                    if parsed:
                        results[parsed["path"]] = parsed
            return results
        except (ObixError, requests.RequestException) as e:
            logger.warning("Watch.pollRefresh failed: %s", e)
            return {}

    def _watch_delete(self) -> None:
        if not self._watch_uri:
            return
        try:
            self._post_url(f"{self._watch_uri}delete/", '<obj/>')
            logger.debug("Watch deleted")
        except (ObixError, requests.RequestException):
            pass
        self._watch_uri = None
        self._watch_points.clear()

    def _parse_watch_element(self, elem: ET.Element) -> Optional[dict]:
        """Parse one element of a Watch values list.

        Niagara returns an <err> element for every URI its Watch service
        cannot resolve, mixed into the same list as real values. Those carry
        no val attribute, so they must be rejected here — counting them as
        values makes a completely failed Watch look like a working one.

        The href Niagara returns matches the path we subscribed with, so it
        is used as the key unchanged.
        """
        tag = elem.tag.replace(f"{{{OBIX_NS}}}", "")
        href = elem.get("href", "")
        if not href:
            return None

        if tag == "err":
            self._watch_rejected += 1
            logger.debug(
                "Watch rejected %s: %s",
                href, elem.get("is") or elem.get("display", "unknown error"),
            )
            return None

        if tag not in WATCH_VALUE_TAGS:
            return None

        val = elem.get("val")
        if val is None:
            return None

        return {
            "path": self._stored_path(href),
            "value": val,
            "status": elem.get("status", "ok"),
        }

    # -- Batch reads ---------------------------------------------------

    def batch_read(self, paths: list[str]) -> dict[str, dict]:
        if not paths:
            return {}

        results = {}
        for i in range(0, len(paths), WATCH_POINTS_PER_BATCH):
            batch = paths[i:i + WATCH_POINTS_PER_BATCH]
            uri_elements = "".join(
                f'<uri is="obix:Read" val="{p}"/>' for p in batch
            )
            body = f'<obj is="obix:BatchIn"><list>{uri_elements}</list></obj>'

            try:
                root = self._post("/batch/", body)
                batch_list = root.find(f".//{{{OBIX_NS}}}list")
                if batch_list is None:
                    for idx, child in enumerate(root):
                        if idx < len(batch):
                            parsed = self._parse_watch_element(child)
                            if parsed:
                                results[parsed["path"]] = parsed
                            else:
                                child_tag = child.tag.replace(f"{{{OBIX_NS}}}", "")
                                if child_tag in ("real", "bool", "int", "str", "enum"):
                                    results[batch[idx]] = {
                                        "path": batch[idx],
                                        "value": child.get("val"),
                                        "status": child.get("status", "ok"),
                                    }
                else:
                    for idx, child in enumerate(batch_list):
                        parsed = self._parse_watch_element(child)
                        if parsed:
                            results[parsed["path"]] = parsed
                        elif idx < len(batch):
                            child_tag = child.tag.replace(f"{{{OBIX_NS}}}", "")
                            if child_tag in ("real", "bool", "int", "str", "enum"):
                                results[batch[idx]] = {
                                    "path": batch[idx],
                                    "value": child.get("val"),
                                    "status": child.get("status", "ok"),
                                }
            except (ObixError, requests.RequestException) as e:
                logger.warning("Batch read failed for chunk %d: %s", i // WATCH_POINTS_PER_BATCH + 1, e)

        return results

    # -- Discovery -----------------------------------------------------

    def discover_points(self, path_filter: str = "") -> list[NiagaraPoint]:
        points = []
        start_path = "/config/"
        if path_filter:
            start_path = path_filter if path_filter.startswith("/") else f"/{path_filter}"
            if not start_path.endswith("/"):
                start_path += "/"
        try:
            self._walk_tree(start_path, points, depth=0, max_depth=10)
        except ObixError as e:
            logger.error("Discovery error at %s: %s", start_path, e)
        except requests.RequestException as e:
            logger.error("HTTP error during discovery: %s", e)
        logger.info("Discovered %d points", len(points))
        return points

    def _walk_tree(
        self, path: str, points: list[NiagaraPoint], depth: int, max_depth: int
    ) -> None:
        if depth > max_depth:
            return
        try:
            root = self._get(path)
        except (ObixError, requests.RequestException) as e:
            logger.debug("Skipping %s: %s", path, e)
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

        contract = root.get("is") or ""
        facets = ""
        child_names = set()
        for child in root:
            name_attr = child.get("name") or ""
            child_names.add(name_attr)
            if name_attr == "facets":
                facets = child.get("val", "") or ""

        # Niagara hangs history extensions off a point as child folders
        # (BooleanCov, NumericInterval and friends). Their contents are
        # logging configuration — capacity, interval, fullPolicy, timeZone —
        # not building values, and they outnumbered the real points on this
        # station 6:1. A folder carrying historyConfig or historyName is one
        # of those, whatever it is named.
        if child_names & {"historyConfig", "historyName"}:
            logger.debug("Skipping history extension at %s", path)
            return

        if "out" in children_by_name:
            out_elem, out_tag = children_by_name["out"]
            parent_name = path.rstrip("/").split("/")[-1]
            if parent_name not in SKIP_POINT_NAMES:
                point = self._parse_point(out_elem, out_tag, path, parent_name)
                if point:
                    point.path = path
                    point.name = parent_name
                    point.precision = _precision_from_facets(facets)
                    point.writable = _is_writable(contract, out_elem)
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
    ) -> Optional[NiagaraPoint]:
        if not name:
            return None

        if name in SKIP_POINT_NAMES:
            return None
        if name.startswith(("pslot:", "slot:", "n:")):
            return None

        href = elem.get("href", "")
        full_path = self._resolve_href(parent_path, href) or f"{parent_path}{name}"

        type_map = {
            "real": "numeric",
            "bool": "boolean",
            "int": "integer",
            "str": "string",
            "enum": "enum",
            "abstime": "datetime",
            "reltime": "duration",
        }

        val = elem.get("val")
        display = elem.get("display", "")
        status = _status_from_display(display) or elem.get("status") or "ok"
        raw_unit = elem.get("unit")
        unit = _normalize_unit(raw_unit) if raw_unit else None
        writable = elem.get("writable", "false") == "true"

        enum_range = []
        if tag == "enum":
            range_attr = elem.get("range")
            if range_attr:
                enum_range = self._fetch_enum_range(range_attr)

        return NiagaraPoint(
            path=full_path,
            name=name,
            value=val,
            status=status,
            display=display,
            point_type=type_map.get(tag, "unknown"),
            unit=unit,
            writable=writable,
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
        except (ObixError, requests.RequestException):
            return []

    def _resolve_href(self, base: str, href: str) -> Optional[str]:
        if not href:
            return None
        if href.startswith("/"):
            return href
        if href.startswith("http"):
            return None
        return base.rstrip("/") + "/" + href.lstrip("./")

    # -- Point reading (single) ----------------------------------------

    def read_point(self, path: str) -> Optional[NiagaraPoint]:
        try:
            root = self._get(path)
        except (ObixError, requests.RequestException) as e:
            logger.warning("Failed to read %s: %s", path, e)
            return None

        tag = root.tag.replace(f"{{{OBIX_NS}}}", "")
        name = root.get("name", path.rstrip("/").split("/")[-1])
        contract = root.get("is") or ""

        if tag in ("real", "bool", "int", "str", "enum", "abstime", "reltime"):
            point = self._parse_point(
                root, tag, "/".join(path.split("/")[:-1]) + "/", name,
            )
            if point:
                # _parse_point derives a path from the element's href, falling
                # back to parent_path + name — which doubles the last segment
                # when the element carries no href. The path we were asked to
                # read is authoritative, exactly as in the "out" branch below.
                point.path = path
                point.writable = _is_writable(contract, root)
            return point

        out_elem = root.find(f".//{{{OBIX_NS}}}real[@name='out']")
        if out_elem is None:
            out_elem = root.find(f".//{{{OBIX_NS}}}bool[@name='out']")
        if out_elem is None:
            out_elem = root.find(f".//{{{OBIX_NS}}}enum[@name='out']")
        if out_elem is None:
            out_elem = root.find(f".//{{{OBIX_NS}}}int[@name='out']")
        if out_elem is None:
            out_elem = root.find(f".//{{{OBIX_NS}}}str[@name='out']")

        if out_elem is not None:
            out_tag = out_elem.tag.replace(f"{{{OBIX_NS}}}", "")
            point = self._parse_point(out_elem, out_tag, path, name)
            if point:
                point.path = path
                point.writable = _is_writable(contract, out_elem)
            return point

        return None

    def read_point_value(self, path: str) -> Optional[str]:
        point = self.read_point(path)
        return point.value if point else None

    # -- Polling (Watch-based or legacy) -------------------------------

    def setup_watch(self, points: list[NiagaraPoint], poll_interval: int = 30) -> bool:
        if not self._has_watch_service:
            logger.info("Watch service not available — using legacy polling")
            return False

        self._watch_delete()

        if not self._create_watch(poll_interval):
            return False

        paths = [pt.path for pt in points]
        self._watch_rejected = 0
        initial = self._watch_add(paths)
        rejected = self._watch_rejected

        if paths and rejected:
            logger.warning(
                "Watch rejected %d of %d points (station returned BadUriErr or "
                "similar) — %d usable values",
                rejected, len(paths), len(initial),
            )

        if paths and (not initial or rejected >= len(paths) * WATCH_REJECT_THRESHOLD):
            logger.warning(
                "Watch unusable for this station (%d/%d rejected) — "
                "falling back to legacy polling",
                rejected, len(paths),
            )
            self._watch_delete()
            return False

        for pt in points:
            data = initial.get(pt.path)
            if data:
                pt.value = data["value"]
                pt.status = data.get("status", "ok")

        logger.info(
            "Watch active: %d points subscribed, %d initial values",
            len(self._watch_points), len(initial),
        )
        return True

    def poll_points(
        self, points: list[NiagaraPoint], max_workers: int = 5,
    ) -> list[NiagaraPoint]:
        if not points:
            return []

        if self._watch_uri:
            return self._poll_via_watch(points)

        return self._poll_via_legacy(points, max_workers)

    def _poll_via_watch(self, points: list[NiagaraPoint]) -> list[NiagaraPoint]:
        current_paths = {pt.path for pt in points}
        new_paths = current_paths - self._watch_points
        removed_paths = self._watch_points - current_paths

        if new_paths:
            self._watch_add(list(new_paths))
        if removed_paths:
            self._watch_remove(list(removed_paths))

        changes = self._watch_poll_changes()

        if changes is None:
            logger.error("Watch poll returned None — marking connection lost")
            self._connected = False
            return points

        for pt in points:
            data = changes.get(pt.path)
            if data:
                pt.value = data["value"]
                pt.status = data.get("status", "ok")

        return points

    def _watch_remove(self, paths: list[str]) -> None:
        if not self._watch_uri or not paths:
            return
        uri_elements = "".join(
            f'<uri val="{self._obix_href(p)}"/>' for p in paths
        )
        body = f'<obj is="obix:WatchIn"><list name="hrefs">{uri_elements}</list></obj>'
        try:
            self._post_url(f"{self._watch_uri}remove/", body)
            self._watch_points -= set(paths)
        except (ObixError, requests.RequestException) as e:
            logger.debug("Watch.remove failed: %s", e)

    def _poll_via_legacy(
        self, points: list[NiagaraPoint], max_workers: int,
    ) -> list[NiagaraPoint]:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results: dict[str, NiagaraPoint] = {}
        fail_count = 0

        def _read_one(pt: NiagaraPoint) -> tuple[str, NiagaraPoint, bool]:
            try:
                refreshed = self.read_point(pt.path)
                if refreshed:
                    # The path we polled is the identity of this point. A
                    # refreshed copy must never redefine it, or the next cycle
                    # polls a path that does not exist and fails permanently.
                    refreshed.path = pt.path
                    return pt.path, refreshed, True
            except Exception as e:
                logger.debug("Poll error for %s: %s", pt.path, e)
            pt.status = "fault"
            return pt.path, pt, False

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_read_one, pt): pt for pt in points}
            for future in as_completed(futures):
                try:
                    path, point, ok = future.result()
                    results[path] = point
                    if not ok:
                        fail_count += 1
                except Exception:
                    pt = futures[future]
                    pt.status = "fault"
                    results[pt.path] = pt
                    fail_count += 1

        if fail_count == len(points):
            self._consecutive_full_failures += 1
            if self._consecutive_full_failures >= 3:
                logger.error("All %d point reads failed %d times in a row — marking connection lost", len(points), self._consecutive_full_failures)
                self._connected = False
                self._consecutive_full_failures = 0
            else:
                logger.warning("All %d point reads failed (attempt %d/3 before reconnect)", len(points), self._consecutive_full_failures)
        else:
            self._consecutive_full_failures = 0
            if fail_count > 0:
                logger.warning("Poll: %d of %d reads failed", fail_count, len(points))

        return [results[pt.path] for pt in points if pt.path in results]

    # -- Lifecycle -----------------------------------------------------

    def close(self) -> None:
        self._watch_delete()
        self._session.close()
        self._connected = False
