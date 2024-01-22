"""Button entities for Lucid vehicles."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
import logging

from lucidmotors import Vehicle, APIError, LucidAPI

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.const import PERCENTAGE
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LucidBaseEntity
from .const import DOMAIN
from .coordinator import LucidDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LucidNumberEntityDescriptionMixin:
    """Mixin to describe a Lucid number entity."""

    key_path: list[str]
    native_value_fn: Callable[[Vehicle], Coroutine[None, None, None]]
    set_native_value_fn: Callable[[LucidAPI, Vehicle, float], Coroutine[None, None, None]]


@dataclass(frozen=True)
class LucidNumberEntityDescription(
    NumberEntityDescription, LucidNumberEntityDescriptionMixin
):
    """Describes Lucid number entity."""


NUMBER_TYPES: tuple[LucidNumberEntityDescription, ...] = (
    LucidNumberEntityDescription(
        key="charge_limit_percent",
        key_path=["state", "charging"],
        translation_key="charging_target",
        icon="mdi:ev-station",
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        native_value_fn=lambda vehicle: round(
            vehicle.state.charging.charge_limit_percent
        ),
        set_native_value_fn=lambda api, vehicle, value: api.set_charge_limit(
            vehicle, round(value)
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Lucid numbers from config entry."""
    coordinator: LucidDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities: list[LucidNumber] = []

    for vehicle in coordinator.api.vehicles:
        entities.extend(
            [
                LucidNumber(coordinator, vehicle, description)
                for description in NUMBER_TYPES
            ]
        )

    async_add_entities(entities)


class LucidNumber(LucidBaseEntity, NumberEntity):
    """Representation of a Lucid vehicle number."""

    entity_description: LucidNumberEntityDescription
    _attr_has_entity_name: bool = True
    _is_on: bool

    def __init__(
        self,
        coordinator: LucidDataUpdateCoordinator,
        vehicle: Vehicle,
        description: LucidNumberEntityDescription,
    ) -> None:
        """Initialize Lucid vehicle number."""
        super().__init__(coordinator, vehicle)
        self.entity_description = description
        self.api = coordinator.api
        self._attr_unique_id = f"{vehicle.config.vin}-{description.key}"

    @property
    def native_value(self) -> int:
        """Return native value."""
        return self.entity_description.native_value_fn(self.vehicle)

    async def async_set_native_value(self, value: float) -> None:
        """Update value."""

        _LOGGER.debug(
            "Setting %s of %s to %d",
            self.entity_description.key,
            self.vehicle.config.nickname,
            value,
        )

        try:
            await self.entity_description.set_native_value_fn(
                self.api, self.vehicle, value
            )
            self.async_write_ha_state()
        except APIError as ex:
            raise HomeAssistantError(ex) from ex
