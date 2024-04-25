"""Button entities for Lucid vehicles."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
import logging

from lucidmotors import APIError, LucidAPI, Vehicle

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LucidBaseEntity
from .const import DOMAIN
from .coordinator import LucidDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LucidButtonEntityDescriptionMixin:
    """Mixin to describe a Lucid Button entity."""

    remote_function: Callable[[LucidAPI, Vehicle], Coroutine[None, None, None]]


@dataclass(frozen=True)
class LucidButtonEntityDescription(
    ButtonEntityDescription, LucidButtonEntityDescriptionMixin
):
    """Describes Lucid button entity."""


BUTTON_TYPES: tuple[LucidButtonEntityDescription, ...] = (
    LucidButtonEntityDescription(
        key="flash_lights",
        translation_key="flash_lights",
        icon="mdi:car-light-alert",
        remote_function=lambda api, vehicle: api.lights_flash(vehicle),
    ),
    LucidButtonEntityDescription(
        key="wake_up",
        translation_key="wake_up",
        icon="mdi:sleep-off",
        remote_function=lambda api, vehicle: api.wakeup_vehicle(vehicle),
    ),
    LucidButtonEntityDescription(
        key="honk_horn",
        translation_key="honk_horn",
        icon="mdi:bugle",
        remote_function=lambda api, vehicle: api.honk_horn(vehicle),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Lucid sensors from config entry."""
    coordinator: LucidDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities: list[LucidButton] = []

    for vehicle in coordinator.api.vehicles:
        entities.extend(
            [
                LucidButton(coordinator, vehicle, description)
                for description in BUTTON_TYPES
            ]
        )

    async_add_entities(entities)


class LucidButton(LucidBaseEntity, ButtonEntity):
    """Representation of a Lucid vehicle button."""

    entity_description: LucidButtonEntityDescription
    _attr_has_entity_name: bool = True

    def __init__(
        self,
        coordinator: LucidDataUpdateCoordinator,
        vehicle: Vehicle,
        description: LucidButtonEntityDescription,
    ) -> None:
        """Initialize Lucid vehicle button."""
        super().__init__(coordinator, vehicle)
        self.entity_description = description
        self.api = coordinator.api
        self._attr_unique_id = f"{vehicle.config.vin}-{description.key}"

    async def async_press(self) -> None:
        """Press the button."""
        try:
            await self.entity_description.remote_function(self.api, self.vehicle)
        except APIError as ex:
            raise HomeAssistantError(ex) from ex

        self.coordinator.async_update_listeners()
        await self.coordinator.expect_update(self.vehicle.config.vin, ("state",))
