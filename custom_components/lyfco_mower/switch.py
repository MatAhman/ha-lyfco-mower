"""Assumed-state cutting blade switch for Lyfco mower."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import LyfcoConfigEntry
from .entity import LyfcoEntity
from .protocol import LyfcoError


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LyfcoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the cutting blade switch."""
    async_add_entities([LyfcoBladeSwitch(entry)])


class LyfcoBladeSwitch(LyfcoEntity, SwitchEntity, RestoreEntity):
    """Track the blade optimistically because Y8 is a toggle command."""

    _attr_translation_key = "blade"
    _attr_icon = "mdi:saw-blade"
    _attr_assumed_state = True

    def __init__(self, entry: LyfcoConfigEntry) -> None:
        super().__init__(entry, "blade")
        self._client = entry.runtime_data.client
        self._is_on: bool | None = None

    async def async_added_to_hass(self) -> None:
        """Restore Home Assistant's last assumed blade state."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            if last_state.state == STATE_ON:
                self._is_on = True
            elif last_state.state == STATE_OFF:
                self._is_on = False

    @property
    def is_on(self) -> bool | None:
        """Return Home Assistant's assumed blade state."""
        return self._is_on

    async def _async_set_assumed_state(self, target: bool) -> None:
        if self._is_on is target:
            return
        try:
            await self._client.async_toggle_blade()
        except LyfcoError as error:
            raise HomeAssistantError(str(error)) from error
        self._is_on = target
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Toggle the blade and assume that it is now on."""
        await self._async_set_assumed_state(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Toggle the blade and assume that it is now off."""
        await self._async_set_assumed_state(False)
