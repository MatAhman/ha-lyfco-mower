"""Synchronized configuration switches for Lyfco mower."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LyfcoConfigEntry
from .entity import LyfcoEntity
from .protocol import LyfcoError, MowerSchedule

DAY_KEYS = (
    "sunday",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LyfcoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create synchronized configuration switches."""
    async_add_entities(
        [LyfcoRainSensorSwitch(entry)]
        + [
            LyfcoEdgeMowingSwitch(entry, day, day_key)
            for day, day_key in enumerate(DAY_KEYS)
        ]
    )


class LyfcoRainSensorSwitch(LyfcoEntity, SwitchEntity):
    """Enable or disable the mower's rain-sensor function."""

    _attr_icon = "mdi:weather-rainy"
    _attr_translation_key = "rain_sensor"

    def __init__(self, entry: LyfcoConfigEntry) -> None:
        super().__init__(entry, "rain_sensor")
        self._client = entry.runtime_data.client

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.configuration is not None

    @property
    def is_on(self) -> bool | None:
        configuration = self.coordinator.data.configuration
        return configuration.rain_sensor if configuration is not None else None

    async def _async_set_enabled(self, enabled: bool) -> None:
        try:
            await self._client.async_set_rain_sensor(enabled)
        except LyfcoError as error:
            raise HomeAssistantError(str(error)) from error
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set_enabled(False)


class LyfcoEdgeMowingSwitch(LyfcoEntity, SwitchEntity):
    """Toggle one day's verified edge-mowing schedule flag."""

    _attr_icon = "mdi:vector-polyline"

    def __init__(self, entry: LyfcoConfigEntry, day: int, day_key: str) -> None:
        super().__init__(entry, f"edge_mowing_{day_key}")
        self._day = day
        self._client = entry.runtime_data.client
        self._attr_translation_key = f"edge_mowing_{day_key}"

    @property
    def schedule(self) -> MowerSchedule | None:
        return next(
            (
                schedule
                for schedule in self.coordinator.data.schedules
                if schedule.day == self._day
            ),
            None,
        )

    @property
    def available(self) -> bool:
        return super().available and self.schedule is not None

    @property
    def is_on(self) -> bool | None:
        schedule = self.schedule
        return schedule.edge_mowing if schedule is not None else None

    async def _async_set_edge_mowing(self, enabled: bool) -> None:
        schedule = self.schedule
        if schedule is None:
            raise HomeAssistantError("The current schedule has not been read yet")
        if schedule.edge_mowing == enabled:
            return
        hour, minute = map(int, schedule.start_time.split(":"))
        try:
            await self._client.async_set_schedule(
                day=self._day,
                start_hour=hour,
                start_minute=minute,
                edge_mowing=enabled,
                area_minutes=schedule.area_minutes,
            )
        except LyfcoError as error:
            raise HomeAssistantError(str(error)) from error
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set_edge_mowing(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set_edge_mowing(False)
