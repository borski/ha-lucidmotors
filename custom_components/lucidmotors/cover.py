"""Cover entities for Lucid vehicles."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
import logging
from typing import Any

from lucidmotors import (
    APIError,
    LucidAPI,
    Vehicle,
    DoorState,
    StrutType,
    WindowPositionStatus,
    AllWindowPosition,
)

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
    CoverEntityDescription,
)
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
    open_function: Callable[[LucidAPI, Vehicle], Coroutine[None, None, None]] | None
    close_function: Callable[[LucidAPI, Vehicle], Coroutine[None, None, None]] | None
    is_open: Callable[[Vehicle, Any], bool]
    is_closed: Callable[[Vehicle, Any], bool] | None
    position: Callable[[Vehicle, Any], int | None] | None
    features: CoverEntityFeature
    cover_device_class: CoverDeviceClass


@dataclass(frozen=True)
class LucidCoverEntityDescription(
    CoverEntityDescription, LucidCoverEntityDescriptionMixin
):
    """Describes Lucid cover entity."""


# Kind of arbitrary relative mapping of strange overly verbose "how open is my
# window" descriptions from Lucid to percentages for the HA Cover entity
# TODO: WINDOW_POSITION_STATUS_BETWEEN_FULLY_CLOSED_AND_SHORT_DROP_DOWN goes where?
WINDOW_POSITION_AS_PERCENT = {
    WindowPositionStatus.WINDOW_POSITION_STATUS_FULLY_CLOSED: 0,
    WindowPositionStatus.WINDOW_POSITION_STATUS_ABOVE_SHORT_DROP_POSITION: 25,
    WindowPositionStatus.WINDOW_POSITION_STATUS_SHORT_DROP_POSITION: 50,
    WindowPositionStatus.WINDOW_POSITION_STATUS_BETWEEN_SHORT_DROP_DOWN_AND_FULLY_OPEN: 50,
    WindowPositionStatus.WINDOW_POSITION_STATUS_BELOW_SHORT_DROP_POSITION: 75,
    WindowPositionStatus.WINDOW_POSITION_STATUS_FULLY_OPEN: 100,
}


COVER_TYPES: tuple[LucidCoverEntityDescription, ...] = (
    LucidCoverEntityDescription(
        key="charge_port",
        key_path=["state", "body"],
        translation_key="charge_port_door",
        icon="mdi:ev-plug-ccs1",
        is_open=lambda vehicle, state: state == DoorState.DOOR_STATE_OPEN,
        is_closed=None,
        position=None,
        close_function=lambda api, vehicle: api.charge_port_close(vehicle),
        open_function=lambda api, vehicle: api.charge_port_open(vehicle),
        features=CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE,
        cover_device_class=CoverDeviceClass.DOOR,
    ),
    LucidCoverEntityDescription(
        key="rear_cargo",
        key_path=["state", "body"],
        translation_key="rear_cargo",
        icon="mdi:car-sports",
        is_open=lambda vehicle, state: state == DoorState.DOOR_STATE_OPEN,
        is_closed=None,
        position=None,
        close_function=lambda api, vehicle: api.trunk_close(vehicle),
        open_function=lambda api, vehicle: api.trunk_open(vehicle),
        features=CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE,
        cover_device_class=CoverDeviceClass.DOOR,
    ),
    LucidCoverEntityDescription(
        key="front_cargo",
        key_path=["state", "body"],
        translation_key="front_cargo",
        icon="mdi:car-sports",
        is_open=lambda vehicle, state: state == DoorState.DOOR_STATE_OPEN,
        is_closed=None,
        position=None,
        close_function=lambda api, vehicle: api.frunk_close(vehicle),
        open_function=lambda api, vehicle: api.frunk_open(vehicle),
        features=CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE,
        cover_device_class=CoverDeviceClass.DOOR,
    ),
    LucidCoverEntityDescription(
        key="left_front",
        key_path=["state", "body", "window_position"],
        translation_key="left_front_window",
        icon="mdi:car-door",
        is_open=lambda vehicle, state: state
        == WindowPositionStatus.WINDOW_POSITION_STATUS_FULLY_OPEN,
        is_closed=lambda vehicle, state: state
        == WindowPositionStatus.WINDOW_POSITION_STATUS_FULLY_CLOSED,
        position=lambda vehicle, state: WINDOW_POSITION_AS_PERCENT.get(state, None),
        close_function=None,
        open_function=None,
        features=CoverEntityFeature(0),
        cover_device_class=CoverDeviceClass.WINDOW,
    ),
    LucidCoverEntityDescription(
        key="right_front",
        key_path=["state", "body", "window_position"],
        translation_key="right_front_window",
        icon="mdi:car-door",
        is_open=lambda vehicle, state: state
        == WindowPositionStatus.WINDOW_POSITION_STATUS_FULLY_OPEN,
        is_closed=lambda vehicle, state: state
        == WindowPositionStatus.WINDOW_POSITION_STATUS_FULLY_CLOSED,
        position=lambda vehicle, state: WINDOW_POSITION_AS_PERCENT.get(state, None),
        close_function=None,
        open_function=None,
        features=CoverEntityFeature(0),
        cover_device_class=CoverDeviceClass.WINDOW,
    ),
    LucidCoverEntityDescription(
        key="left_rear",
        key_path=["state", "body", "window_position"],
        translation_key="left_rear_window",
        icon="mdi:car-door",
        is_open=lambda vehicle, state: state
        == WindowPositionStatus.WINDOW_POSITION_STATUS_FULLY_OPEN,
        is_closed=lambda vehicle, state: state
        == WindowPositionStatus.WINDOW_POSITION_STATUS_FULLY_CLOSED,
        position=lambda vehicle, state: WINDOW_POSITION_AS_PERCENT.get(state, None),
        close_function=None,
        open_function=None,
        features=CoverEntityFeature(0),
        cover_device_class=CoverDeviceClass.WINDOW,
    ),
    LucidCoverEntityDescription(
        key="right_rear",
        key_path=["state", "body", "window_position"],
        translation_key="right_rear_window",
        icon="mdi:car-door",
        is_open=lambda vehicle, state: state
        == WindowPositionStatus.WINDOW_POSITION_STATUS_FULLY_OPEN,
        is_closed=lambda vehicle, state: state
        == WindowPositionStatus.WINDOW_POSITION_STATUS_FULLY_CLOSED,
        position=lambda vehicle, state: WINDOW_POSITION_AS_PERCENT.get(state, None),
        close_function=None,
        open_function=None,
        features=CoverEntityFeature(0),
        cover_device_class=CoverDeviceClass.WINDOW,
    ),
    LucidCoverEntityDescription(
        key="all_windows_position",
        key_path=["state", "body"],
        translation_key="all_windows",
        icon="mdi:car-door",
        is_open=lambda vehicle, state: state
        == AllWindowPosition.ALL_WINDOW_POSITION_OPEN,
        is_closed=lambda vehicle, state: state
        == AllWindowPosition.ALL_WINDOW_POSITION_CLOSED,
        position=None,
        close_function=lambda api, vehicle: api.close_all_windows(vehicle),
        open_function=lambda api, vehicle: api.open_all_windows(vehicle),
        features=CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE,
        cover_device_class=CoverDeviceClass.WINDOW,
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
            [
                LucidCover(coordinator, vehicle, description)
                for description in COVER_TYPES
            ]
        )

    async_add_entities(entities)


class LucidCover(LucidBaseEntity, CoverEntity):
    """Representation of a Lucid cover."""

    entity_description: LucidCoverEntityDescription
    _attr_has_entity_name: bool = True

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
        self._attr_unique_id = f"{vehicle.config.vin}-{description.translation_key}"
        self._attr_supported_features = description.features
        self._attr_device_class = description.cover_device_class

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close cover."""

        _LOGGER.debug(
            "Closing %s of %s",
            self.entity_description.key,
            self.vehicle.config.nickname,
        )

        assert self.entity_description.close_function is not None
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

        assert self.entity_description.open_function is not None
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

        if self.entity_description.is_closed is None:
            self._attr_is_closed = not self.entity_description.is_open(
                self.vehicle, state
            )
        else:
            self._attr_is_closed = self.entity_description.is_closed(
                self.vehicle, state
            )

        if self.entity_description.position is not None:
            self._attr_current_cover_position = self.entity_description.position(
                self.vehicle, state
            )
            _LOGGER.debug(
                "Cover position '%s' is %r",
                self.entity_description.key,
                self._attr_current_cover_position,
            )

        super()._handle_coordinator_update()
