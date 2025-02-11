"""Climate control entity for Lucid vehicles."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Optional

from lucidmotors import APIError, Vehicle, HvacPower, DefrostState, MaxACState
from lucidmotors.const import (
    PRECONDITION_TEMPERATURE_MIN,
    PRECONDITION_TEMPERATURE_MAX,
)

from homeassistant.components.climate import (
    PRESET_NONE,
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
from .const import DOMAIN, DEFAULT_TARGET_TEMPERATURE
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


CLIMATE_DESCRIPTION: LucidClimateEntityDescription = LucidClimateEntityDescription(
    key="climate",
    name="Climate Control",
    translation_key="climate",
    icon="mdi:thermometer-auto",
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
        entities.append(LucidClimate(coordinator, vehicle, CLIMATE_DESCRIPTION))

    async_add_entities(entities)


class LucidClimate(LucidBaseEntity, ClimateEntity):
    """Representation of a Lucid vehicle climate control."""

    entity_description: LucidClimateEntityDescription

    _attr_hvac_modes: list[HVACMode] = [
        HVACMode.OFF,
        HVACMode.HEAT_COOL,
    ]
    _attr_preset_modes = [PRESET_NONE, "Defrost", "Max A/C"]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp: float = PRECONDITION_TEMPERATURE_MIN
    _attr_max_temp: float = PRECONDITION_TEMPERATURE_MAX
    _attr_preset_mode: Optional[str] = None
    _attr_target_temperature: Optional[float] = DEFAULT_TARGET_TEMPERATURE

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
        self._attr_preset_mode = PRESET_NONE

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _LOGGER.debug(
            "Updating climate entity for %s",
            self.vehicle.config.nickname,
        )

        # Update entity attributes
        self._attr_current_temperature = self.vehicle.state.cabin.interior_temp
        self._attr_target_temperature = (
            self.vehicle.state.hvac.front_left_set_temperature
        )

        match self.vehicle.state.hvac.power:
            case HvacPower.HVAC_ON | HvacPower.HVAC_PRECONDITION:
                target = self._attr_target_temperature
                if self.vehicle.state.hvac.defrost == DefrostState.DEFROST_ON:
                    self._attr_hvac_mode = HVACMode.HEAT_COOL
                    self._attr_hvac_action = HVACAction.HEATING
                elif (
                    self.vehicle.state.hvac.max_ac_status == MaxACState.MAX_AC_STATE_ON
                ):
                    self._attr_hvac_mode = HVACMode.HEAT_COOL
                    self._attr_hvac_action = HVACAction.COOLING
                else:
                    self._attr_supported_features |= (
                        ClimateEntityFeature.TARGET_TEMPERATURE
                    )
                    current = self.vehicle.state.cabin.interior_temp
                    self._attr_hvac_mode = HVACMode.HEAT_COOL
                    target = self.vehicle.state.hvac.front_left_set_temperature
                    if current >= target:
                        self._attr_hvac_action = HVACAction.COOLING
                    else:
                        self._attr_hvac_action = HVACAction.HEATING
            case HvacPower.HVAC_OFF:
                self._attr_hvac_action = HVACAction.OFF
                self._attr_hvac_mode = HVACMode.OFF
                self._attr_supported_features &= (
                    ~ClimateEntityFeature.TARGET_TEMPERATURE
                )
            case _:
                self._attr_hvac_action = None
                self._attr_hvac_mode = None
                self._attr_supported_features &= (
                    ~ClimateEntityFeature.TARGET_TEMPERATURE
                )

        if self.vehicle.state.hvac.defrost == DefrostState.DEFROST_ON:
            self._attr_preset_mode = "Defrost"
            self._attr_supported_features &= ~ClimateEntityFeature.TARGET_TEMPERATURE
        elif self.vehicle.state.hvac.max_ac_status == MaxACState.MAX_AC_STATE_ON:
            self._attr_preset_mode = "Max A/C"
            self._attr_supported_features &= ~ClimateEntityFeature.TARGET_TEMPERATURE
        else:
            self._attr_preset_mode = PRESET_NONE

        _LOGGER.info(
            "HVAC power: %r; action: %r; mode: %r; target: %r; current: %r",
            self.vehicle.state.hvac.power,
            self._attr_hvac_action,
            self._attr_hvac_mode,
            self._attr_target_temperature,
            self.vehicle.state.cabin.interior_temp,
        )

        super()._handle_coordinator_update()

    async def _expect_update(self) -> None:
        await self.coordinator.expect_update(
            self.vehicle.config.vin,
            ("state", "hvac"),
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        _LOGGER.debug(
            "Setting preset mode of %s to %s",
            self.vehicle.config.nickname,
            preset_mode,
        )

        current_mode = self._attr_preset_mode

        self._attr_preset_mode = preset_mode
        self.async_write_ha_state()

        if preset_mode == PRESET_NONE:
            if current_mode == "Defrost":
                await self.api.defrost_off(self.vehicle)
            elif current_mode == "Max A/C":
                await self.api.max_ac_off(self.vehicle)
        elif preset_mode == "Defrost":
            if current_mode == "Max A/C":
                await self.api.max_ac_off(self.vehicle)
            await self.api.defrost_on(self.vehicle)
        elif preset_mode == "Max A/C":
            if current_mode == "Defrost":
                await self.api.defrost_off(self.vehicle)
            await self.api.max_ac_on(self.vehicle)

        await self._expect_update()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""
        _LOGGER.debug(
            "Setting HVAC mode of %s to %r",
            self.vehicle.config.nickname,
            hvac_mode,
        )

        match hvac_mode:
            case HVACMode.OFF:
                target = None
            case HVACMode.HEAT_COOL:
                target = self._attr_target_temperature
            case _:
                _LOGGER.error(
                    "Climate control for %s does not support mode %r",
                    self.vehicle.config.nickname,
                    hvac_mode,
                )
                raise NotImplementedError()

        await self.async_set_temperature(temperature=target)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set temperature."""
        temperature = kwargs.get("temperature")

        hvac_mode = self.hvac_mode

        _LOGGER.info("async_set_temperature: %r", kwargs)
        _LOGGER.info("hvac_mode: %r", hvac_mode)

        _LOGGER.debug(
            "Setting temperature of %s to %r",
            self.vehicle.config.nickname,
            temperature,
        )

        try:
            await self.api.set_cabin_temperature(self.vehicle, temperature)
            await self._expect_update()
            self.async_write_ha_state()
        except APIError as ex:
            raise HomeAssistantError(ex) from ex
