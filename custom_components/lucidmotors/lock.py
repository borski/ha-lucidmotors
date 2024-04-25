"""Switch entities for Lucid vehicles."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
import logging
from typing import Any

from lucidmotors import APIError, LucidAPI, Vehicle, LockState

from homeassistant.components.lock import LockEntity, LockEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LucidBaseEntity
from .const import DOMAIN
from .coordinator import LucidDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LucidLockEntityDescriptionMixin:
    """Mixin to describe a Lucid lock entity."""

    key_path: list[str]
    lock_function: Callable[[LucidAPI, Vehicle], Coroutine[None, None, None]]
    unlock_function: Callable[[LucidAPI, Vehicle], Coroutine[None, None, None]]
    unlocked_value: Any


@dataclass(frozen=True)
class LucidLockEntityDescription(
    LockEntityDescription, LucidLockEntityDescriptionMixin
):
    """Describes Lucid lock entity."""


LOCK_TYPES: tuple[LucidLockEntityDescription, ...] = (
    LucidLockEntityDescription(
        key="door_locks",
        key_path=["state", "body"],
        translation_key="door_locks",
        icon="mdi:car-door-lock",
        unlocked_value=LockState.LOCK_STATE_UNLOCKED,
        lock_function=lambda api, vehicle: api.doors_lock(vehicle),
        unlock_function=lambda api, vehicle: api.doors_unlock(vehicle),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Lucid sensors from config entry."""
    coordinator: LucidDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities: list[LucidLock] = []

    for vehicle in coordinator.api.vehicles:
        entities.extend(
            [LucidLock(coordinator, vehicle, description) for description in LOCK_TYPES]
        )

    async_add_entities(entities)


class LucidLock(LucidBaseEntity, LockEntity):
    """Representation of a Lucid lock."""

    entity_description: LucidLockEntityDescription
    _attr_has_entity_name: bool = True
    _is_on: bool

    def __init__(
        self,
        coordinator: LucidDataUpdateCoordinator,
        vehicle: Vehicle,
        description: LucidLockEntityDescription,
    ) -> None:
        """Initialize Lucid lock."""
        super().__init__(coordinator, vehicle)
        self.entity_description = description
        self.api = coordinator.api
        self._attr_unique_id = f"{vehicle.config.vin}-{description.key}"

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock door."""

        _LOGGER.debug(
            "Locking %s of %s",
            self.entity_description.key,
            self.vehicle.config.nickname,
        )

        try:
            await self.entity_description.lock_function(self.api, self.vehicle)
            # Update our local state for the entity so that it doesn't appear
            # to revert to its previous state until the next API update
            self._attr_is_locked = True
            self.async_write_ha_state()
        except APIError as ex:
            raise HomeAssistantError(ex) from ex

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock door."""

        _LOGGER.debug(
            "Unlocking %s of %s",
            self.entity_description.key,
            self.vehicle.config.nickname,
        )

        try:
            await self.entity_description.unlock_function(self.api, self.vehicle)
            self._attr_is_locked = False
            self.async_write_ha_state()
        except APIError as ex:
            raise HomeAssistantError(ex) from ex

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""

        _LOGGER.debug(
            "Updating lock '%s' of %s",
            self.entity_description.key,
            self.vehicle.config.nickname,
        )

        state = self.vehicle
        for attr in self.entity_description.key_path:
            state = getattr(state, attr)
        state = getattr(state, self.entity_description.key)

        self._attr_is_locked = state != self.entity_description.unlocked_value
        super()._handle_coordinator_update()
