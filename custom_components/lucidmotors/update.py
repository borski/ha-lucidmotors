"""Update entity for Lucid vehicles."""
from __future__ import annotations

import logging

from lucidmotors import Vehicle

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LucidBaseEntity
from .const import DOMAIN
from .coordinator import LucidDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Lucid update entity from config entry."""
    coordinator: LucidDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    entities: list[LucidUpdateEntity] = []

    for vehicle in coordinator.api.vehicles:
        entities.append(LucidUpdateEntity(coordinator, vehicle))

    async_add_entities(entities)


class LucidUpdateEntity(LucidBaseEntity, UpdateEntity):
    """Software update entity for Lucid vehicles."""

    _attr_force_update: bool = False
    _attr_icon: str = "mdi:update"
    _attr_supported_features = UpdateEntityFeature.PROGRESS

    def __init__(
        self, coordinator: LucidDataUpdateCoordinator, vehicle: Vehicle
    ) -> None:
        """Initialize the vehicle tracker."""
        super().__init__(coordinator, vehicle)

        self._attr_unique_id = f"{vehicle.config.vin}-update"
        self._attr_name = None

    @property
    def installed_version(self) -> str:
        """Return the current software version of the vehicle."""
        return self.vehicle.state.chassis.software_version

    @property
    def latest_version(self) -> str:
        """Return the latest available software version."""
        # The API reports version 0 if there is no update available.
        if self.vehicle.state.software_update.version_available_raw == 0:
            return self.installed_version
        return self.vehicle.state.software_update.version_available

    @property
    def in_progress(self) -> bool | int:
        """Return whether the update is in progress, and at what percentage."""
        if self.vehicle.state.software_update.state != "IN_PROGRESS":
            return False

        return self.vehicle.state.software_update.percent_complete
