"""Alarm binary sensors for Lyfco mower."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LyfcoConfigEntry
from .const import ALARM_KEYS
from .entity import LyfcoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LyfcoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the 14 decoded alarm entities."""
    async_add_entities(
        LyfcoAlarmBinarySensor(entry, index, key)
        for index, key in enumerate(ALARM_KEYS)
    )


class LyfcoAlarmBinarySensor(LyfcoEntity, BinarySensorEntity):
    """One alarm bit in the mower's W response."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: LyfcoConfigEntry, index: int, key: str) -> None:
        super().__init__(entry, f"alarm_{key}")
        self._index = index
        self._attr_translation_key = f"alarm_{key}"

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.alarm_flags[self._index]
