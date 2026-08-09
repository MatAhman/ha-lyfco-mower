"""Alarm binary sensors for Lyfco mower."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LyfcoConfigEntry
from .const import ALARM_KEYS, ALARM_UNIQUE_KEYS, CHARGING_VOLTAGE
from .entity import LyfcoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LyfcoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create inferred state and the 14 decoded alarm entities."""
    async_add_entities(
        [
            LyfcoDockedBinarySensor(entry),
            LyfcoChargingBinarySensor(entry),
            LyfcoInferredRainBinarySensor(entry),
            *(
                LyfcoAlarmBinarySensor(
                    entry, index, key, ALARM_UNIQUE_KEYS[index]
                )
                for index, key in enumerate(ALARM_KEYS)
            ),
        ]
    )


class LyfcoDockedBinarySensor(LyfcoEntity, BinarySensorEntity):
    """Expose the dock latch established by charging voltage."""

    _attr_translation_key = "docked"
    _attr_icon = "mdi:home-map-marker"

    def __init__(self, entry: LyfcoConfigEntry) -> None:
        super().__init__(entry, "docked")

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.docked

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "inferred": True,
            "source": self.coordinator.data.inference_source,
        }


class LyfcoChargingBinarySensor(LyfcoEntity, BinarySensorEntity):
    """Expose active charging inferred from measured machine voltage."""

    _attr_translation_key = "charging"
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    def __init__(self, entry: LyfcoConfigEntry) -> None:
        super().__init__(entry, "charging")

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.charging

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "inferred": True,
            "threshold_voltage": CHARGING_VOLTAGE,
            "machine_voltage": self.coordinator.data.voltage,
        }


class LyfcoInferredRainBinarySensor(LyfcoEntity, BinarySensorEntity):
    """Expose a likely wet rain sensor from an unexplained auto return."""

    _attr_translation_key = "rain_detected_inferred"
    _attr_device_class = BinarySensorDeviceClass.MOISTURE

    def __init__(self, entry: LyfcoConfigEntry) -> None:
        super().__init__(entry, "rain_detected_inferred")

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.rain_detected_inferred

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "inferred": True,
            "reason": "automatic_return_without_home_command_or_alarm",
        }


class LyfcoAlarmBinarySensor(LyfcoEntity, BinarySensorEntity):
    """One alarm bit in the mower's W response."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        entry: LyfcoConfigEntry,
        index: int,
        key: str,
        unique_key: str,
    ) -> None:
        super().__init__(entry, f"alarm_{unique_key}")
        self._index = index
        self._attr_translation_key = f"alarm_{key}"

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.alarm_flags[self._index]
