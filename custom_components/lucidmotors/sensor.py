"""Support for reading vehicle status from Lucid API."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
import logging
from typing import Any, cast, Optional

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
    ChargeState,
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
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util
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


# The battery module also sends sentinels in place of its energy reading: 255
# (0xFF) and 65.535 (0xFFFF scaled by 1000). Neither is a real reading, and
# either would look like a charge or a big drop to anything that trusts it.
KWHR_SENTINEL_MIN = 255.0
KWHR_SENTINEL_FFFF = 65.535
KWHR_SENTINEL_TOLERANCE = 0.01


def _plausible_kwhr(value: float, name: str) -> float | None:
    """Return value, or None when it is a sentinel rather than a reading."""
    if (
        value <= 0
        or value >= KWHR_SENTINEL_MIN
        or abs(value - KWHR_SENTINEL_FFFF) < KWHR_SENTINEL_TOLERANCE
    ):
        _LOGGER.warning(
            "Ignoring implausible %s of %s kWh: a sentinel, not a reading, so "
            "this update is skipped",
            name,
            value,
        )
        return None
    return value


# Below this distance since the last charge, efficiency is mostly noise: a few
# hundred metres and a fraction of a kWh can come out at any number at all.
MIN_DISTANCE_SINCE_CHARGE_MI = 1.0

# A battery that holds more energy than it did at the baseline, by more than
# regen alone plausibly adds, was charged while we weren't looking (Home
# Assistant was down, or the API never showed the cable). Start over from here,
# but only once two readings in a row agree, so one bad value can't do it.
MISSED_CHARGE_KWH = 2.0


def _efficiency_since_charge(
    baseline_odometer_km: float,
    baseline_kwhr: float,
    odometer_km: float,
    kwhr: float,
) -> tuple[float | None, float, float]:
    """Return (mi/kWh, miles driven, kWh used) since the baseline.

    Efficiency is None until MIN_DISTANCE_SINCE_CHARGE_MI has been driven, and
    while no net energy has been used (a long descent can do that).
    """
    distance_mi = DistanceConverter.convert(
        odometer_km - baseline_odometer_km,
        UnitOfLength.KILOMETERS,
        UnitOfLength.MILES,
    )
    energy_kwh = baseline_kwhr - kwhr
    return _miles_per_kwh(distance_mi, energy_kwh), distance_mi, energy_kwh


def _miles_per_kwh(distance_mi: float, energy_kwh: float) -> float | None:
    """Return mi/kWh, or None below the minimum distance or with no energy used."""
    if distance_mi < MIN_DISTANCE_SINCE_CHARGE_MI or energy_kwh <= 0:
        return None
    return distance_mi / energy_kwh


def _driving_kwh(
    last_odometer_km: float | None,
    last_kwhr: float | None,
    odometer_km: float,
    kwhr: float,
) -> float:
    """Return the kWh used between two polls if the car moved, else 0.

    Polls where the odometer stood still are parked: climate, Sentry, standby.
    """
    if last_odometer_km is None or last_kwhr is None:
        return 0.0
    if odometer_km <= last_odometer_km:
        return 0.0
    return last_kwhr - kwhr


def _cable_connected(charge_state: int) -> bool | None:
    """Return whether a charging cable is connected, or None if unknown."""
    if charge_state == ChargeState.CHARGE_STATE_UNKNOWN:
        return None
    return charge_state != ChargeState.CHARGE_STATE_NOT_CONNECTED


# Power usage and Efficiency are a change in battery energy divided by a change
# in time or distance. Between two consecutive polls that change can be a few
# seconds or a few metres while the energy moves by a whole step, and the
# result is enormous. So each waits for at least this much before computing a
# new value, and keeps its previous value until then.
MIN_POWER_INTERVAL_S = 60.0
MIN_EFFICIENCY_DISTANCE_MI = 1.0
# The energy reading moves in steps, so one mile alone still read anywhere from
# 57 to 636 Wh/mi on a real drive. Efficiency therefore updates every mile but
# reports the last few miles together, weighted by distance: a hard
# acceleration still moves it, by its share of the window.
EFFICIENCY_WINDOW_MI = 3.0


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
                LucidEfficiencySinceChargeSensor(
                    coordinator,
                    vehicle,
                    LucidSensorEntityDescription(
                        key="efficiency_since_charge",
                        key_path=[],  # Unused
                        translation_key="efficiency_since_charge",
                        icon="mdi:lightning-bolt-outline",
                        state_class=SensorStateClass.MEASUREMENT,
                        suggested_display_precision=2,
                        # HA only gained an energy-distance device class (and
                        # unit conversion for it) in 2025.3. This string is the
                        # same unit that class uses, so adding the device class
                        # later keeps the history.
                        native_unit_of_measurement="mi/kWh",
                    ),
                ),
                LucidDrivingEfficiencySinceChargeSensor(
                    coordinator,
                    vehicle,
                    LucidSensorEntityDescription(
                        key="driving_efficiency_since_charge",
                        key_path=[],  # Unused
                        translation_key="driving_efficiency_since_charge",
                        icon="mdi:car-electric-outline",
                        state_class=SensorStateClass.MEASUREMENT,
                        suggested_display_precision=2,
                        native_unit_of_measurement="mi/kWh",
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
    """Driving efficiency sensor derived from Lucid API data.

    Battery energy used over distance driven, in Wh/mi. A new value comes every
    MIN_EFFICIENCY_DISTANCE_MI and covers the last EFFICIENCY_WINDOW_MI driven
    (fewer at the start). Only polls where the odometer moved count, so
    energy used or added while parked (climate, Sentry, charging) is not put
    on the next drive.
    """

    def __init__(
        self,
        coordinator: LucidDataUpdateCoordinator,
        vehicle: Vehicle,
        description: LucidSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, vehicle, description)
        self._last_odometer_km: float | None = None
        self._last_kwhr: float | None = None
        self._distance_km = 0.0
        self._energy_kwh = 0.0
        # Completed stretches of at least MIN_EFFICIENCY_DISTANCE_MI, oldest
        # first, as (miles, kWh).
        self._stretches: deque[tuple[float, float]] = deque()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _LOGGER.debug(
            "Updating sensor '%s' of %s",
            self.entity_description.key,
            self.vehicle.config.nickname,
        )

        odometer_km = self.vehicle.state.chassis.odometer_km
        kwhr = _plausible_kwhr(self.vehicle.state.battery.kwhr, "battery energy")
        # An unset odometer reads 0; skip it like a sentinel.
        if kwhr is not None and odometer_km > 0:
            self._add_sample(odometer_km, kwhr)

        super(LucidSensor, self)._handle_coordinator_update()

    def _add_sample(self, odometer_km: float, kwhr: float) -> None:
        last_odometer_km = self._last_odometer_km
        last_kwhr = self._last_kwhr
        self._last_odometer_km = odometer_km
        self._last_kwhr = kwhr

        if last_odometer_km is None or last_kwhr is None:
            return
        if odometer_km < last_odometer_km:
            # Not a drive. Start the distance over.
            self._distance_km = 0.0
            self._energy_kwh = 0.0
            self._stretches.clear()
            return
        if odometer_km == last_odometer_km:
            # Parked, or the same snapshot again.
            return

        self._distance_km += odometer_km - last_odometer_km
        self._energy_kwh += last_kwhr - kwhr

        # HA doesn't have automatic unit conversion for Wh/mi type units, so
        # we'll have to convert manually here to miles so us non-metric folks
        # can survive.
        distance_mi = DistanceConverter.convert(
            self._distance_km,
            UnitOfLength.KILOMETERS,
            UnitOfLength.MILES,
        )
        if distance_mi < MIN_EFFICIENCY_DISTANCE_MI:
            return

        self._stretches.append((distance_mi, self._energy_kwh))
        self._distance_km = 0.0
        self._energy_kwh = 0.0
        # Drop the oldest stretch while the rest still cover the window.
        total_mi = sum(miles for miles, _ in self._stretches)
        while total_mi - self._stretches[0][0] >= EFFICIENCY_WINDOW_MI:
            total_mi -= self._stretches.popleft()[0]
        total_kwh = sum(kwh for _, kwh in self._stretches)

        # Energy used (kWh to Wh) per mile. Negative when regeneration put
        # back more than was used, which a long descent can do.
        self._attr_native_value = total_kwh * 1000.0 / total_mi


class LucidPowerSensor(LucidSensor):
    """Power usage sensor derived from Lucid API data.

    The rate the battery's energy is falling, in kW, averaged over at least
    MIN_POWER_INTERVAL_S of the car's own timestamps. Negative while the
    battery is gaining energy (charging).
    """

    def __init__(
        self,
        coordinator: LucidDataUpdateCoordinator,
        vehicle: Vehicle,
        description: LucidSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, vehicle, description)
        self._saved_last_updated_ms: int | None = None
        self._saved_kwhr: float | None = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _LOGGER.debug(
            "Updating sensor '%s' of %s",
            self.entity_description.key,
            self.vehicle.config.nickname,
        )

        last_updated_ms = self.vehicle.state.last_updated_ms
        kwhr = _plausible_kwhr(self.vehicle.state.battery.kwhr, "battery energy")
        if kwhr is not None and last_updated_ms > 0:
            self._add_sample(last_updated_ms, kwhr)

        super(LucidSensor, self)._handle_coordinator_update()

    def _add_sample(self, last_updated_ms: int, kwhr: float) -> None:
        if (
            self._saved_last_updated_ms is None
            or self._saved_kwhr is None
            or last_updated_ms < self._saved_last_updated_ms
        ):
            self._saved_last_updated_ms = last_updated_ms
            self._saved_kwhr = kwhr
            return

        elapsed_h = (last_updated_ms - self._saved_last_updated_ms) / 3600000.0
        if elapsed_h * 3600.0 < MIN_POWER_INTERVAL_S:
            # Includes the same snapshot seen again.
            return

        # kWh / h, aka kW. The rate of discharge, so energy falling is positive.
        self._attr_native_value = (self._saved_kwhr - kwhr) / elapsed_h
        self._saved_last_updated_ms = last_updated_ms
        self._saved_kwhr = kwhr


@dataclass
class LucidChargeBaseline(ExtraStoredData):
    """What the efficiency-since-charge sensors keep across restarts."""

    odometer_km: float | None = None
    kwhr: float | None = None
    time: datetime | None = None
    cable_connected: bool | None = None
    native_value: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a dict representation of the baseline."""
        data = asdict(self)
        data["time"] = self.time.isoformat() if self.time is not None else None
        return data

    @classmethod
    def from_dict(cls, restored: dict[str, Any]) -> LucidChargeBaseline | None:
        """Initialize a stored baseline from a dict, or None if unusable."""
        try:
            # Without these two there is no baseline to restore.
            restored["odometer_km"]
            restored["kwhr"]
            values: dict[str, Any] = {}
            for fld in fields(cls):
                value = restored.get(fld.name, fld.default)
                if value is None or fld.name == "cable_connected":
                    values[fld.name] = value
                elif fld.name == "time":
                    values[fld.name] = dt_util.parse_datetime(value)
                else:
                    values[fld.name] = float(value)
            return cls(**values)
        except (KeyError, TypeError, ValueError):
            return None


