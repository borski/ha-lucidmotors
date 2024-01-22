"""Switch entities for Lucid vehicles."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
import logging
from typing import Any

from lucidmotors import APIError, LucidAPI, Vehicle, LightState

from homeassistant.components.light import (
    ATTR_FLASH,
    ColorMode,
    LightEntity,
    LightEntityDescription,
    LightEntityFeature,
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
class LucidLightEntityDescriptionMixin:
    """Mixin to describe a Lucid Light entity."""

    key_path: list[str]
    turn_on_function: Callable[[LucidAPI, Vehicle], Coroutine[None, None, None]]
    turn_off_function: Callable[[LucidAPI, Vehicle], Coroutine[None, None, None]]
    flash_function: Callable[[LucidAPI, Vehicle], Coroutine[None, None, None]]
    off_value: Any
    on_value: Any


@dataclass(frozen=True)
class LucidLightEntityDescription(
    LightEntityDescription, LucidLightEntityDescriptionMixin
):
    """Describes Lucid light entity."""


LIGHT_TYPES: tuple[LucidLightEntityDescription, ...] = (
    LucidLightEntityDescription(
        key="headlights",
        key_path=["state", "chassis"],
        translation_key="headlights",
        icon="mdi:car-light-high",
        turn_on_function=lambda api, vehicle: api.lights_on(vehicle),
        turn_off_function=lambda api, vehicle: api.lights_off(vehicle),
        flash_function=lambda api, vehicle: api.lights_flash(vehicle),
        off_value=LightState.LIGHT_STATE_OFF,
        on_value=LightState.LIGHT_STATE_ON,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Lucid sensors from config entry."""
    coordinator: LucidDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities: list[LucidLight] = []

    for vehicle in coordinator.api.vehicles:
        entities.extend(
            [
                LucidLight(coordinator, vehicle, description)
                for description in LIGHT_TYPES
            ]
        )

    async_add_entities(entities)


class LucidLight(LucidBaseEntity, LightEntity):
    """Representation of a Lucid vehicle light."""

    entity_description: LucidLightEntityDescription
    _attr_has_entity_name: bool = True
    _is_on: bool | None

    def __init__(
        self,
        coordinator: LucidDataUpdateCoordinator,
        vehicle: Vehicle,
        description: LucidLightEntityDescription,
    ) -> None:
        """Initialize Lucid vehicle light."""
        super().__init__(coordinator, vehicle)
        self.entity_description = description
        self.api = coordinator.api
        self._attr_unique_id = f"{vehicle.config.vin}-{description.key}"
        self._attr_supported_color_modes = {ColorMode.ONOFF}
        self._attr_color_mode = ColorMode.ONOFF
        self._attr_supported_features = LightEntityFeature.FLASH

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _LOGGER.debug(
            "Updating light '%s' of %s",
            self.entity_description.key,
            self.vehicle.config.nickname,
        )
        state = self.vehicle
        for attr in self.entity_description.key_path:
            state = getattr(state, attr)
        state = getattr(state, self.entity_description.key)
        # Using != off_value rather than == on_value so that UNKNOWN states
        # will be considered on. This may not always be the right answer, but I
        # think it's better to turn unknown things off rather than on?
        if state == self.entity_description.off_value:
            self._is_on = False
        elif state == self.entity_description.on_value:
            self._is_on = True
        else:
            self._is_on = None
        super()._handle_coordinator_update()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light."""
        if ATTR_FLASH in kwargs:
            await self.entity_description.flash_function(self.api, self.vehicle)
        else:
            try:
                await self.entity_description.turn_on_function(self.api, self.vehicle)
                self.async_write_ha_state()
            except APIError as ex:
                raise HomeAssistantError(ex) from ex
        path = tuple(self.entity_description.key_path + [self.entity_description.key])
        await self.coordinator.expect_update(self.vehicle.config.vin, path)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch."""
        try:
            await self.entity_description.turn_off_function(self.api, self.vehicle)
            self.async_write_ha_state()
        except APIError as ex:
            raise HomeAssistantError(ex) from ex
        path = tuple(self.entity_description.key_path + [self.entity_description.key])
        await self.coordinator.expect_update(self.vehicle.config.vin, path)

    @property
    def is_on(self) -> bool | None:
        """Get the current state of the switch."""
        return self._is_on
