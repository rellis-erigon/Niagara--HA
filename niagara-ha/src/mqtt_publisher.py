"""Publish Niagara points to Home Assistant via MQTT Discovery."""

import json
import logging
from typing import Any

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


class MqttPublisher:
    def __init__(
        self,
        host: str = "core-mosquitto",
        port: int = 1883,
        username: str = "",
        password: str = "",
        topic_prefix: str = "niagara",
    ):
        self._host = host
        self._port = port
        self._topic_prefix = topic_prefix
        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="niagara-ha-bridge",
        )
        if username:
            self._client.username_pw_set(username, password or None)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._connected = False
        self._published_discoveries: set[str] = set()

    @property
    def connected(self) -> bool:
        return self._connected

    def _on_connect(
        self, client: mqtt.Client, userdata: Any, flags: Any, rc: Any, properties: Any = None
    ) -> None:
        if hasattr(rc, "value"):
            rc_val = rc.value
        else:
            rc_val = rc
        if rc_val == 0:
            self._connected = True
            logger.info("Connected to MQTT broker at %s:%d", self._host, self._port)
        else:
            logger.error("MQTT connection failed: rc=%s", rc)

    def _on_disconnect(
        self, client: mqtt.Client, userdata: Any, flags: Any = None, rc: Any = None, properties: Any = None
    ) -> None:
        self._connected = False
        logger.warning("Disconnected from MQTT broker")

    def connect(self) -> bool:
        try:
            self._client.connect(self._host, self._port, keepalive=60)
            self._client.loop_start()
            return True
        except Exception as e:
            logger.error("Failed to connect to MQTT: %s", e)
            return False

    def publish_discovery(self, entity_map: dict[str, Any]) -> None:
        topic = entity_map["discovery_topic"]
        payload = json.dumps(entity_map["config_payload"])
        result = self._client.publish(topic, payload, qos=1, retain=True)
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            self._published_discoveries.add(topic)
            logger.debug("Discovery published: %s", topic)
        else:
            logger.warning("Failed to publish discovery to %s", topic)

    def publish_state(self, state_topic: str, value: str) -> None:
        self._client.publish(state_topic, value, qos=0, retain=False)

    def publish_attributes(self, state_topic: str, attributes: dict) -> None:
        attr_topic = f"{state_topic}/attributes"
        self._client.publish(attr_topic, json.dumps(attributes), qos=0, retain=False)

    def publish_availability(self, available: bool) -> None:
        topic = f"{self._topic_prefix}/bridge/availability"
        payload = "online" if available else "offline"
        self._client.publish(topic, payload, qos=1, retain=True)

    def remove_stale_discoveries(
        self, current_topics: set[str],
    ) -> None:
        stale = self._published_discoveries - current_topics
        for topic in stale:
            self._client.publish(topic, "", qos=1, retain=True)
        if stale:
            logger.info("Cleared %d stale discovery topics", len(stale))
        self._published_discoveries = current_topics.copy()

    def disconnect(self) -> None:
        self.publish_availability(False)
        self._client.loop_stop()
        self._client.disconnect()
        self._connected = False