@dataclass
class LucidDrivingBaseline(LucidChargeBaseline):
    """The baseline plus the energy used while moving, and the last poll."""

    driving_kwh: float = 0.0
    last_odometer_km: float | None = None
    last_kwhr: float | None = None


class LucidSinceChargeSensor(LucidSensor, RestoreEntity):
    """Base for efficiency sensors measured from the last unplug.

    The baseline is the odometer and battery energy at the moment the cable
    comes out. It is kept across restarts.
    """

    _baseline_type: type[LucidChargeBaseline] = LucidChargeBaseline
    _baseline: LucidChargeBaseline

    def __init__(
        self,
        coordinator: LucidDataUpdateCoordinator,
        vehicle: Vehicle,
        description: LucidSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, vehicle, description)
        self._baseline = self._baseline_type()
        self._distance_mi: float | None = None
        self._rise_pending = False

    async def async_added_to_hass(self) -> None:
        """Restore the baseline before the first coordinator update."""
        if (extra := await self.async_get_last_extra_data()) is not None:
            restored = self._baseline_type.from_dict(extra.as_dict())
            if restored is not None:
                self._baseline = restored
                self._attr_native_value = restored.native_value
        await super().async_added_to_hass()

    @property
    def extra_restore_state_data(self) -> LucidChargeBaseline:
        """Return the baseline to be stored across restarts."""
        return self._baseline

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return how far since the baseline, and when it was taken."""
        return {
            "distance_since_charge_mi": _round(self._distance_mi, 1),
            "baseline_time": self._baseline.time,
        }

    def _take_baseline(self, odometer_km: float, kwhr: float, reason: str) -> None:
        _LOGGER.debug(
            "New '%s' baseline for %s (%s): %.1f km, %.2f kWh",
            self.entity_description.key,
            self.vehicle.config.nickname,
            reason,
            odometer_km,
            kwhr,
        )
        self._baseline.odometer_km = odometer_km
        self._baseline.kwhr = kwhr
        self._baseline.time = dt_util.utcnow()

    def _update_baseline(
        self, odometer_km: float, kwhr: float, connected: bool | None
    ) -> bool:
        """Take a new baseline if needed. Return False to skip this poll."""
        baseline = self._baseline
        # An unplug after a charge rises too, and is handled below.
        rise = (
            baseline.kwhr is not None
            and not connected
            and not baseline.cable_connected
            and kwhr > baseline.kwhr + MISSED_CHARGE_KWH
        )
        if rise and not self._rise_pending:
            # Wait for the next reading before trusting it.
            self._rise_pending = True
            return False
        self._rise_pending = False

        if baseline.odometer_km is None or baseline.kwhr is None:
            self._take_baseline(odometer_km, kwhr, "first update")
        elif baseline.cable_connected and connected is False:
            self._take_baseline(odometer_km, kwhr, "cable unplugged")
        elif odometer_km < baseline.odometer_km:
            self._take_baseline(odometer_km, kwhr, "odometer went backwards")
        elif rise:
            self._take_baseline(odometer_km, kwhr, "missed a charge")
        return True

    def _sample(self, odometer_km: float, kwhr: float) -> None:
        """Record a poll, plugged in or not. Nothing to do by default."""

    def _value(self, odometer_km: float, kwhr: float) -> float | None:
        """Return the efficiency for this poll, while unplugged."""
        raise NotImplementedError

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _LOGGER.debug(
            "Updating sensor '%s' of %s",
            self.entity_description.key,
            self.vehicle.config.nickname,
        )

        state = self.vehicle.state
        odometer_km = state.chassis.odometer_km
        kwhr = _plausible_kwhr(state.battery.kwhr, "battery energy")
        connected = _cable_connected(state.charging.charge_state)
        baseline = self._baseline
        if connected is None:
            # Unknown charge state: carry on as if nothing changed.
            connected = baseline.cable_connected

        # An unset protobuf field reads as zero; no car is at 0 km.
        if (
            odometer_km > 0
            and kwhr is not None
            and self._update_baseline(odometer_km, kwhr, connected)
        ):
            baseline.cable_connected = connected
            self._sample(odometer_km, kwhr)

            # While plugged in the battery is filling, so the figure would go
            # negative. Hold the last value until the cable comes out.
            if not connected:
                value = self._value(odometer_km, kwhr)
                self._attr_native_value = value
                baseline.native_value = value

        super(LucidSensor, self)._handle_coordinator_update()


def _round(value: float | None, digits: int) -> float | None:
    return round(value, digits) if value is not None else None


class LucidEfficiencySinceChargeSensor(LucidSinceChargeSensor):
    """Average efficiency since the charging cable was last unplugged.

    Counts everything the car has used since, parked or driving.
    """

    def __init__(
        self,
        coordinator: LucidDataUpdateCoordinator,
        vehicle: Vehicle,
        description: LucidSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, vehicle, description)
        self._energy_kwh: float | None = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return how far and how much energy since the baseline."""
        return {
            **super().extra_state_attributes,
            "energy_used_since_charge_kwh": _round(self._energy_kwh, 2),
        }

    def _value(self, odometer_km: float, kwhr: float) -> float | None:
        value, self._distance_mi, self._energy_kwh = _efficiency_since_charge(
            cast(float, self._baseline.odometer_km),
            cast(float, self._baseline.kwhr),
            odometer_km,
            kwhr,
        )
        return value


