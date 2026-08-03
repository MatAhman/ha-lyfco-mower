"""Constants for the Lyfco mower integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "lyfco_mower"
DEFAULT_NAME = "Lyfco Robot Mower"
DEFAULT_PORT = 9600
POLL_INTERVAL = timedelta(seconds=30)

PLATFORMS = ["sensor", "binary_sensor", "button", "lawn_mower", "switch"]

ALARM_KEYS = (
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
