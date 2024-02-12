"""Support for reading vehicle status from Lucid API."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import logging
from typing import cast

from lucidmotors import (
    Vehicle,
    AlarmMode,
    AlarmStatus,
    PaintColor,
    Look,
    Wheels,
    PowerState,
    EnergyType,
    DriveMode,
    GearPosition,
    enum_to_str,
)
from lucidmotors.const import TIRE_PRESSURE_MAX, CHARGE_SESSION_TIME_MAX

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfLength,
    UnitOfPower,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import LucidBaseEntity
from .const import DOMAIN
from .coordinator import LucidDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass
class LucidSensorEntityDescription(SensorEntityDescription):
    """Describes Lucid sensor entity."""

    key_path: list[str] = field(default_factory=list)
    value: Callable = lambda x, y: x


SENSOR_TYPES: list[LucidSensorEntityDescription] = [
    LucidSensorEntityDescription(
        key="charge_percent",
        key_path=["state", "battery"],
        translation_key="remaining_battery_percent",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        native_unit_of_measurement=PERCENTAGE,
    ),
    LucidSensorEntityDescription(
        key="kwhr",
        key_path=["state", "battery"],
        translation_key="remaining_battery_power",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    LucidSensorEntityDescription(
        key="capacity_kwhr",
        key_path=["state", "battery"],
        translation_key="battery_capacity",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    LucidSensorEntityDescription(
        key="charge_session_kwh",
        key_path=["state", "charging"],
        translation_key="charge_session_power",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-charging",
        suggested_display_precision=0,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    LucidSensorEntityDescription(
        key="charge_session_mi",
        key_path=["state", "charging"],
        translation_key="charge_session_range",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:map-marker-distance",
        suggested_display_precision=0,
        native_unit_of_measurement=UnitOfLength.MILES,
    ),
    LucidSensorEntityDescription(
        key="charge_rate_kwh_precise",
        key_path=["state", "charging"],
        translation_key="charging_rate",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
    ),
    LucidSensorEntityDescription(
        key="charge_rate_mph_precise",
        key_path=["state", "charging"],
        translation_key="charging_rate_distance",
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        native_unit_of_measurement=UnitOfSpeed.MILES_PER_HOUR,
    ),
    LucidSensorEntityDescription(
        key="session_minutes_remaining",
        key_path=["state", "charging"],
        translation_key="charge_session_time_remaining",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value=lambda value, _: None if value == CHARGE_SESSION_TIME_MAX else value,
    ),
    LucidSensorEntityDescription(
        key="remaining_range",
        key_path=["state", "battery"],
        translation_key="remaining_range",
        icon="mdi:map-marker-distance",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
    ),
    LucidSensorEntityDescription(
        key="odometer_km",
        key_path=["state", "chassis"],
        translation_key="mileage",
        icon="mdi:counter",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
    ),
    LucidSensorEntityDescription(
        key="exterior_temp",
        key_path=["state", "cabin"],
        translation_key="exterior_temp",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
    ),
    LucidSensorEntityDescription(
        key="interior_temp",
        key_path=["state", "cabin"],
        translation_key="interior_temp",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
    ),
    LucidSensorEntityDescription(
        key="front_left_tire_pressure_bar",
        key_path=["state", "chassis"],
        translation_key="front_left_tire_pressure",
        icon="mdi:tire",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPressure.BAR,
        suggested_display_precision=1,
        value=lambda value, _: None if value == TIRE_PRESSURE_MAX else value,
    ),
    LucidSensorEntityDescription(
        key="front_right_tire_pressure_bar",
        key_path=["state", "chassis"],
        translation_key="front_right_tire_pressure",
        icon="mdi:tire",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPressure.BAR,
        suggested_display_precision=1,
        value=lambda value, _: None if value == TIRE_PRESSURE_MAX else value,
    ),
    LucidSensorEntityDescription(
        key="rear_left_tire_pressure_bar",
        key_path=["state", "chassis"],
        translation_key="rear_left_tire_pressure",
        icon="mdi:tire",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPressure.BAR,
        suggested_display_precision=1,
        value=lambda value, _: None if value == TIRE_PRESSURE_MAX else value,
    ),
    LucidSensorEntityDescription(
        key="rear_right_tire_pressure_bar",
        key_path=["state", "chassis"],
        translation_key="rear_right_tire_pressure",
        icon="mdi:tire",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPressure.BAR,
        suggested_display_precision=1,
        value=lambda value, _: None if value == TIRE_PRESSURE_MAX else value,
    ),
    LucidSensorEntityDescription(
        key="mode",
        key_path=["state", "alarm"],
        translation_key="alarm_mode",
        icon="mdi:shield-lock",
        value=lambda value, _: enum_to_str(AlarmMode, value),
    ),
    LucidSensorEntityDescription(
        key="status",
        key_path=["state", "alarm"],
        translation_key="alarm_status",
        icon="mdi:shield-lock",
        value=lambda value, _: enum_to_str(AlarmStatus, value),
    ),
    LucidSensorEntityDescription(
        key="paint_color",
        key_path=["config"],
        translation_key="paint_color",
        icon="mdi:palette",
        value=lambda value, _: enum_to_str(PaintColor, value),
    ),
    LucidSensorEntityDescription(
        key="look",
        key_path=["config"],
        translation_key="look",
        icon="mdi:car-outline",
        value=lambda value, _: enum_to_str(Look, value),
    ),
    LucidSensorEntityDescription(
        key="wheels",
        key_path=["config"],
        translation_key="wheels",
        icon="mdi:tire",
        value=lambda value, _: enum_to_str(Wheels, value),
    ),
    LucidSensorEntityDescription(
        key="power",
        key_path=["state"],
        translation_key="power_state",
        icon="mdi:power-settings",
        value=lambda value, _: enum_to_str(PowerState, value),
    ),
    LucidSensorEntityDescription(
        key="energy_type",
        key_path=["state", "charging"],
        translation_key="energy_type",
        icon="mdi:current-ac",
        value=lambda value, _: enum_to_str(EnergyType, value),
    ),
    LucidSensorEntityDescription(
        key="drive_mode",
        key_path=["state"],
        translation_key="drive_mode",
        icon="mdi:car-settings",
        value=lambda value, _: enum_to_str(DriveMode, value),
    ),
    LucidSensorEntityDescription(
        key="gear_position",
        key_path=["state"],
        translation_key="gear_position",
        icon="mdi:car-shift-pattern",
        value=lambda value, _: enum_to_str(GearPosition, value),
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Lucid sensors from config entry."""
    coordinator: LucidDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities: list[LucidSensor] = []

    for vehicle in coordinator.api.vehicles:
        entities.extend(
            [
                LucidSensor(coordinator, vehicle, description)
                for description in SENSOR_TYPES
            ]
        )

    async_add_entities(entities)


class LucidSensor(LucidBaseEntity, SensorEntity):
    """Representation of a Lucid vehicle sensor."""

    entity_description: LucidSensorEntityDescription
    _attr_has_entity_name: bool = True

    def __init__(
        self,
        coordinator: LucidDataUpdateCoordinator,
        vehicle: Vehicle,
        description: LucidSensorEntityDescription,
    ) -> None:
        """Initialize Lucid vehicle sensor."""
        super().__init__(coordinator, vehicle)
        self.entity_description = description
        self._attr_unique_id = f"{vehicle.config.vin}-{description.key}"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _LOGGER.debug(
            "Updating sensor '%s' of %s",
            self.entity_description.key,
            self.vehicle.config.nickname,
        )
        state = self.vehicle
        for attr in self.entity_description.key_path:
            state = getattr(state, attr)
        state = getattr(state, self.entity_description.key)
        self._attr_native_value = cast(
            StateType, self.entity_description.value(state, self.hass)
        )
        super()._handle_coordinator_update()

    @property
    def translation_key(self) -> str | None:
        """Return the translation key to translate the entity's states."""
        return self.entity_description.translation_key
