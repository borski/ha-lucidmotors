"""Support for reading vehicle status from Lucid API."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import logging
from typing import cast, Optional

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
    TcuDownloadStatus,
    LteType,
    ScheduledChargeState,
    KeepClimateStatus,
    MpbFaultStatus,
    Interior,
    RoofType,
    RearSeatConfig,
    RideHeightStatus,
    AirSuspensionLevelStatus,
    SentryEnablementState,
    EnhancedDeterrenceState,
    SentryThreat,
    SentryUsbDriveStatus,
    SentryRemoteAlarmState,
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
    DEGREE,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfLength,
    UnitOfPower,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util.unit_conversion import DistanceConverter

from . import LucidBaseEntity
from .const import DOMAIN
from .coordinator import LucidDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# The battery module sometimes reports 255 for its percentage signals: the 0xFF
# "signal invalid" sentinel arriving as a literal value instead of being
# filtered out before it reaches us. Nothing above this cutoff is a real
# percentage, so drop it rather than record a spike.
MAX_PLAUSIBLE_PERCENTAGE = 110.0


def _plausible_percentage(value: float, name: str) -> float | None:
    """Return value, or None when it is too large to be a real percentage."""
    if value > MAX_PLAUSIBLE_PERCENTAGE:
        _LOGGER.warning(
            "Ignoring implausible %s of %s%%: above the %s%% cutoff, so the "
            "sensor reports unknown until the next valid reading",
            name,
            value,
            MAX_PLAUSIBLE_PERCENTAGE,
        )
        return None
    return value


@dataclass(frozen=True)
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
        suggested_display_precision=1,
        native_unit_of_measurement=PERCENTAGE,
        value=lambda value, _: _plausible_percentage(value, "battery charge"),
    ),
    LucidSensorEntityDescription(
        key="kwhr",
        key_path=["state", "battery"],
        translation_key="remaining_battery_power",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    LucidSensorEntityDescription(
        key="capacity_kwhr",
        key_path=["state", "battery"],
        translation_key="battery_capacity",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    LucidSensorEntityDescription(
        key="charge_session_kwh",
        key_path=["state", "charging"],
        translation_key="charge_session_power",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-charging",
        suggested_display_precision=2,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    LucidSensorEntityDescription(
        key="charge_session_mi",
        key_path=["state", "charging"],
        translation_key="charge_session_range",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:map-marker-distance",
        suggested_display_precision=1,
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
        suggested_display_precision=1,
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
    # enum_to_str does not know the next three enums, so they are mapped here.
    # *_UNKNOWN is left out of each map and reads as unknown.
    LucidSensorEntityDescription(
        key="interior",
        key_path=["config"],
        translation_key="interior",
        icon="mdi:car-seat",
        device_class=SensorDeviceClass.ENUM,
        options=[
            "Santa Cruz",
            "Tahoe",
            "Mojave",
            "Mojave PurLuxe",
            "Santa Monica",
            "Big Basin",
            "Yosemite",
            "Ojai",
            "Sapphire",
        ],
        value=lambda value, _: {
            Interior.INTERIOR_SANTA_CRUZ: "Santa Cruz",
            Interior.INTERIOR_TAHOE: "Tahoe",
            Interior.INTERIOR_MOJAVE: "Mojave",
            Interior.INTERIOR_MOJAVE_PURLUXE: "Mojave PurLuxe",
            Interior.INTERIOR_SANTA_MONICA: "Santa Monica",
            Interior.INTERIOR_BIG_BASIN: "Big Basin",
            Interior.INTERIOR_YOSEMITE: "Yosemite",
            Interior.INTERIOR_OJAI: "Ojai",
            Interior.INTERIOR_SAPPHIRE: "Sapphire",
        }.get(value),
    ),
    LucidSensorEntityDescription(
        key="roof",
        key_path=["config"],
        translation_key="roof",
        icon="mdi:car-select",
        device_class=SensorDeviceClass.ENUM,
        options=["Glass Canopy", "Metal", "Carbon Fiber"],
        value=lambda value, _: {
            RoofType.ROOF_TYPE_GLASS_CANOPY: "Glass Canopy",
            RoofType.ROOF_TYPE_METAL: "Metal",
            RoofType.ROOF_TYPE_CARBON_FIBER: "Carbon Fiber",
        }.get(value),
    ),
    LucidSensorEntityDescription(
        key="rear_seat_config",
        key_path=["config"],
        translation_key="seating",
        icon="mdi:seat-passenger",
        device_class=SensorDeviceClass.ENUM,
        options=["5 seats", "6 seats", "7 seats"],
        value=lambda value, _: {
            RearSeatConfig.REAR_SEAT_CONFIG_5_SEAT: "5 seats",
            RearSeatConfig.REAR_SEAT_CONFIG_6_SEAT: "6 seats",
            RearSeatConfig.REAR_SEAT_CONFIG_7_SEAT: "7 seats",
        }.get(value),
    ),
    LucidSensorEntityDescription(
        key="power",
        key_path=["state"],
        translation_key="power_state",
        device_class=SensorDeviceClass.ENUM,
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
    LucidSensorEntityDescription(
        key="max_cell_temp",
        key_path=["state", "battery"],
        translation_key="max_cell_temp",
        icon="mdi:thermometer-chevron-up",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
    ),
    LucidSensorEntityDescription(
        key="min_cell_temp",
        key_path=["state", "battery"],
        translation_key="min_cell_temp",
        icon="mdi:thermometer-chevron-down",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
    ),
    LucidSensorEntityDescription(
        key="battery_health_level",
        key_path=["state", "battery"],
        translation_key="battery_health_level",
        icon="mdi:stethoscope",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        value=lambda value, _: _plausible_percentage(value, "battery health level"),
    ),
    LucidSensorEntityDescription(
        key="speed",
        key_path=["state", "chassis"],
        translation_key="speed",
        icon="mdi:speedometer",
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
    ),
    LucidSensorEntityDescription(
        key="tcu_download_status",
        key_path=["state", "software_update"],
        translation_key="update_download_status",
        icon="mdi:cloud-download",
        device_class=SensorDeviceClass.ENUM,
        value=lambda value, _: enum_to_str(TcuDownloadStatus, value),
    ),
    LucidSensorEntityDescription(
        key="heading_precise",
        key_path=["state", "gps"],
        translation_key="gps_heading",
        icon="mdi:compass",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=DEGREE,
        suggested_display_precision=0,
    ),
    LucidSensorEntityDescription(
        key="elevation",
        key_path=["state", "gps"],
        translation_key="gps_elevation",
        icon="mdi:elevation-rise",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfLength.METERS,
        suggested_display_precision=0,
        # Reported in centimetres; see the PR description for how this was
        # established, the protobuf carries no unit.
        value=lambda value, _: round(value / 100, 1),
    ),
    LucidSensorEntityDescription(
        key="latitude",
        key_path=["state", "gps", "location"],
        translation_key="gps_latitude",
        icon="mdi:latitude",
        entity_registry_enabled_default=False,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=DEGREE,
        suggested_display_precision=6,
    ),
    LucidSensorEntityDescription(
        key="longitude",
        key_path=["state", "gps", "location"],
        translation_key="gps_longitude",
        icon="mdi:longitude",
        entity_registry_enabled_default=False,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=DEGREE,
        suggested_display_precision=6,
    ),
    LucidSensorEntityDescription(
        key="lte_type",
        key_path=["state", "tcu_internet"],
        translation_key="lte_type",
        icon="mdi:signal-4g",
        device_class=SensorDeviceClass.ENUM,
        # lucidmotors only defines 3G and 4G; anything else (including
        # LTE_TYPE_UNKNOWN) is reported as unknown rather than invented.
        options=["3G", "4G"],
        value=lambda value, _: {
            LteType.LTE_TYPE_3G: "3G",
            LteType.LTE_TYPE_4G: "4G",
        }.get(value),
    ),
    LucidSensorEntityDescription(
        key="lte_rssi",
        key_path=["state", "tcu_internet"],
        translation_key="lte_signal_strength",
        icon="mdi:signal",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        # The TCU reports 0 when it has no LTE signal at all.
        value=lambda value, _: None if value == 0 else value,
    ),
    LucidSensorEntityDescription(
        key="wifi_rssi",
        key_path=["state", "tcu_internet"],
        translation_key="wifi_signal_strength",
        icon="mdi:wifi",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        # As above: 0 means "not associated", not "0 dBm".
        value=lambda value, _: None if value == 0 else value,
    ),
    LucidSensorEntityDescription(
        key="active_session_ac_current_limit",
        key_path=["state", "charging"],
        translation_key="active_session_ac_current_limit",
        icon="mdi:current-ac",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=0,
        # 0 means there is no active session, not a 0 A limit.
        value=lambda value, _: None if value == 0 else value,
    ),
    LucidSensorEntityDescription(
        key="port_power",
        key_path=["state", "charging"],
        translation_key="charge_port_power",
        icon="mdi:ev-plug-type2",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        suggested_display_precision=1,
        # Sub-milliwatt readings are floating point noise, not charging.
        value=lambda value, _: None if value < 0.001 else value,
    ),
    LucidSensorEntityDescription(
        key="scheduled_charge",
        key_path=["state", "charging"],
        translation_key="scheduled_charge_state",
        icon="mdi:calendar-clock",
        device_class=SensorDeviceClass.ENUM,
        options=["idle", "scheduled", "charging_soon"],
        value=lambda value, _: {
            ScheduledChargeState.SCHEDULED_CHARGE_STATE_IDLE: "idle",
            ScheduledChargeState.SCHEDULED_CHARGE_STATE_SCHEDULED_TO_CHARGE: "scheduled",
            ScheduledChargeState.SCHEDULED_CHARGE_STATE_REQUEST_TO_CHARGE: "charging_soon",
        }.get(value),
    ),
    LucidSensorEntityDescription(
        key="preconditioning_time_remaining",
        key_path=["state", "battery"],
        translation_key="preconditioning_time_remaining",
        icon="mdi:battery-clock",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        # 0xFFFF is the sentinel the pack uses for "not preconditioning".
        value=lambda value, _: None if value in (0, 65535) else value,
    ),
    LucidSensorEntityDescription(
        key="unavailable_range",
        key_path=["state", "battery"],
        translation_key="unavailable_range",
        icon="mdi:map-marker-remove",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        suggested_display_precision=0,
        value=lambda value, _: None if value == 0.0 else value,
    ),
    LucidSensorEntityDescription(
        key="keep_climate_status",
        key_path=["state", "hvac"],
        translation_key="keep_climate_status",
        icon="mdi:paw",
        device_class=SensorDeviceClass.ENUM,
        options=["inactive", "enabled", "canceled", "pet_mode"],
        value=lambda value, _: {
            KeepClimateStatus.KEEP_CLIMATE_STATUS_INACTIVE: "inactive",
            KeepClimateStatus.KEEP_CLIMATE_STATUS_ENABLED: "enabled",
            KeepClimateStatus.KEEP_CLIMATE_STATUS_CANCELED: "canceled",
            KeepClimateStatus.KEEP_CLIMATE_STATUS_PET_MODE_ON: "pet_mode",
        }.get(value),
    ),
    LucidSensorEntityDescription(
        key="mpb_fault_status",
        key_path=["state", "fault_state"],
        translation_key="motor_fault_status",
        icon="mdi:engine",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.ENUM,
        options=[
            "normal",
            "warning",
            "critical",
            "warning_protection",
            "critical_protection",
            "critical_hardware",
        ],
        # MPB is the Motor Power Bridge, i.e. the inverter. HAPS variants are
        # protection-system sub-faults and HW_FLT is a hardware fault.
        # UNKNOWN / RESERVED / INVALID map to None so the entity reads as
        # unknown rather than showing a state nobody can act on.
        value=lambda value, _: {
            MpbFaultStatus.MPB_FAULT_STATUS_NORMAL: "normal",
            MpbFaultStatus.MPB_FAULT_STATUS_WARNING: "warning",
            MpbFaultStatus.MPB_FAULT_STATUS_CRITICAL: "critical",
            MpbFaultStatus.MPB_FAULT_STATUS_WARNING_HAPS: "warning_protection",
            MpbFaultStatus.MPB_FAULT_STATUS_CRITICAL_HAPS: "critical_protection",
            MpbFaultStatus.MPB_FAULT_STATUS_CRITICAL_HW_FLT: "critical_hardware",
        }.get(value),
    ),
    LucidSensorEntityDescription(
        key="ride_height_status",
        key_path=["state", "body"],
        translation_key="ride_height",
        icon="mdi:car-lifted-pickup",
        device_class=SensorDeviceClass.ENUM,
        options=["Lowest", "Low", "Standard", "High", "Highest"],
        # UNKNOWN and UNDEFINED are not heights, so both read as unknown.
        value=lambda value, _: {
            RideHeightStatus.RIDE_HEIGHT_STATUS_LOWEST: "Lowest",
            RideHeightStatus.RIDE_HEIGHT_STATUS_LOW: "Low",
            RideHeightStatus.RIDE_HEIGHT_STATUS_STANDARD: "Standard",
            RideHeightStatus.RIDE_HEIGHT_STATUS_HIGH: "High",
            RideHeightStatus.RIDE_HEIGHT_STATUS_HIGHEST: "Highest",
        }.get(value),
    ),
    LucidSensorEntityDescription(
        key="air_suspension_level_status",
        key_path=["state", "body"],
        translation_key="air_suspension_status",
        icon="mdi:arrow-up-down",
        device_class=SensorDeviceClass.ENUM,
        # Whether the suspension is moving between heights, not the height.
        options=["Idle", "Raising", "Lowering", "Pending"],
        value=lambda value, _: {
            AirSuspensionLevelStatus.AIR_SUSPENSION_LEVEL_STATUS_IDLE: "Idle",
            AirSuspensionLevelStatus.AIR_SUSPENSION_LEVEL_STATUS_RAISING: "Raising",
            AirSuspensionLevelStatus.AIR_SUSPENSION_LEVEL_STATUS_LOWERING: "Lowering",
            AirSuspensionLevelStatus.AIR_SUSPENSION_LEVEL_STATUS_PENDING: "Pending",
        }.get(value),
    ),
    # Sentry mode, from VehicleState.sentry_state. IDLE is the car's own state
    # in each of these and is kept rather than guessed at; every UNKNOWN reads
    # as unknown.
    LucidSensorEntityDescription(
        key="enablement_state",
        key_path=["state", "sentry_state"],
        translation_key="sentry_mode",
        icon="mdi:cctv",
        device_class=SensorDeviceClass.ENUM,
        options=["enabled", "disabled", "idle"],
        value=lambda value, _: {
            SentryEnablementState.SENTRY_STATE_ENABLED: "enabled",
            SentryEnablementState.SENTRY_STATE_DISABLED: "disabled",
            SentryEnablementState.SENTRY_STATE_IDLE: "idle",
        }.get(value),
    ),
    LucidSensorEntityDescription(
        key="enhanced_deterrence_state",
        key_path=["state", "sentry_state"],
        translation_key="enhanced_deterrence",
        icon="mdi:shield-alert-outline",
        device_class=SensorDeviceClass.ENUM,
        options=["enabled", "disabled", "idle"],
        value=lambda value, _: {
            EnhancedDeterrenceState.ENHANCED_DETERRENCE_ENABLED: "enabled",
            EnhancedDeterrenceState.ENHANCED_DETERRENCE_DISABLED: "disabled",
            EnhancedDeterrenceState.ENHANCED_DETERRENCE_IDLE: "idle",
        }.get(value),
    ),
    LucidSensorEntityDescription(
        key="threat_level",
        key_path=["state", "sentry_state"],
        translation_key="sentry_threat_level",
        icon="mdi:shield-alert",
        device_class=SensorDeviceClass.ENUM,
        options=["no_threat", "idle", "level_1", "level_2", "level_3"],
        value=lambda value, _: {
            SentryThreat.SENTRY_THREAT_NO_THREAT: "no_threat",
            SentryThreat.SENTRY_THREAT_IDLE: "idle",
            SentryThreat.SENTRY_THREAT_LEVEL_ONE: "level_1",
            SentryThreat.SENTRY_THREAT_LEVEL_TWO: "level_2",
            SentryThreat.SENTRY_THREAT_LEVEL_THREE: "level_3",
        }.get(value),
    ),
    LucidSensorEntityDescription(
        key="remote_alarm_state",
        key_path=["state", "sentry_state"],
        translation_key="sentry_alarm_status",
        icon="mdi:alarm-light-outline",
        device_class=SensorDeviceClass.ENUM,
        options=["on", "off", "idle"],
        value=lambda value, _: {
            SentryRemoteAlarmState.SENTRY_REMOTE_ALARM_ON: "on",
            SentryRemoteAlarmState.SENTRY_REMOTE_ALARM_OFF: "off",
            SentryRemoteAlarmState.SENTRY_REMOTE_ALARM_IDLE: "idle",
        }.get(value),
    ),
    LucidSensorEntityDescription(
        key="usb_drive_status",
        key_path=["state", "sentry_state"],
        translation_key="sentry_usb_drive",
        icon="mdi:usb-flash-drive",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.ENUM,
        options=["connected", "not_connected", "idle"],
        value=lambda value, _: {
            SentryUsbDriveStatus.SENTRY_USB_DRIVE_CONNECTED: "connected",
            SentryUsbDriveStatus.SENTRY_USB_DRIVE_NOT_CONNECTED: "not_connected",
            SentryUsbDriveStatus.SENTRY_USB_DRIVE_IDLE: "idle",
        }.get(value),
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

        entities.extend(
            [
                LucidEfficiencySensor(
                    coordinator,
                    vehicle,
                    LucidSensorEntityDescription(
                        key="efficiency",
                        key_path=[],  # Unused
                        translation_key="efficiency",
                        icon="mdi:lightning-bolt",
                        native_unit_of_measurement="Wh/mi",
                    ),
                ),
                LucidPowerSensor(
                    coordinator,
                    vehicle,
                    LucidSensorEntityDescription(
                        key="power_usage",
                        key_path=[],  # Unused
                        translation_key="power_usage",
                        icon="mdi:meter-electric",
                        device_class=SensorDeviceClass.POWER,
                        state_class=SensorStateClass.MEASUREMENT,
                        native_unit_of_measurement=UnitOfPower.KILO_WATT,
                    ),
                ),
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


class LucidEfficiencySensor(LucidSensor):
    """Driving efficiency sensor derived from Lucid API data."""

    _saved_odometer: Optional[float] = None
    _saved_charge: Optional[float] = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _LOGGER.debug(
            "Updating sensor '%s' of %s",
            self.entity_description.key,
            self.vehicle.config.nickname,
        )

        current_odometer = self.vehicle.state.chassis.odometer_km
        current_charge = self.vehicle.state.battery.kwhr

        if self._saved_odometer is None:
            self._saved_odometer = current_odometer
        if self._saved_charge is None:
            self._saved_charge = current_charge

        odo_diff = current_odometer - self._saved_odometer
        charge_diff = current_charge - self._saved_charge

        # Convert kWh to Wh
        charge_diff *= 1000.0

        # What we're looking for is the rate of discharge, which is negative
        # charge_diff.
        charge_diff = -charge_diff

        # HA doesn't have automatic unit conversion for Wh/mi type units, so
        # we'll have to convert manually here to miles so us non-metric folks
        # can survive.
        odo_diff = DistanceConverter.convert(
            odo_diff,
            UnitOfLength.KILOMETERS,
            UnitOfLength.MILES,
        )

        if odo_diff == 0.0:
            # We haven't moved. Also avoid zero division.
            value = None
        else:
            # Wh / mi
            value = charge_diff / odo_diff

        # Update saved
        self._saved_odometer = current_odometer
        self._saved_charge = current_charge

        self._attr_native_value = cast(StateType, value)
        super(LucidSensor, self)._handle_coordinator_update()


class LucidPowerSensor(LucidSensor):
    """Momentary power usage sensor derived from Lucid API data."""

    _saved_last_updated_ms: Optional[int] = None
    _saved_charge: Optional[float] = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _LOGGER.debug(
            "Updating sensor '%s' of %s",
            self.entity_description.key,
            self.vehicle.config.nickname,
        )

        current_update_ms = self.vehicle.state.last_updated_ms
        current_charge = self.vehicle.state.battery.kwhr

        if self._saved_last_updated_ms is None:
            self._saved_last_updated_ms = current_update_ms
        if self._saved_charge is None:
            self._saved_charge = current_charge

        time_diff_ms = current_update_ms - self._saved_last_updated_ms
        charge_diff = current_charge - self._saved_charge

        # What we're looking for is the rate of discharge, which is negative
        # charge_diff.
        charge_diff = -charge_diff

        time_diff_h = time_diff_ms / 3600000.0

        if time_diff_h == 0:
            # Sensors have not been updated. Also avoid zero division.
            value = None
        else:
            # kWh / h, aka kW
            value = charge_diff / time_diff_h

        # Update saved
        self._saved_last_updated_ms = current_update_ms
        self._saved_charge = current_charge

        self._attr_native_value = cast(StateType, value)
        super(LucidSensor, self)._handle_coordinator_update()
