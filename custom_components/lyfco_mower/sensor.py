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
from homeassistant.const import EntityCategory, UnitOfElectricPotential, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LyfcoConfigEntry
from .entity import LyfcoEntity
from .protocol import MowerStatus


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
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LyfcoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create mower sensors."""
    async_add_entities(LyfcoSensor(entry, description) for description in SENSORS)


class LyfcoSensor(LyfcoEntity, SensorEntity):
    """A value read from the W/V responses."""

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
        if self.entity_description.key != "active_alarms":
            return None
        return {"active_alarm_keys": list(self.coordinator.data.active_alarm_keys)}
