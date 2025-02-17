"""Switch entities for Lucid vehicles."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
import logging
from typing import Any

from lucidmotors import (
    LucidAPI,
    Vehicle,
    AlarmMode,
    SeatClimateMode,
    SteeringHeaterStatus,
    SteeringWheelHeaterLevel,
    FrontSeatsHeatingAvailability,
    FrontSeatsVentilationAvailability,
    SecondRowHeatedSeatsAvailability,
    RearSeatConfig,
    HeatedSteeringWheelAvailability,
)

from homeassistant.components.select import (
    SelectEntity,
    SelectEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LucidBaseEntity
from .const import DOMAIN
from .coordinator import LucidDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

OPTION_TO_MODE_MAP: dict[type, dict[str, int]] = {
    AlarmMode: {
        "Off": AlarmMode.ALARM_MODE_OFF,
        "On": AlarmMode.ALARM_MODE_ON,
        "Push Notifications Only": AlarmMode.ALARM_MODE_SILENT,
    },
    SeatClimateMode: {
        "Off": SeatClimateMode.SEAT_CLIMATE_MODE_OFF,
        "Low": SeatClimateMode.SEAT_CLIMATE_MODE_LOW,
        "Medium": SeatClimateMode.SEAT_CLIMATE_MODE_MEDIUM,
        "High": SeatClimateMode.SEAT_CLIMATE_MODE_HIGH,
    },
    SteeringWheelHeaterLevel: {
        "Off": SteeringWheelHeaterLevel.STEERING_WHEEL_HEATER_LEVEL_OFF,
        "Low": SteeringWheelHeaterLevel.STEERING_WHEEL_HEATER_LEVEL_1,
        "Medium": SteeringWheelHeaterLevel.STEERING_WHEEL_HEATER_LEVEL_2,
        "High": SteeringWheelHeaterLevel.STEERING_WHEEL_HEATER_LEVEL_3,
    },
}
MODE_TO_OPTION_MAP: dict[type, dict[int, str]] = {
    AlarmMode: {
        AlarmMode.ALARM_MODE_OFF: "Off",
        AlarmMode.ALARM_MODE_ON: "On",
        AlarmMode.ALARM_MODE_SILENT: "Push Notifications Only",
    },
    SeatClimateMode: {
        SeatClimateMode.SEAT_CLIMATE_MODE_OFF: "Off",
        SeatClimateMode.SEAT_CLIMATE_MODE_LOW: "Low",
        SeatClimateMode.SEAT_CLIMATE_MODE_MEDIUM: "Medium",
        SeatClimateMode.SEAT_CLIMATE_MODE_HIGH: "High",
    },
    SteeringWheelHeaterLevel: {
        SteeringWheelHeaterLevel.STEERING_WHEEL_HEATER_LEVEL_OFF: "Off",
        SteeringWheelHeaterLevel.STEERING_WHEEL_HEATER_LEVEL_1: "Low",
        SteeringWheelHeaterLevel.STEERING_WHEEL_HEATER_LEVEL_2: "Medium",
        SteeringWheelHeaterLevel.STEERING_WHEEL_HEATER_LEVEL_3: "High",
    },
}


@dataclass(frozen=True)
class LucidSelectEntityDescriptionMixin:
    """Mixin to describe a Lucid select entity."""

    key_path: list[str]
    select_fn: Callable[[LucidAPI, Vehicle, Any], Coroutine[None, None, None]]
    prereq_fn: Callable[[Vehicle], bool]
    enum_type: type


@dataclass(frozen=True)
class LucidSelectEntityDescription(
    SelectEntityDescription, LucidSelectEntityDescriptionMixin
):
    """Describes Lucid select entity."""


SELECT_TYPES: tuple[LucidSelectEntityDescription, ...] = (
    LucidSelectEntityDescription(
        key="mode",
        key_path=["state", "alarm"],
        translation_key="alarm",
        icon="mdi:shield-car",
        options=[*OPTION_TO_MODE_MAP[AlarmMode]],
        select_fn=lambda api, vehicle, mode: api.alarm_control(vehicle, mode),
        prereq_fn=lambda vehicle: True,
        enum_type=AlarmMode,
    ),
    LucidSelectEntityDescription(
        key="driver_heat_backrest_zone3",
        key_path=["state", "hvac", "seats"],
        translation_key="driver_heater_backrest",
        icon="mdi:car-seat-heater",
        options=[*OPTION_TO_MODE_MAP[SeatClimateMode]],
        select_fn=lambda api, vehicle, mode: api.seat_climate_control(
            vehicle, driver_heat_backrest_zone1=mode, driver_heat_backrest_zone3=mode
        ),
        prereq_fn=lambda vehicle: vehicle.config.front_seats_heating
        == FrontSeatsHeatingAvailability.FRONT_SEATS_HEATING_AVAILABLE,
        enum_type=SeatClimateMode,
    ),
    LucidSelectEntityDescription(
        key="driver_heat_cushion_zone4",
        key_path=["state", "hvac", "seats"],
        translation_key="driver_heater_cushion",
        icon="mdi:car-seat-heater",
        options=[*OPTION_TO_MODE_MAP[SeatClimateMode]],
        select_fn=lambda api, vehicle, mode: api.seat_climate_control(
            vehicle, driver_heat_cushion_zone2=mode, driver_heat_cushion_zone4=mode
        ),
        prereq_fn=lambda vehicle: vehicle.config.front_seats_heating
        == FrontSeatsHeatingAvailability.FRONT_SEATS_HEATING_AVAILABLE,
        enum_type=SeatClimateMode,
    ),
    LucidSelectEntityDescription(
        key="front_passenger_heat_backrest_zone3",
        key_path=["state", "hvac", "seats"],
        translation_key="front_passenger_heater_backrest",
        icon="mdi:car-seat-heater",
        options=[*OPTION_TO_MODE_MAP[SeatClimateMode]],
        select_fn=lambda api, vehicle, mode: api.seat_climate_control(
            vehicle,
            front_passenger_heat_backrest_zone1=mode,
            front_passenger_heat_backrest_zone3=mode,
        ),
        prereq_fn=lambda vehicle: vehicle.config.front_seats_heating
        == FrontSeatsHeatingAvailability.FRONT_SEATS_HEATING_AVAILABLE,
        enum_type=SeatClimateMode,
    ),
    LucidSelectEntityDescription(
        key="front_passenger_heat_cushion_zone4",
        key_path=["state", "hvac", "seats"],
        translation_key="front_passenger_heater_cushion",
        icon="mdi:car-seat-heater",
        options=[*OPTION_TO_MODE_MAP[SeatClimateMode]],
        select_fn=lambda api, vehicle, mode: api.seat_climate_control(
            vehicle,
            front_passenger_heat_cushion_zone2=mode,
            front_passenger_heat_cushion_zone4=mode,
        ),
        prereq_fn=lambda vehicle: vehicle.config.front_seats_heating
        == FrontSeatsHeatingAvailability.FRONT_SEATS_HEATING_AVAILABLE,
        enum_type=SeatClimateMode,
    ),
    LucidSelectEntityDescription(
        key="rear_passenger_heat_left",
        key_path=["state", "hvac", "seats"],
        translation_key="rear_left_seat_heater",
        icon="mdi:car-seat-heater",
        options=[*OPTION_TO_MODE_MAP[SeatClimateMode]],
        select_fn=lambda api, vehicle, mode: api.seat_climate_control(
            vehicle, rear_left_seat_heater=mode
        ),
        prereq_fn=lambda vehicle: vehicle.config.second_row_heated_seats
        == SecondRowHeatedSeatsAvailability.SECOND_ROW_HEATED_SEATS_AVAILABLE,
        enum_type=SeatClimateMode,
    ),
    LucidSelectEntityDescription(
        key="rear_passenger_heat_center",
        key_path=["state", "hvac", "seats"],
        translation_key="rear_center_seat_heater",
        icon="mdi:car-seat-heater",
        options=[*OPTION_TO_MODE_MAP[SeatClimateMode]],
        select_fn=lambda api, vehicle, mode: api.seat_climate_control(
            vehicle, rear_center_seat_heater=mode
        ),
        prereq_fn=lambda vehicle: vehicle.config.second_row_heated_seats
        == SecondRowHeatedSeatsAvailability.SECOND_ROW_HEATED_SEATS_AVAILABLE
        and vehicle.config.rear_seat_config != RearSeatConfig.REAR_SEAT_CONFIG_6_SEAT,
        enum_type=SeatClimateMode,
    ),
    LucidSelectEntityDescription(
        key="rear_passenger_heat_right",
        key_path=["state", "hvac", "seats"],
        translation_key="rear_right_seat_heater",
        icon="mdi:car-seat-heater",
        options=[*OPTION_TO_MODE_MAP[SeatClimateMode]],
        select_fn=lambda api, vehicle, mode: api.seat_climate_control(
            vehicle, rear_right_seat_heater=mode
        ),
        prereq_fn=lambda vehicle: vehicle.config.second_row_heated_seats
        == SecondRowHeatedSeatsAvailability.SECOND_ROW_HEATED_SEATS_AVAILABLE,
        enum_type=SeatClimateMode,
    ),
    LucidSelectEntityDescription(
        key="steering_heater_level",
        key_path=["state", "hvac"],
        translation_key="steering_heater",
        icon="mdi:steering",
        options=[*OPTION_TO_MODE_MAP[SteeringWheelHeaterLevel]],
        prereq_fn=lambda vehicle: vehicle.config.heated_steering_wheel
        == HeatedSteeringWheelAvailability.HEATED_STEERING_WHEEL_AVAILABLE,
        select_fn=lambda api, vehicle, level: api.steering_wheel_heater_control(
            vehicle, level
        ),
        enum_type=SteeringWheelHeaterLevel,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Lucid sensors from config entry."""
    coordinator: LucidDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities: list[LucidSelect] = []

    for vehicle in coordinator.api.vehicles:
        entities.extend(
            [
                LucidSelect(coordinator, vehicle, description)
                for description in SELECT_TYPES
                if description.prereq_fn(vehicle)
            ]
        )

    async_add_entities(entities)


