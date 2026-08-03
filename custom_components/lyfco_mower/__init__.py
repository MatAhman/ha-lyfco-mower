"""Lyfco robot mower integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .coordinator import LyfcoCoordinator
from .protocol import LyfcoMowerClient


@dataclass(slots=True)
class LyfcoRuntimeData:
    """Runtime objects associated with a config entry."""

    client: LyfcoMowerClient
    coordinator: LyfcoCoordinator


type LyfcoConfigEntry = ConfigEntry[LyfcoRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: LyfcoConfigEntry) -> bool:
    """Set up Lyfco from a config entry."""
    client = LyfcoMowerClient(entry.data[CONF_HOST])
    coordinator = LyfcoCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = LyfcoRuntimeData(client, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LyfcoConfigEntry) -> bool:
    """Unload a config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.client.async_close()
    return True
