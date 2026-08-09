"""Home Assistant lawn mower entity for Lyfco mower."""

from __future__ import annotations

from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LyfcoConfigEntry
from .entity import LyfcoEntity
from .protocol import LyfcoError


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LyfcoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the mower control entity."""
    async_add_entities([LyfcoLawnMower(entry)])


class LyfcoLawnMower(LyfcoEntity, LawnMowerEntity):
    """Represent the mower using Home Assistant's standard mower controls."""

    _attr_supported_features = (
        LawnMowerEntityFeature.START_MOWING
        | LawnMowerEntityFeature.PAUSE
        | LawnMowerEntityFeature.DOCK
    )

    def __init__(self, entry: LyfcoConfigEntry) -> None:
        super().__init__(entry, "mower")
        self._client = entry.runtime_data.client
        self._assumed_activity: LawnMowerActivity | None = None

    @property
    def activity(self) -> LawnMowerActivity | None:
        """Return alarms or the combined command/voltage activity."""
        if any(self.coordinator.data.alarm_flags):
            return LawnMowerActivity.ERROR
        if self._assumed_activity is not None:
            return self._assumed_activity
        activity = self.coordinator.data.inferred_activity
        return {
            "mowing": LawnMowerActivity.MOWING,
            "paused": LawnMowerActivity.PAUSED,
            "returning": LawnMowerActivity.RETURNING,
            "docked": LawnMowerActivity.DOCKED,
        }.get(activity)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "inferred": True,
            "inference_source": self.coordinator.data.inference_source,
            "charging": self.coordinator.data.charging,
            "docked": self.coordinator.data.docked,
            "rain_detected_inferred": (
                self.coordinator.data.rain_detected_inferred
            ),
        }

    def _handle_coordinator_update(self) -> None:
        """Replace immediate optimistic activity with the latest inference."""
        self._assumed_activity = None
        super()._handle_coordinator_update()

    async def _async_command(
        self, method_name: str, activity: LawnMowerActivity
    ) -> None:
        try:
            await getattr(self._client, method_name)()
        except LyfcoError as error:
            raise HomeAssistantError(str(error)) from error
        self._assumed_activity = activity
        self.async_write_ha_state()

    async def async_start_mowing(self) -> None:
        """Start automatic mowing."""
        await self._async_command("async_start_auto", LawnMowerActivity.MOWING)

    async def async_pause(self) -> None:
        """Stop/pause the mower using the verified Y0 command."""
        await self._async_command("async_stop", LawnMowerActivity.PAUSED)

    async def async_dock(self) -> None:
        """Return the mower to its charging station."""
        await self._async_command("async_go_home", LawnMowerActivity.RETURNING)
