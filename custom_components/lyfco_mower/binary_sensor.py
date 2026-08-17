"""Binary sensors for Lyfco mower."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LyfcoConfigEntry
from .const import (
    ALARM_KEYS,
    ALARM_UNIQUE_KEYS,
    CHARGE_BACKOFF_VOLTAGE,
    CHARGE_RECONNECT_VOLTAGE,
    CHARGING_VOLTAGE,
    ONLINE_MAX_AGE_SECONDS,
    ONLINE_MAX_FAILURES,
)
from .entity import LyfcoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LyfcoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create connectivity, inferred state and decoded alarm entities."""
    async_add_entities(
        [
            LyfcoOnlineBinarySensor(entry),
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


class LyfcoOnlineBinarySensor(LyfcoEntity, BinarySensorEntity):
    """Expose whether the mower is responding to status polling."""

    _attr_translation_key = "online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: LyfcoConfigEntry) -> None:
        super().__init__(entry, "online")

    @property
    def available(self) -> bool:
        """Keep the connectivity entity available when the mower is offline."""
        return True

    @property
    def is_on(self) -> bool:
        """Return true while recent communication remains trustworthy."""
        last_success = self.coordinator._last_real_success
        if last_success <= 0:
            return False
        age = max(0.0, time.monotonic() - last_success)
        return (
            self.coordinator._consecutive_update_failures < ONLINE_MAX_FAILURES
            and age <= ONLINE_MAX_AGE_SECONDS
        )

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose useful connection diagnostics."""
        last_success = self.coordinator._last_real_success
        age = (
            None
            if last_success <= 0
            else round(max(0.0, time.monotonic() - last_success), 1)
        )
        last_successful_poll = (
            None
            if age is None
            else (
                datetime.now(timezone.utc) - timedelta(seconds=age)
            ).isoformat()
        )
        poll_interval = self.coordinator.update_interval
        return {
            "last_seen": last_successful_poll,
            "last_successful_poll": last_successful_poll,
            "last_seen_age_seconds": age,
            "consecutive_failures": self.coordinator._consecutive_update_failures,
            "poll_interval_seconds": (
                None if poll_interval is None else poll_interval.total_seconds()
            ),
            "connection_state": self.coordinator._connection_state,
            "offline_after_failures": ONLINE_MAX_FAILURES,
            "offline_after_seconds": ONLINE_MAX_AGE_SECONDS,
        }


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
    """Expose active charging inferred from the measured dock cycle."""

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
            "dock_detect_voltage": CHARGING_VOLTAGE,
            "backoff_voltage": CHARGE_BACKOFF_VOLTAGE,
            "reconnect_voltage": CHARGE_RECONNECT_VOLTAGE,
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