class LucidSelect(LucidBaseEntity, SelectEntity):
    """Representation of a Lucid select entity."""

    entity_description: LucidSelectEntityDescription
    _attr_has_entity_name: bool = True

    def __init__(
        self,
        coordinator: LucidDataUpdateCoordinator,
        vehicle: Vehicle,
        description: LucidSelectEntityDescription,
    ) -> None:
        """Initialize Lucid vehicle switch."""
        super().__init__(coordinator, vehicle)
        self.entity_description = description
        self.api = coordinator.api
        self._attr_unique_id = f"{vehicle.config.vin}-{description.key}"

    async def _expect_update(self) -> None:
        await self.coordinator.expect_update(
            self.vehicle.config.vin,
            tuple([*self.entity_description.key_path, self.entity_description.key]),
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _LOGGER.debug(
            "Updating select '%s' of %s",
            self.entity_description.key,
            self.vehicle.config.nickname,
        )
        state = self.vehicle
        for attr in self.entity_description.key_path:
            state = getattr(state, attr)
        value = getattr(state, self.entity_description.key)

        self._attr_current_option = MODE_TO_OPTION_MAP[
            self.entity_description.enum_type
        ].get(value, None)

        # Hopefully temporary workaround for Air always reporting UNKNOWN
        # steering wheel heater level
        if (
            self.entity_description.key == "steering_heater_level"
            and self._attr_current_option is None
        ):
            match self.vehicle.state.hvac.steering_heater:
                case SteeringHeaterStatus.STEERING_HEATER_STATUS_ON:
                    self._attr_current_option = "High"
                case SteeringHeaterStatus.STEERING_HEATER_STATUS_OFF:
                    self._attr_current_option = "Off"

        super()._handle_coordinator_update()

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        await self.entity_description.select_fn(
            self.api,
            self.vehicle,
            OPTION_TO_MODE_MAP[self.entity_description.enum_type][option],
        )
        await self._expect_update()
