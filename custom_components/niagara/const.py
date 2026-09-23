"""Constants for the Niagara BMS integration."""

DOMAIN = "niagara"

CONF_ADDON_URL = "addon_url"
CONF_DEVICE_NAME = "device_name"
CONF_AREA_DEPTH = "area_depth"
CONF_DEVICE_NAME_DEPTH = "device_name_depth"

# The add-on's hostname carries a per-install repository hash
# (e.g. b7c7a509-niagara-ha), so it cannot be hardcoded. The config flow asks
# Supervisor for it; this is only the fallback when Supervisor is unavailable,
# such as a bridge running outside the add-on.
DEFAULT_ADDON_URL = ""

ADDON_SLUG_SUFFIX = "niagara-ha"
DEFAULT_INGRESS_PORT = 8099
SUPERVISOR_API = "http://supervisor"
DEFAULT_POLL_INTERVAL = 30
DEFAULT_DEVICE_NAME = "Niagara BMS"
DEFAULT_AREA_DEPTH = 1

# How many trailing path segments name a device. 1 gives "DB-1-Light";
# the previous behaviour was the integration name plus three segments, which
# produced names too long to read in any dashboard.
DEFAULT_DEVICE_NAME_DEPTH = 1
