"""Sensors for Lyfco mower."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    PERCENTAGE,
    UnitOfElectricPotential,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LyfcoConfigEntry
from .entity import LyfcoEntity
from .protocol import MowerStatus

BATTERY_EMPTY_VOLTAGE = 24.0
BATTERY_FULL_VOLTAGE = 29.0
BATTERY_MIN_PERCENT = 5


def estimated_battery_percent(voltage: float) -> int:
    """Estimate state of charge from the measured E1750 voltage."""
    if voltage <= BATTERY_EMPTY_VOLTAGE:
        return BATTERY_MIN_PERCENT
    if voltage >= BATTERY_FULL_VOLTAGE:
        return 100
    ratio = (voltage - BATTERY_EMPTY_VOLTAGE) / (
        BATTERY_FULL_VOLTAGE - BATTERY_EMPTY_VOLTAGE
    )
    return round(BATTERY_MIN_PERCENT + ratio * (100 - BATTERY_MIN_PERCENT))


@dataclass(frozen=True, kw_only=True)
class LyfcoSensorDescription(SensorEntityDescription):
    """Describe how to extract one value from mower status."""

    value_fn: Callable[[MowerStatus], object]


SENSORS = (
    LyfcoSensorDescription(
        key="runtime_hours",
        translation_key="runtime_hours",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda status: status.runtime_hours,
    ),
    LyfcoSensorDescription(
        key="charge_hours",
        translation_key="charge_hours",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda status: status.charge_hours,
    ),
    LyfcoSensorDescription(
        key="voltage",
        translation_key="voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda status: status.voltage,
    ),
    LyfcoSensorDescription(
        key="battery_estimated",
        translation_key="battery_estimated",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery",
        value_fn=lambda status: estimated_battery_percent(status.voltage),
    ),
    LyfcoSensorDescription(
        key="active_alarms",
        translation_key="active_alarms",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda status: len(status.active_alarm_keys),
    ),
    LyfcoSensorDescription(
        key="firmware",
        translation_key="firmware",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda status: status.firmware,
    ),
    LyfcoSensorDescription(
        key="working_areas",
        translation_key="working_areas",
        icon="mdi:map-marker-radius-outline",
        value_fn=lambda status: sum(area.enabled for area in status.areas)
        if status.areas
        else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LyfcoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create mower sensors."""
    entities: list[SensorEntity] = [
        LyfcoSensor(entry, description) for description in SENSORS
    ]
    entities.append(LyfcoCurrentChargingMinutesSensor(entry))
    async_add_entities(entities)


class LyfcoSensor(LyfcoEntity, SensorEntity):
    """A value read from the W/V responses or derived from voltage."""

    entity_description: LyfcoSensorDescription

    def __init__(
        self, entry: LyfcoConfigEntry, description: LyfcoSensorDescription
    ) -> None:
        super().__init__(entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> object:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        if self.entity_description.key == "active_alarms":
            return {"active_alarm_keys": list(self.coordinator.data.active_alarm_keys)}
        if self.entity_description.key == "working_areas":
            return {
                f"area_{area.number}": {
                    "located": area.located,
                    "enabled": area.enabled,
                }
                for area in self.coordinator.data.areas
            }
        if self.entity_description.key == "battery_estimated":
            return {
                "estimated": True,
                "model": "linear_voltage_estimate",
                "empty_reference_voltage": BATTERY_EMPTY_VOLTAGE,
                "empty_reference_percent": BATTERY_MIN_PERCENT,
                "full_reference_voltage": BATTERY_FULL_VOLTAGE,
                "machine_voltage": self.coordinator.data.voltage,
            }
        return None


class LyfcoCurrentChargingMinutesSensor(LyfcoEntity, SensorEntity):
    """Minute-resolution duration of the currently detected charging phase."""

    _attr_translation_key = "current_charging_minutes"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:timer-charging"

    def __init__(self, entry: LyfcoConfigEntry) -> None:
        super().__init__(entry, "current_charging_minutes")

    @property
    def native_value(self) -> float:
        return self.coordinator.current_charging_minutes

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "active_charging": self.coordinator.data.charging,
            "note": (
                "Minute-resolution time for the currently inferred charging "
                "phase; the mower's own total charging counter is only whole hours."
            ),
        }
