"""Niagara BMS Bridge — main entry point.

Reads add-on options, connects to Niagara via oBIX, discovers points,
and publishes them to Home Assistant via MQTT Discovery on a polling loop.
"""

import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

from mqtt_publisher import MqttPublisher
from obix_client import ObixClient, ObixError
from point_manager import (
    DEVICE_FOLDERS_FILE,
    POINTS_DIR,
    POINTS_FILE,
    filter_enabled,
    load_auto_enable_rules,
    load_device_folders,
    load_point_selections,
    save_point_selections,
)
from point_mapper import map_point

OPTIONS_PATH = Path("/data/options.json")
VALUES_FILE = POINTS_DIR / "values.json"
RECONNECT_DELAY = 10
MAX_RECONNECT_DELAY = 300


def load_options() -> dict:
    if not OPTIONS_PATH.exists():
        logger.error("No options.json found at %s", OPTIONS_PATH)
        sys.exit(1)
    with open(OPTIONS_PATH) as f:
        return json.load(f)


def setup_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


logger = logging.getLogger("niagara-ha")

_shutdown = False


def _signal_handler(signum, frame):
    global _shutdown
    _shutdown = True
    logger.info("Shutdown signal received")


def _get_file_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def main() -> None:
    global _shutdown

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    opts = load_options()
    setup_logging(opts.get("log_level", "info"))

    logger.info("Niagara BMS Bridge v0.6.2 starting")
    logger.info("Target: %s:%d (HTTPS=%s)", opts["niagara_host"], opts["niagara_port"], opts["use_https"])

    if not opts.get("niagara_host"):
        logger.error("niagara_host is required — configure the add-on and restart")
        sys.exit(1)

    obix = ObixClient(
        host=opts["niagara_host"],
        username=opts.get("niagara_user", ""),
        password=opts.get("niagara_password", ""),
        port=opts.get("niagara_port", 443),
        use_https=opts.get("use_https", True),
        verify_ssl=opts.get("verify_ssl", False),
    )

    mqtt_pub = MqttPublisher(
        host=opts.get("mqtt_host") or os.environ.get("MQTT_HOST", "core-mosquitto"),
        port=opts.get("mqtt_port") or int(os.environ.get("MQTT_PORT", "1883")),
        username=opts.get("mqtt_user") or os.environ.get("MQTT_USER", ""),
        password=opts.get("mqtt_password") or os.environ.get("MQTT_PASSWORD", ""),
        topic_prefix=opts.get("mqtt_topic_prefix", "niagara"),
    )

    poll_interval = opts.get("poll_interval_seconds", 30)
    point_filter = opts.get("point_filter", "")
    topic_prefix = opts.get("mqtt_topic_prefix", "niagara")
    device_name = opts.get("device_name", "Niagara BMS")
    poll_workers = opts.get("poll_workers", 5)
    device_depth = opts.get("device_depth", 0)
    reconnect_delay = RECONNECT_DELAY

    if not mqtt_pub.connect():
        logger.error("Cannot connect to MQTT broker — retrying in background")

    time.sleep(2)

    active_points: list = []
    entity_maps: dict = {}
    points_mtime = 0.0
    folders_mtime = 0.0
    discovered_count = 0
    last_values: dict[str, str] = _load_values_cache()
    last_statuses: dict[str, str] = {}
    using_watch = False
    if last_values:
        logger.info("Loaded %d cached point values from previous session", len(last_values))

    while not _shutdown:
        if not obix.connected:
            logger.info("Connecting to Niagara oBIX...")
            if obix.test_connection():
                reconnect_delay = RECONNECT_DELAY
                logger.info("Discovering points (filter=%s)...", point_filter or "(none)")
                discovered_points = obix.discover_points(point_filter)
                discovered_count = len(discovered_points)
                logger.info("Found %d points", discovered_count)

                existing_selections = load_point_selections()
                config_patterns = opts.get("auto_enable_patterns", [])
                file_patterns = load_auto_enable_rules()
                auto_patterns = list(dict.fromkeys(config_patterns + file_patterns))
                if auto_patterns:
                    logger.info("Auto-enable rules: %d patterns active", len(auto_patterns))
                device_folders = load_device_folders()
                selections = save_point_selections(discovered_points, existing_selections, auto_patterns, device_depth, device_folders)
                points_mtime = _get_file_mtime(POINTS_FILE)
                folders_mtime = _get_file_mtime(DEVICE_FOLDERS_FILE)
                active_points = filter_enabled(discovered_points, selections)
                logger.info(
                    "Active points: %d of %d (use the web UI or edit points.yaml)",
                    len(active_points), discovered_count,
                )

                del discovered_points, existing_selections
                entity_maps = _publish_entities(active_points, selections, mqtt_pub, topic_prefix, device_name, last_values, device_depth, device_folders)
                del selections
                last_statuses.clear()

                using_watch = obix.setup_watch(active_points, poll_interval)
                if using_watch:
                    for pt in active_points:
                        if pt.value is not None:
                            last_values[pt.path] = str(pt.value)
                            mapped = entity_maps.get(pt.path)
                            if mapped:
                                mqtt_pub.publish_state(mapped["state_topic"], str(pt.value))
                    logger.info("Watch mode: initial values published for %d points", len(last_values))
                else:
                    logger.info("Legacy polling mode: %d workers", poll_workers)
            else:
                logger.warning(
                    "Connection failed — retrying in %ds", reconnect_delay
                )
                _sleep_interruptible(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, MAX_RECONNECT_DELAY)
                continue

        current_mtime = _get_file_mtime(POINTS_FILE)
        current_folders_mtime = _get_file_mtime(DEVICE_FOLDERS_FILE)
        if current_mtime > points_mtime or current_folders_mtime > folders_mtime:
            if current_mtime > points_mtime:
                logger.info("points.yaml changed — reloading selections")
            if current_folders_mtime > folders_mtime:
                logger.info("device_folders.yaml changed — reloading device grouping")
            points_mtime = current_mtime
            folders_mtime = current_folders_mtime
            selections = load_point_selections()
            device_folders = load_device_folders()
            enabled_paths = {p for p, e in selections.items() if e.get("enabled", False)}
            active_points = [pt for pt in active_points if pt.path in enabled_paths]
            new_paths = enabled_paths - {pt.path for pt in active_points}
            if new_paths:
                logger.info("New enabled points detected (%d) — will pick up on next reconnect", len(new_paths))
            entity_maps = _publish_entities(active_points, selections, mqtt_pub, topic_prefix, device_name, last_values, device_depth, device_folders)
            del selections
            last_statuses.clear()
            if using_watch:
                using_watch = obix.setup_watch(active_points, poll_interval)
            logger.info("Reloaded: %d active points", len(active_points))

        updated = obix.poll_points(active_points, max_workers=poll_workers)
        active_points = updated

        changed = 0
        unchanged = 0
        faulted = 0
        for pt in updated:
            mapped = entity_maps.get(pt.path)
            if not mapped:
                continue
            if pt.value is not None:
                val_str = str(pt.value)
                if last_values.get(pt.path) != val_str:
                    mqtt_pub.publish_state(mapped["state_topic"], val_str)
                    last_values[pt.path] = val_str
                    changed += 1
                else:
                    unchanged += 1
            else:
                faulted += 1

            if pt.status and pt.status != "ok":
                if last_statuses.get(pt.path) != pt.status:
                    mqtt_pub.publish_attributes(
                        mapped["state_topic"],
                        {"niagara_status": pt.status, "niagara_path": pt.path},
                    )
                    last_statuses[pt.path] = pt.status

        logger.debug("Poll: %d changed, %d unchanged, %d faulted", changed, unchanged, faulted)

        _write_values_cache(last_values)

        if not obix.connected:
            mqtt_pub.publish_availability(False)
            continue

        _sleep_interruptible(poll_interval)

    logger.info("Shutting down...")
    mqtt_pub.disconnect()
    obix.close()


