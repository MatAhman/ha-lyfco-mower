"""Control buttons for Lyfco mower."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
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
    """Create the verified mower control buttons."""
    async_add_entities(
        [
            LyfcoActionButton(
                entry, "start_auto", "mdi:play-circle-outline", "async_start_auto"
            ),
            LyfcoActionButton(
                entry, "go_home", "mdi:home-import-outline", "async_go_home"
            ),
            LyfcoActionButton(
                entry, "stop", "mdi:stop-circle-outline", "async_stop"
            ),
            LyfcoActionButton(
                entry, "manual_mode", "mdi:gamepad-variant-outline", "async_manual_mode"
            ),
            LyfcoActionButton(
                entry, "manual_forward", "mdi:arrow-up-bold", "async_manual_forward"
            ),
            LyfcoActionButton(
                entry, "manual_reverse", "mdi:arrow-down-bold", "async_manual_reverse"
            ),
            LyfcoActionButton(
                entry, "manual_left", "mdi:arrow-left-bold", "async_manual_left"
            ),
            LyfcoActionButton(
                entry, "manual_right", "mdi:arrow-right-bold", "async_manual_right"
            ),
        ]
    )


class LyfcoActionButton(LyfcoEntity, ButtonEntity):
    """Send one verified action over the mower's active TCP session."""

    def __init__(
        self, entry: LyfcoConfigEntry, key: str, icon: str, method_name: str
    ) -> None:
        super().__init__(entry, key)
        self._client = entry.runtime_data.client
        self._method_name = method_name
        self._attr_translation_key = key
        self._attr_icon = icon

    async def async_press(self) -> None:
        try:
            await getattr(self._client, self._method_name)()
        except LyfcoError as error:
            raise HomeAssistantError(str(error)) from error
        # Let the normal coordinator poll update status after the command.
