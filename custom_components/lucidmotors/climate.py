"""Climate control entity for Lucid vehicles."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from lucidmotors import APIError, Vehicle, HvacPower
from lucidmotors.const import (
    PRECONDITION_TEMPERATURE_MIN,
    PRECONDITION_TEMPERATURE_MAX,
)

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityDescription,
    ClimateEntityFeature,
    HVACMode,
    HVACAction,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LucidBaseEntity
from .const import DOMAIN
from .coordinator import LucidDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LucidClimateEntityDescriptionMixin:
    """Mixin to describe a Lucid climate entity."""


@dataclass(frozen=True)
class LucidClimateEntityDescription(
    ClimateEntityDescription, LucidClimateEntityDescriptionMixin
):
    """Describes Lucid Climate entity."""


CLIMATE_DESCRIPTION: LucidClimateEntityDescription = (
    LucidClimateEntityDescription(
        key="climate",
        name="Climate Control",
        translation_key="climate",
        icon="mdi:thermometer-auto"
    )
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Lucid sensors from config entry."""
    coordinator: LucidDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities: list[LucidClimate] = []

    for vehicle in coordinator.api.vehicles:
        entities.append(
            LucidClimate(coordinator, vehicle, CLIMATE_DESCRIPTION)
        )

    async_add_entities(entities)


class LucidClimate(LucidBaseEntity, ClimateEntity):
    """Representation of a Lucid vehicle climate control."""

    entity_description: LucidClimateEntityDescription

    _attr_hvac_modes: list[HVACMode] = [
        HVACMode.OFF,
        HVACMode.HEAT_COOL,
    ]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp: float = PRECONDITION_TEMPERATURE_MIN
    _attr_max_temp: float = PRECONDITION_TEMPERATURE_MAX
    _attr_target_temperature: float = 20.0

    def __init__(
        self,
        coordinator: LucidDataUpdateCoordinator,
        vehicle: Vehicle,
        description: LucidClimateEntityDescription,
    ) -> None:
        """Initialize Lucid vehicle climate control."""
        super().__init__(coordinator, vehicle)
        self.entity_description = description
        self.api = coordinator.api
        self._attr_unique_id = f"{vehicle.config.vin}-{description.key}"
        self._attr_hvac_mode = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _LOGGER.debug(
            "Updating climate entity for %s",
            self.vehicle.config.nickname,
        )

        # Update entity attributes
        self._attr_current_temperature = self.vehicle.state.cabin.interior_temp

        match self.vehicle.state.hvac.power:
            case HvacPower.HVAC_ON | HvacPower.HVAC_PRECONDITION:
                target = self.target_temperature
                current = self.vehicle.state.cabin.interior_temp
                self._attr_hvac_mode = HVACMode.HEAT_COOL
                if target is None:
                    self._attr_hvac_action = None
                elif current >= target:
                    self._attr_hvac_action = HVACAction.COOLING
                else:
                    self._attr_hvac_action = HVACAction.HEATING
            case HvacPower.HVAC_OFF:
                self._attr_hvac_action = HVACAction.OFF
                self._attr_hvac_mode = HVACMode.OFF
            case _:
                self._attr_hvac_action = None
                self._attr_hvac_mode = None

        _LOGGER.info(
            "HVAC power: %r; action: %r; mode: %r; target: %r; current: %r",
            self.vehicle.state.hvac.power,
            self._attr_hvac_action,
            self._attr_hvac_mode,
            self.target_temperature,
            self.vehicle.state.cabin.interior_temp,
        )

        super()._handle_coordinator_update()

    async def _expect_update(self):
        await self.coordinator.expect_update(
            self.vehicle.config.vin,
            ('state', 'hvac'),
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""
        _LOGGER.debug(
            "Setting HVAC mode of %s to %r",
            self.vehicle.config.nickname,
            hvac_mode,
        )

        self._attr_hvac_mode = hvac_mode
        await self.async_set_temperature(temperature=self.target_temperature)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set temperature."""
        temperature = kwargs.get("temperature")
        if temperature is not None:
            self._attr_target_temperature = temperature

        hvac_mode = self.hvac_mode

        match hvac_mode:
            case HVACMode.OFF:
                target = None
            case HVACMode.HEAT_COOL:
                target = self.target_temperature
                if target is None:
                    # User just set a mode, but we don't have a target temperature yet.
                    # Make it up?
                    target = 20.0  # degrees celsius
            case _:
                _LOGGER.error(
                    "Climate control for %s does not support mode %r",
                    self.vehicle.config.nickname,
                    hvac_mode,
                )
                raise NotImplementedError()

        _LOGGER.debug(
            "Setting temperature of %s to %r",
            self.vehicle.config.nickname,
            target,
        )

        try:
            await self.api.set_cabin_temperature(self.vehicle, target)
            self.async_write_ha_state()
            await self._expect_update()
        except APIError as ex:
            raise HomeAssistantError(ex) from ex
