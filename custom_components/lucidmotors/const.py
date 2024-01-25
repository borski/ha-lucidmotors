"""Constants for the Lucid Motors integration."""

DOMAIN = "lucidmotors"
ATTRIBUTION = "Data provided by Lucid Motors API"

ATTR_DIRECTION = "direction"
ATTR_ELEVATION = "elevation"
ATTR_POSITION_TIME = "position_time"
ATTR_VIN = "vin"

# Base vehicle info refresh interval (seconds)
DEFAULT_UPDATE_INTERVAL = 30

# "Fast" refresh interval used when new information is expected for some reason
FAST_UPDATE_INTERVAL = 5

# Timeout for fast updates in case data does not actually change
FAST_UPDATE_TIMEOUT = 2 * 60

# Default target temperature for climate control (Celsius)
DEFAULT_TARGET_TEMPERATURE = 20.0
