"""Lyfco robot mower integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DEFAULT_NAME, DOMAIN, PLATFORMS
from .coordinator import LyfcoCoordinator
from .protocol import LyfcoError, LyfcoMowerClient

SERVICE_SET_SCHEDULE = "set_schedule"


def _area_minutes(value: object) -> int:
    """Validate the original app's 0-250 minute, 10-minute-step range."""
    minutes = vol.Coerce(int)(value)
    if minutes < 0 or minutes > 250 or minutes % 10:
        raise vol.Invalid("must be 0-250 in steps of 10")
    return minutes


SET_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): cv.string,
        vol.Required("day"): vol.All(vol.Coerce(int), vol.Range(min=0, max=6)),
        vol.Required("start_time"): cv.time,
        vol.Required("edge_mowing", default=False): cv.boolean,
        **{
            vol.Required(f"area_{number}_minutes", default=0): _area_minutes
            for number in range(1, 7)
        },
    }
)


@dataclass(slots=True)
class LyfcoRuntimeData:
    """Runtime objects associated with a config entry."""

    client: LyfcoMowerClient
    coordinator: LyfcoCoordinator


type LyfcoConfigEntry = ConfigEntry[LyfcoRuntimeData]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register integration-level actions."""

    async def async_set_schedule(call: ServiceCall) -> None:
        entry = hass.config_entries.async_get_entry(call.data["config_entry_id"])
        if entry is None or entry.domain != DOMAIN or not hasattr(entry, "runtime_data"):
            raise HomeAssistantError("The selected Lyfco mower is not loaded")
        start: time = call.data["start_time"]
        area_minutes = tuple(
            call.data[f"area_{number}_minutes"] for number in range(1, 7)
        )
        try:
            await entry.runtime_data.client.async_set_schedule(
                day=call.data["day"],
                start_hour=start.hour,
                start_minute=start.minute,
                edge_mowing=call.data["edge_mowing"],
                area_minutes=area_minutes,
            )
        except LyfcoError as error:
            raise HomeAssistantError(str(error)) from error
        await entry.runtime_data.coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SCHEDULE,
        async_set_schedule,
        schema=SET_SCHEDULE_SCHEMA,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: LyfcoConfigEntry) -> bool:
    """Set up Lyfco from a config entry."""
    client = LyfcoMowerClient(entry.data[CONF_HOST])
    coordinator = LyfcoCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = LyfcoRuntimeData(client, coordinator)

    # Home Assistant 2026.8 restricts each device to one config entry. Create
    # the mower explicitly for this entry before forwarding its entity
    # platforms instead of relying only on implicit DeviceInfo processing.
    host = entry.data[CONF_HOST]
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, host)},
        name=DEFAULT_NAME,
        manufacturer="Lyfco",
        model=coordinator.data.model
        or "Robot mower (local LAN protocol)",
        sw_version=coordinator.data.firmware,
    )

    # v0.5.1 replaced the optimistic blade switch with a stateless button.
    # Remove the obsolete registry entry so it does not remain unavailable.
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        obsolete_schedule_sensor = (
            entity.domain == "sensor"
            and entity.platform == DOMAIN
            and "_schedule_" in entity.unique_id
        )
        obsolete_blade_switch = (
            entity.domain == "switch"
            and entity.platform == DOMAIN
            and entity.unique_id.endswith("_blade")
        )
        if obsolete_schedule_sensor or obsolete_blade_switch:
            registry.async_remove(entity.entity_id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start_schedule_tracker()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LyfcoConfigEntry) -> bool:
    """Unload a config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    entry.runtime_data.coordinator.async_stop_schedule_tracker()
    await entry.runtime_data.client.async_close()
    return True
