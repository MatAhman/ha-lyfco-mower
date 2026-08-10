"""Data coordinator for Lyfco mower."""

from __future__ import annotations

import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

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
        self._clock_signature: tuple[object, ...] | None = None
        self._last_clock_attempt = 0.0
        self._consecutive_update_failures = 0

    async def _async_update_data(self) -> MowerStatus:
        try:
            status = await self.client.async_get_status()
        except LyfcoError as error:
            self._consecutive_update_failures += 1
            if self.data is not None and self._consecutive_update_failures <= 2:
                # The mower's Miotlink bridge occasionally drops one or two
                # polls and then recovers. Preserve the last confirmed state
                # during that short window; a third failure still makes the
                # coordinator unavailable in the normal Home Assistant way.
                _LOGGER.debug(
                    "Keeping last mower status after transient failure %s/2: %s",
                    self._consecutive_update_failures,
                    error,
                )
                return self.data
            raise UpdateFailed(str(error)) from error
        self._consecutive_update_failures = 0
        await self.async_sync_clock()
        return status

    async def async_sync_clock(self, force: bool = False) -> bool:
        """Sync on startup, local date change, or DST/time-zone change."""
        local_time = dt_util.now()
        signature = (
            local_time.date(),
            local_time.utcoffset(),
            str(local_time.tzinfo),
        )
        if not force and signature == self._clock_signature:
            return True
        if not force and time.monotonic() - self._last_clock_attempt < 300:
            return False
        self._last_clock_attempt = time.monotonic()
        try:
            await self.client.async_sync_clock(local_time)
        except LyfcoError as error:
            _LOGGER.warning("Could not synchronize mower clock: %s", error)
            return False
        self._clock_signature = signature
        return True
