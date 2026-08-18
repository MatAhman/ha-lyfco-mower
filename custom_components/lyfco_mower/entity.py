"""Base entity for EGROBOT-compatible robot mowers."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import LyfcoConfigEntry
from .const import DEFAULT_NAME, DOMAIN
from .coordinator import LyfcoCoordinator


class LyfcoEntity(CoordinatorEntity[LyfcoCoordinator]):
    """Base class shared by all mower entities."""

    _attr_has_entity_name = True

    def __init__(self, entry: LyfcoConfigEntry, key: str) -> None:
        super().__init__(entry.runtime_data.coordinator)
        host = entry.data["host"]
        self._attr_unique_id = f"{host}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, host)},
            name=DEFAULT_NAME,
            model=self.coordinator.data.model
            or "Robot mower (local EGROBOT/Miotlink protocol)",
            sw_version=self.coordinator.data.firmware,
        )
