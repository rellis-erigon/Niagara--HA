"""oBIX client for Niagara 4 stations.

Connects over HTTP/HTTPS using Basic auth and reads/writes points
via the oBIX REST interface (XML over HTTP).
"""

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote

import requests
import urllib3
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)

OBIX_NS = "http://obix.org/ns/schema/1.0"
NS = {"o": OBIX_NS}

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
    "pollFrequency", "pollEnabled",
    "tuningPolicyRef", "proxyExt",
    "facets", "icon", "href",
    "enabled", "overridden", "actions",
    "watchCount", "lease", "pollRate",
    "type", "is", "display", "displayName",
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
        self._auth = HTTPBasicAuth(username, password)
        self._session = requests.Session()
        self._session.auth = self._auth
        self._session.verify = verify_ssl
        if not verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self._session.headers.update({"Accept": "text/xml", "Content-Type": "text/xml"})
        self._connected = False

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
            return True
        except requests.RequestException as e:
            self._connected = False
            logger.error("Connection failed: %s", e)
            return False

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

    def discover_points(self, path_filter: str = "") -> list[NiagaraPoint]:
        """Walk the oBIX point tree and return all discoverable points."""
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

        for child in root:
            tag = child.tag.replace(f"{{{OBIX_NS}}}", "")
            href = child.get("href", "")
            name = child.get("name", "")
            display = child.get("display", "")

            if tag in ("ref", "list"):
                child_path = self._resolve_href(path, href)
                if child_path:
                    self._walk_tree(child_path, points, depth + 1, max_depth)
            elif tag in ("real", "bool", "int", "str", "enum", "abstime", "reltime"):
                point = self._parse_point(child, tag, path, name)
                if point:
                    points.append(point)

    def _parse_point(
        self, elem: ET.Element, tag: str, parent_path: str, name: str
    ) -> Optional[NiagaraPoint]:
        if not name:
            return None

        href = elem.get("href", "")
        full_path = self._resolve_href(parent_path, href) or f"{parent_path}{name}"

        if "/exports/" not in full_path:
            return None

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
        status = elem.get("status", "ok")
        display = elem.get("display", "")
        unit = elem.get("unit")
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

    def read_point(self, path: str) -> Optional[NiagaraPoint]:
        try:
            root = self._get(path)
        except (ObixError, requests.RequestException) as e:
            logger.warning("Failed to read %s: %s", path, e)
            return None

        tag = root.tag.replace(f"{{{OBIX_NS}}}", "")
        name = root.get("name", path.rstrip("/").split("/")[-1])

        if tag in ("real", "bool", "int", "str", "enum", "abstime", "reltime"):
            return self._parse_point(root, tag, "/".join(path.split("/")[:-1]) + "/", name)

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
            return point

        return None

    def read_point_value(self, path: str) -> Optional[str]:
        point = self.read_point(path)
        return point.value if point else None

    def poll_points(
        self, points: list[NiagaraPoint], max_workers: int = 5,
    ) -> list[NiagaraPoint]:
        """Re-read the current value of each point using concurrent requests."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        if not points:
            return []

        results: dict[str, NiagaraPoint] = {}
        fail_count = 0

        def _read_one(pt: NiagaraPoint) -> tuple[str, NiagaraPoint, bool]:
            try:
                refreshed = self.read_point(pt.path)
                if refreshed:
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
            logger.error("All %d point reads failed — marking connection lost", len(points))
            self._connected = False
        elif fail_count > 0:
            logger.warning("Poll: %d of %d reads failed", fail_count, len(points))

        return [results[pt.path] for pt in points if pt.path in results]

    def close(self) -> None:
        self._session.close()
        self._connected = False
