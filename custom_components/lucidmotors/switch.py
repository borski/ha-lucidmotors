"""Switch entities for Lucid vehicles."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
import logging
from typing import Any

from lucidmotors import APIError, LucidAPI, Vehicle, DefrostState, ChargeState

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
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
class LucidSwitchEntityDescriptionMixin:
    """Mixin to describe a Lucid Switch entity."""

    key_path: list[str]
    turn_on_function: Callable[[LucidAPI, Vehicle], Coroutine[None, None, None]]
    turn_off_function: Callable[[LucidAPI, Vehicle], Coroutine[None, None, None]]
    on_value: Any


@dataclass(frozen=True)
class LucidSwitchEntityDescription(
    SwitchEntityDescription, LucidSwitchEntityDescriptionMixin
):
    """Describes Lucid switch entity."""


SWITCH_TYPES: tuple[LucidSwitchEntityDescription, ...] = (
    LucidSwitchEntityDescription(
        key="charge_state",
        key_path=["state", "charging"],
        translation_key="charging",
        icon="mdi:ev-station",
        device_class=SwitchDeviceClass.SWITCH,
        turn_on_function=lambda api, vehicle: api.start_charging(vehicle),
        turn_off_function=lambda api, vehicle: api.stop_charging(vehicle),
        on_value=ChargeState.CHARGE_STATE_CHARGING,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Lucid sensors from config entry."""
    coordinator: LucidDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities: list[LucidSwitch] = []

    for vehicle in coordinator.api.vehicles:
        entities.extend(
            [
                LucidSwitch(coordinator, vehicle, description)
                for description in SWITCH_TYPES
            ]
        )

    async_add_entities(entities)


class LucidSwitch(LucidBaseEntity, SwitchEntity):
    """Representation of a Lucid vehicle switch."""

    entity_description: LucidSwitchEntityDescription
    _attr_has_entity_name: bool = True
    _is_on: bool

    def __init__(
        self,
        coordinator: LucidDataUpdateCoordinator,
        vehicle: Vehicle,
        description: LucidSwitchEntityDescription,
    ) -> None:
        """Initialize Lucid vehicle switch."""
        super().__init__(coordinator, vehicle)
        self.entity_description = description
        self.api = coordinator.api
        self._attr_unique_id = f"{vehicle.config.vin}-{description.key}"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _LOGGER.debug(
            "Updating switch '%s' of %s",
            self.entity_description.key,
            self.vehicle.config.nickname,
        )
        state = self.vehicle
        for attr in self.entity_description.key_path:
            state = getattr(state, attr)
        state = getattr(state, self.entity_description.key)

        self._is_on = state == self.entity_description.on_value
        super()._handle_coordinator_update()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch."""
        try:
            await self.entity_description.turn_on_function(self.api, self.vehicle)
            # Update our local state for the entity so that it doesn't appear
            # to revert to its previous state until the next API update
            self._is_on = True
            self.async_write_ha_state()
        except APIError as ex:
            raise HomeAssistantError(ex) from ex

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch."""
        try:
            await self.entity_description.turn_off_function(self.api, self.vehicle)
            self._is_on = False
            self.async_write_ha_state()
        except APIError as ex:
            raise HomeAssistantError(ex) from ex

    @property
    def is_on(self) -> bool:
        """Get the current state of the switch."""
        return self._is_on
