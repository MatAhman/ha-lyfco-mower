"""Data coordinator for Lyfco mower."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, POLL_INTERVAL
from .protocol import LyfcoError, LyfcoMowerClient, MowerStatus

_LOGGER = logging.getLogger(__name__)


class LyfcoCoordinator(DataUpdateCoordinator[MowerStatus]):
    """Poll status while the protocol client maintains its TCP heartbeat."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: LyfcoMowerClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=POLL_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> MowerStatus:
        try:
            return await self.client.async_get_status()
        except LyfcoError as error:
            raise UpdateFailed(str(error)) from error
