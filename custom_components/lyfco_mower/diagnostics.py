"""Diagnostics support for Lyfco Robot Mower 1.0.0."""

from __future__ import annotations

from dataclasses import asdict
import time
from typing import Any

from homeassistant.core import HomeAssistant

from . import LyfcoConfigEntry
from .state_machine import (
    DOCK_JUMP_MIN,
    PASSIVE_ACTIVE_MIN_RISE,
    PASSIVE_ACTIVE_MIN_SAMPLES,
    PASSIVE_ACTIVE_MIN_SECONDS,
    PASSIVE_STARTUP_MIN_RISE,
    PASSIVE_STARTUP_MIN_SAMPLES,
    PASSIVE_STARTUP_MIN_SECONDS,
    RETURN_MIN_RISE,
    RETURN_MIN_SAMPLES,
    RETURN_SETTLE_SECONDS,
    VOLTAGE_HISTORY_SIZE,
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: LyfcoConfigEntry
) -> dict[str, Any]:
    """Return diagnostics without host, Wi-Fi credentials or PIN data."""
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    status = coordinator.data

    return {
        "integration": {
            "config_entry_id": entry.entry_id,
            "integration_version": "1.0.0",
            "diagnostics_format": 5,
        },
        "experiment": {
            "purpose": (
                "field-tested passive dock detection, schedule authority fixes, "
                "reload recovery and estimated battery state"
            ),
            "normal_w_poll_seconds": 30,
            "fast_w_poll_seconds": 10,
            "fast_poll_window_seconds": 180,
            "freshness_guard_before_user_commands_seconds": 60,
            "absolute_voltage_alone_can_prove_docked": False,
            "charge_reference_values_are_hard_thresholds": False,
            "return_load_settle_seconds": RETURN_SETTLE_SECONDS,
            "return_window_min_samples": RETURN_MIN_SAMPLES,
            "return_window_total_voltage": RETURN_MIN_RISE,
            "passive_startup_min_samples": PASSIVE_STARTUP_MIN_SAMPLES,
            "passive_startup_min_seconds": PASSIVE_STARTUP_MIN_SECONDS,
            "passive_startup_rise_total_voltage": PASSIVE_STARTUP_MIN_RISE,
            "passive_active_min_samples": PASSIVE_ACTIVE_MIN_SAMPLES,
            "passive_active_min_seconds": PASSIVE_ACTIVE_MIN_SECONDS,
            "passive_active_rise_total_voltage": PASSIVE_ACTIVE_MIN_RISE,
            "dock_jump_min_voltage": DOCK_JUMP_MIN,
            "all_state_voltage_history_samples": VOLTAGE_HISTORY_SIZE,
            "completed_charge_cycles_persist_across_reload": True,
            "live_activity_state_persisted_across_reload": False,
        },
        "capabilities_observed": {
            "local_status": True,
            "manual_control": True,
            "schedule": True,
            "working_areas": True,
            "configuration": True,
            "rain_sensor_control": True,
            "firmware_known": status.firmware is not None,
            "adaptive_polling": True,
            "persistent_charge_learning": True,
            "connection_freshness": True,
            "passive_dock_detection": True,
            "estimated_battery_percent": True,
            "minute_resolution_current_charging_time": True,
            "schedule_readback_retries": True,
        },
        "coordinator": coordinator.diagnostics(),
        "protocol_inference": coordinator.state_machine.diagnostics(
            time.monotonic()
        ),
        "schedule_write_verification": runtime.client.schedule_write_diagnostics,
        "latest_status": {
            **asdict(status),
            "active_alarm_keys": list(status.active_alarm_keys),
        },
        "issues": [],
    }
