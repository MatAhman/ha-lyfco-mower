"""Constants for the EGROBOT-compatible mower integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "lyfco_mower"
DEFAULT_NAME = "EGROBOT Mower"
DEFAULT_PORT = 9600
POLL_INTERVAL = timedelta(seconds=30)

PLATFORMS = ["sensor", "binary_sensor", "button", "lawn_mower", "text", "switch"]

ALARM_KEYS = (
    "boundary_wire_broken",
    "charging_station_no_power",
    "low_battery",
    "left_wheel_motor_overload",
    "right_wheel_motor_overload",
    "blade_motor_overload",
    "lift_sensor",
    "pressure_sensor",
    "collision_sensor",
    "handle_sensor",
    "mower_tilted",
    "wire_signal_lost",
    "outside_boundary",
    "mower_stuck",
)

# Preserve the entity unique IDs used by versions through 0.6.4. This lets an
# upgrade correct the displayed alarm names without creating duplicate entities.
ALARM_UNIQUE_KEYS = (
    "boundary_wire_broken",
    "charging_station_no_power",
    "low_battery",
    "left_wheel_motor_overload",
    "right_wheel_motor_overload",
    "blade_motor_overload",
    "lift_pressure_sensor_1",
    "lift_pressure_sensor_2",
    "lift_pressure_sensor_3",
    "handle_sensor",
    "mower_tilted",
    "wire_signal_lost",
    "outside_boundary",
    "mower_stuck",
)

# Empirical voltage model from the physically tested Lyfco E1750/M10 charging
# station. These values are inference context, not generic hard thresholds for
# every EGROBOT-compatible mower.
CHARGING_VOLTAGE = 26.4
CHARGE_BACKOFF_VOLTAGE = 29.5
CHARGE_RECONNECT_VOLTAGE = 28.6
VOLTAGE_TREND_EPSILON = 0.02

# Connectivity sensor debounce. A normal poll is every 30 seconds, so three
# consecutive failures are normally about 90 seconds. The age limit also keeps
# the sensor honest if polling is delayed or suspended for any reason.
ONLINE_MAX_FAILURES = 3
ONLINE_MAX_AGE_SECONDS = 90.0
