"""Directly editable weekday schedules for Lyfco mower."""

from __future__ import annotations

import re

from homeassistant.components.text import TextEntity, TextMode
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
SCHEDULE_PATTERN = re.compile(
    r"^\s*(\d{2}):(\d{2})\s*-\s*(\d{2}):(\d{2})\s*$"
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LyfcoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create seven editable schedule rows."""
    async_add_entities(
        LyfcoScheduleText(entry, day, day_key)
        for day, day_key in enumerate(DAY_KEYS)
    )


class LyfcoScheduleText(LyfcoEntity, TextEntity):
    """Edit start/end time directly from the mower device page."""

    _attr_icon = "mdi:calendar-clock"
    _attr_mode = TextMode.TEXT
    _attr_native_min = 13
    _attr_native_max = 13
    _attr_pattern = r"\d{2}:\d{2}\s*-\s*\d{2}:\d{2}"

    def __init__(self, entry: LyfcoConfigEntry, day: int, day_key: str) -> None:
        super().__init__(entry, f"schedule_{day_key}")
        self._day = day
        self._client = entry.runtime_data.client
        self._attr_translation_key = f"schedule_{day_key}"

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
    def native_value(self) -> str | None:
        schedule = self.schedule
        if schedule is None:
            return None
        start_hour, start_minute = map(int, schedule.start_time.split(":"))
        end_minutes = (
            start_hour * 60 + start_minute + sum(schedule.area_minutes)
        ) % (24 * 60)
        return (
            f"{schedule.start_time} - "
            f"{end_minutes // 60:02d}:{end_minutes % 60:02d}"
        )

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        schedule = self.schedule
        if schedule is None:
            return None
        attributes: dict[str, object] = {
            "edge_mowing": schedule.edge_mowing,
            "total_minutes": sum(schedule.area_minutes),
        }
        attributes.update(
            {
                f"area_{number}_minutes": minutes
                for number, minutes in enumerate(schedule.area_minutes, start=1)
            }
        )
        return attributes

    async def async_set_value(self, value: str) -> None:
        """Set a simple start/end schedule while preserving safe details."""
        match = SCHEDULE_PATTERN.fullmatch(value)
        if match is None:
            raise HomeAssistantError("Use the format HH:MM - HH:MM")
        start_hour, start_minute, end_hour, end_minute = map(int, match.groups())
        if start_hour > 23 or end_hour > 23 or start_minute > 59 or end_minute > 59:
            raise HomeAssistantError("Schedule time is outside 00:00-23:59")

        start = start_hour * 60 + start_minute
        end = end_hour * 60 + end_minute
        duration = 0 if end == start else (end - start) % (24 * 60)
        if duration % 10:
            raise HomeAssistantError("Mowing duration must be in steps of 10 minutes")

        schedule = self.schedule
        if schedule is None:
            raise HomeAssistantError("The current schedule has not been read yet")
        current_total = sum(schedule.area_minutes)
        if duration == current_total:
            area_minutes = schedule.area_minutes
        elif all(value == 0 for value in schedule.area_minutes[1:]):
            if duration > 250:
                raise HomeAssistantError("A simple schedule can be at most 250 minutes")
            area_minutes = (duration, 0, 0, 0, 0, 0)
        else:
            raise HomeAssistantError(
                "This day uses several working areas. Use the advanced set_schedule "
                "action to change its duration without losing area allocation."
            )

        try:
            await self._client.async_set_schedule(
                day=self._day,
                start_hour=start_hour,
                start_minute=start_minute,
                edge_mowing=schedule.edge_mowing,
                area_minutes=area_minutes,
            )
        except LyfcoError as error:
            raise HomeAssistantError(str(error)) from error
        await self.coordinator.async_request_refresh()
