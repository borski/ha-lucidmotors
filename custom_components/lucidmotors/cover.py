"""Cover entities for Lucid vehicles."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
import logging
from typing import Any

from lucidmotors import APIError, LucidAPI, Vehicle, DoorState, StrutType

from homeassistant.components.cover import CoverDeviceClass, CoverEntity, CoverEntityFeature, CoverEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LucidBaseEntity
from .const import DOMAIN
from .coordinator import LucidDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

@dataclass(frozen=True)
class LucidCoverEntityDescriptionMixin:
    """Mixin to describe a Lucid cover entity."""

    key_path: list[str]
    open_function: Callable[[LucidAPI, Vehicle], Coroutine[None, None, None]]
    close_function: Callable[[LucidAPI, Vehicle], Coroutine[None, None, None]]
    open_value: Any


@dataclass(frozen=True)
class LucidCoverEntityDescription(
    CoverEntityDescription, LucidCoverEntityDescriptionMixin
):
    """Describes Lucid cover entity."""


COVER_TYPES: tuple[LucidCoverEntityDescription, ...] = (
    LucidCoverEntityDescription(
        key="charge_port",
        key_path=["state", "body"],
        translation_key="charge_port_door",
        icon="mdi:ev-plug-ccs1",
        open_value=DoorState.DOOR_STATE_OPEN,
        close_function=lambda api, vehicle: api.charge_port_close(vehicle),
        open_function=lambda api, vehicle: api.charge_port_open(vehicle),
    ),
    LucidCoverEntityDescription(
        key="rear_cargo",
        key_path=["state", "body"],
        translation_key="rear_cargo",
        icon="mdi:car-sports",
        open_value=DoorState.DOOR_STATE_OPEN,
        close_function=lambda api, vehicle: api.trunk_close(vehicle),
        open_function=lambda api, vehicle: api.trunk_open(vehicle),
    ),
    LucidCoverEntityDescription(
        key="front_cargo",
        key_path=["state", "body"],
        translation_key="front_cargo",
        icon="mdi:car-sports",
        open_value=DoorState.DOOR_STATE_OPEN,
        close_function=lambda api, vehicle: api.frunk_close(vehicle),
        open_function=lambda api, vehicle: api.frunk_open(vehicle),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Lucid sensors from config entry."""
    coordinator: LucidDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities: list[LucidCover] = []

    for vehicle in coordinator.api.vehicles:
        entities.extend(
            [LucidCover(coordinator, vehicle, description) for description in COVER_TYPES]
        )

    async_add_entities(entities)


class LucidCover(LucidBaseEntity, CoverEntity):
    """Representation of a Lucid cover."""

    entity_description: LucidCoverEntityDescription
    _attr_has_entity_name: bool = True
    _is_on: bool

    def __init__(
        self,
        coordinator: LucidDataUpdateCoordinator,
        vehicle: Vehicle,
        description: LucidCoverEntityDescription,
    ) -> None:
        """Initialize Lucid cover."""
        super().__init__(coordinator, vehicle)
        self.entity_description = description
        self.api = coordinator.api
        self._attr_unique_id = f"{vehicle.config.vin}-{description.key}"
        self._attr_supported_features = CoverEntityFeature.OPEN
        # Note: Pure's frunk has STRUT_TYPE_GAS, not powered open/close. Close
        # doesn't actually do anything.
        if (
            description.key != "front_cargo" or
            vehicle.config.frunk_strut == StrutType.STRUT_TYPE_POWER
        ):
            self._attr_supported_features |= CoverEntityFeature.CLOSE
        self._attr_device_class = CoverDeviceClass.DOOR

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close cover."""

        _LOGGER.debug(
            "Closing %s of %s",
            self.entity_description.key,
            self.vehicle.config.nickname,
        )

        try:
            await self.entity_description.close_function(self.api, self.vehicle)
            # Update our local state for the entity so that it doesn't appear
            # to revert to its previous state until the next API update
            self._attr_is_closed = True
            self.async_write_ha_state()
        except APIError as ex:
            raise HomeAssistantError(ex) from ex

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open cover."""

        _LOGGER.debug(
            "Opening %s of %s",
            self.entity_description.key,
            self.vehicle.config.nickname,
        )

        try:
            await self.entity_description.open_function(self.api, self.vehicle)
            self._attr_is_closed = False
            self.async_write_ha_state()
        except APIError as ex:
            raise HomeAssistantError(ex) from ex

    @property
    def is_closed(self):
        """Returns True if cover is closed."""
        return self._attr_is_closed

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""

        _LOGGER.debug(
            "Updating cover '%s' of %s",
            self.entity_description.key,
            self.vehicle.config.nickname,
        )

        state = self.vehicle
        for attr in self.entity_description.key_path:
            state = getattr(state, attr)
        state = getattr(state, self.entity_description.key)

        self._attr_is_closed = state != self.entity_description.open_value
        super()._handle_coordinator_update()