def _publish_entities(
    active_points, selections, mqtt_pub, topic_prefix, device_name,
    cached_values: dict[str, str] | None = None, device_depth: int = 0,
    device_folders: list[str] | None = None,
) -> dict:
    entity_maps = {}
    for pt in active_points:
        entry = selections.get(pt.path, {})
        group = entry.get("group", "")
        custom_name = entry.get("custom_name", "")
        mapped = map_point(pt, topic_prefix, device_name, group, custom_name, device_depth, device_folders)
        if mapped:
            entity_maps[pt.path] = mapped
            mqtt_pub.publish_discovery(mapped)

    current_topics = {m["discovery_topic"] for m in entity_maps.values()}
    mqtt_pub.remove_stale_discoveries(current_topics)
    mqtt_pub.publish_availability(True)

    initial_values = 0
    for pt in active_points:
        mapped = entity_maps.get(pt.path)
        if not mapped:
            continue
        val = str(pt.value) if pt.value is not None else (cached_values or {}).get(pt.path)
        if val is not None:
            mqtt_pub.publish_state(mapped["state_topic"], val)
            initial_values += 1

    logger.info(
        "Published %d entities to HA (%d with initial values)",
        len(entity_maps), initial_values,
    )
    return entity_maps


def _load_values_cache() -> dict[str, str]:
    try:
        if VALUES_FILE.exists():
            with open(VALUES_FILE) as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.debug("Failed to read values cache: %s", e)
    return {}


def _write_values_cache(values: dict[str, str]) -> None:
    try:
        POINTS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = VALUES_FILE.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(values, f)
        tmp.replace(VALUES_FILE)
    except OSError as e:
        logger.debug("Failed to write values cache: %s", e)


def _sleep_interruptible(seconds: int) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end and not _shutdown:
        time.sleep(1)


if __name__ == "__main__":
    main()
