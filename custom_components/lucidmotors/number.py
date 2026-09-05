"""Button entities for Lucid vehicles."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
import logging

from lucidmotors import Vehicle, APIError, LucidAPI

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.const import PERCENTAGE
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
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
    native_value_fn: Callable[[Vehicle], float]
    set_native_value_fn: Callable[
        [LucidAPI, Vehicle, float], Coroutine[None, None, None]
    ]


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
        native_min_value=50.0,  # Enforced by Lucid API
        native_value_fn=lambda vehicle: round(
            vehicle.state.charging.charge_limit_percent
        ),
        set_native_value_fn=lambda api, vehicle, value: api.set_charge_limit(
            vehicle, round(value)
        ),
    ),
    LucidNumberEntityDescription(
        key="energy_ac_current_limit",
        key_path=["state", "charging"],
        translation_key="ac_charge_current_limit",
        icon="mdi:current-ac",
        native_step=1,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        # 8 A is the lowest current IEC 61851 permits for AC charging; the
        # upper bound is the Air's 19.2 kW onboard charger at 240 V.
        native_min_value=8.0,
        native_max_value=80.0,
        native_value_fn=lambda vehicle: (
            vehicle.state.charging.energy_ac_current_limit or 80
        ),
        set_native_value_fn=lambda api, vehicle, value: api.set_ac_current_limit(
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
    # Holds the value we just sent to the car until the coordinator confirms
    # it with a fresh vehicle poll. Without this, async_write_ha_state()
    # immediately re-reads native_value_fn from the stale cache and the
    # slider snaps back to the old value.
    _optimistic_value: float | None = None

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

    @callback
    def _handle_coordinator_update(self) -> None:
        """Drop the optimistic value once fresh vehicle data arrives."""
        self._optimistic_value = None
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float:
        """Return native value, preferring a pending optimistic write."""
        if self._optimistic_value is not None:
            return self._optimistic_value
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
            # Show the new value immediately instead of the stale cached one.
            self._optimistic_value = value
            self.async_write_ha_state()
        except APIError as ex:
            raise HomeAssistantError(ex) from ex
