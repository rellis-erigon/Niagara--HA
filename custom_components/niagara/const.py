"""Constants for the Niagara BMS integration."""

DOMAIN = "niagara"

CONF_USE_HTTPS = "use_https"
CONF_VERIFY_SSL = "verify_ssl"
CONF_POINT_FILTER = "point_filter"
CONF_POLL_WORKERS = "poll_workers"
CONF_DEVICE_NAME = "device_name"
CONF_DEVICE_DEPTH = "device_depth"
CONF_AREA_DEPTH = "area_depth"

DEFAULT_PORT = 443
DEFAULT_POLL_INTERVAL = 30
DEFAULT_DEVICE_NAME = "Niagara BMS"
DEFAULT_DEVICE_DEPTH = 0
DEFAULT_AREA_DEPTH = 1
DEFAULT_POLL_WORKERS = 5
