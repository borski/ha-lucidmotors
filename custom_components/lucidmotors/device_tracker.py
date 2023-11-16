"""Device tracker for Lucid vehicles."""
from __future__ import annotations

import logging
from typing import Any

from lucidmotors import Vehicle

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LucidBaseEntity
from .const import ATTR_DIRECTION, ATTR_ELEVATION, ATTR_POSITION_TIME, DOMAIN
from .coordinator import LucidDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Lucid tracker from config entry."""
    coordinator: LucidDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    entities: list[LucidTrackerEntity] = []

    for vehicle in coordinator.api.vehicles:
        entities.append(LucidTrackerEntity(coordinator, vehicle))

    async_add_entities(entities)


class LucidTrackerEntity(LucidBaseEntity, TrackerEntity):
    """Tracker for Lucid vehicles."""

    _attr_force_update: bool = False
    _attr_icon: str = "mdi:car"

    def __init__(
        self, coordinator: LucidDataUpdateCoordinator, vehicle: Vehicle
    ) -> None:
        """Initialize the vehicle tracker."""
        super().__init__(coordinator, vehicle)

        self._attr_unique_id = self.vehicle.config.vin
        self._attr_name = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        return {
            **self._attrs,
            ATTR_DIRECTION: self.vehicle.state.gps.heading_precise,
            ATTR_ELEVATION: self.vehicle.state.gps.elevation,
            ATTR_POSITION_TIME: self.vehicle.state.gps.position_time,
        }

    @property
    def latitude(self) -> float | None:
        """Return latitude value of the vehicle."""
        return self.vehicle.state.gps.location.latitude

    @property
    def longitude(self) -> float | None:
        """Return longitude value of the vehicle."""
        return self.vehicle.state.gps.location.longitude

    @property
    def source_type(self) -> SourceType:
        """Return the source type, eg gps or router, of the vehicle."""
        return SourceType.GPS