class LucidDrivingEfficiencySinceChargeSensor(LucidSinceChargeSensor):
    """Efficiency since the last unplug, counting only energy used while moving.

    Energy is summed over the polls in which the odometer advanced, so climate,
    Sentry and standby while parked are left out. This is the figure to compare
    with the car's own.
    """

    _baseline_type = LucidDrivingBaseline
    _baseline: LucidDrivingBaseline

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return how far and how much driving energy since the baseline."""
        return {
            **super().extra_state_attributes,
            "driving_energy_since_charge_kwh": _round(self._baseline.driving_kwh, 2),
        }

    def _take_baseline(self, odometer_km: float, kwhr: float, reason: str) -> None:
        super()._take_baseline(odometer_km, kwhr, reason)
        self._baseline.driving_kwh = 0.0

    def _sample(self, odometer_km: float, kwhr: float) -> None:
        baseline = self._baseline
        baseline.driving_kwh += _driving_kwh(
            baseline.last_odometer_km, baseline.last_kwhr, odometer_km, kwhr
        )
        baseline.last_odometer_km = odometer_km
        baseline.last_kwhr = kwhr

    def _value(self, odometer_km: float, kwhr: float) -> float | None:
        self._distance_mi = DistanceConverter.convert(
            odometer_km - cast(float, self._baseline.odometer_km),
            UnitOfLength.KILOMETERS,
            UnitOfLength.MILES,
        )
        return _miles_per_kwh(self._distance_mi, self._baseline.driving_kwh)
