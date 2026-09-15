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
from point_mapper import map_point

OPTIONS_PATH = Path("/data/options.json")
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


def main() -> None:
    global _shutdown

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    opts = load_options()
    setup_logging(opts.get("log_level", "info"))

    logger.info("Niagara BMS Bridge v0.1.2 starting")
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
        host=os.environ.get("MQTT_HOST", "core-mosquitto"),
        port=int(os.environ.get("MQTT_PORT", "1883")),
        username=os.environ.get("MQTT_USER", ""),
        password=os.environ.get("MQTT_PASSWORD", ""),
        topic_prefix=opts.get("mqtt_topic_prefix", "niagara"),
    )

    poll_interval = opts.get("poll_interval_seconds", 30)
    point_filter = opts.get("point_filter", "")
    topic_prefix = opts.get("mqtt_topic_prefix", "niagara")
    reconnect_delay = RECONNECT_DELAY

    if not mqtt_pub.connect():
        logger.error("Cannot connect to MQTT broker — retrying in background")

    time.sleep(2)

    discovered_points = []
    entity_maps = {}

    while not _shutdown:
        if not obix.connected:
            logger.info("Connecting to Niagara oBIX...")
            if obix.test_connection():
                reconnect_delay = RECONNECT_DELAY
                logger.info("Discovering points (filter=%s)...", point_filter or "(none)")
                discovered_points = obix.discover_points(point_filter)
                logger.info("Found %d points", len(discovered_points))

                entity_maps = {}
                for pt in discovered_points:
                    mapped = map_point(pt, topic_prefix)
                    if mapped:
                        entity_maps[pt.path] = mapped
                        mqtt_pub.publish_discovery(mapped)

                current_topics = {m["discovery_topic"] for m in entity_maps.values()}
                mqtt_pub.remove_stale_discoveries(current_topics)
                mqtt_pub.publish_availability(True)
                logger.info("Published %d entities to HA", len(entity_maps))
            else:
                logger.warning(
                    "Connection failed — retrying in %ds", reconnect_delay
                )
                _sleep_interruptible(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, MAX_RECONNECT_DELAY)
                continue

        updated = obix.poll_points(discovered_points)
        discovered_points = updated

        for pt in updated:
            mapped = entity_maps.get(pt.path)
            if not mapped:
                continue
            value = pt.value if pt.value is not None else ""
            mqtt_pub.publish_state(mapped["state_topic"], value)

            if pt.status and pt.status != "ok":
                mqtt_pub.publish_attributes(
                    mapped["state_topic"],
                    {"niagara_status": pt.status, "niagara_path": pt.path},
                )

        if not obix.connected:
            mqtt_pub.publish_availability(False)
            continue

        _sleep_interruptible(poll_interval)

    logger.info("Shutting down...")
    mqtt_pub.disconnect()
    obix.close()


def _sleep_interruptible(seconds: int) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end and not _shutdown:
        time.sleep(1)


if __name__ == "__main__":
    main()
