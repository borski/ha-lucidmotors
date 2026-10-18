"""Support for reading vehicle status from Lucid API."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import logging

from lucidmotors import (
    Vehicle,
    WalkawayState,
    DoorState,
    HvacPower,
    ChargeState,
    InternetStatus,
    KeyfobBatteryStatus,
    LockState,
    DefrostState,
    WarningState,
    TirePressureSensorDefective,
)

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LucidBaseEntity
from .const import DOMAIN
from .coordinator import LucidDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LucidBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes Lucid sensor entity."""

    key_path: list[str] = field(default_factory=list)
    is_on_fn: Callable = lambda x, y: x


# The car reports UNKNOWN when it has no answer; leave the entity unknown rather
# than reporting that as "no warning".
TIRE_WARNING = {
    WarningState.WARNING_OFF: False,
    WarningState.WARNING_ON: True,
}
TIRE_SENSOR_DEFECTIVE = {
    TirePressureSensorDefective.TIRE_PRESSURE_SENSOR_DEFECTIVE_OFF: False,
    TirePressureSensorDefective.TIRE_PRESSURE_SENSOR_DEFECTIVE_ON: True,
}


SENSOR_TYPES: dict[str, LucidBinarySensorEntityDescription] = {
    "front_left_door": LucidBinarySensorEntityDescription(
        key="front_left_door",
        key_path=["state", "body"],
        translation_key="front_left_door",
        icon="mdi:door",
        device_class=BinarySensorDeviceClass.DOOR,
        is_on_fn=lambda vehicle: vehicle.state.body.front_left_door
        != DoorState.DOOR_STATE_CLOSED,
    ),
    "front_right_door": LucidBinarySensorEntityDescription(
        key="front_right_door",
        key_path=["state", "body"],
        translation_key="front_right_door",
        icon="mdi:door",
        device_class=BinarySensorDeviceClass.DOOR,
        is_on_fn=lambda vehicle: vehicle.state.body.front_right_door
        != DoorState.DOOR_STATE_CLOSED,
    ),
    "rear_left_door": LucidBinarySensorEntityDescription(
        key="rear_left_door",
        key_path=["state", "body"],
        translation_key="rear_left_door",
        icon="mdi:door",
        device_class=BinarySensorDeviceClass.DOOR,
        is_on_fn=lambda vehicle: vehicle.state.body.rear_left_door
        != DoorState.DOOR_STATE_CLOSED,
    ),
    "rear_right_door": LucidBinarySensorEntityDescription(
        key="rear_right_door",
        key_path=["state", "body"],
        translation_key="rear_right_door",
        icon="mdi:door",
        device_class=BinarySensorDeviceClass.DOOR,
        is_on_fn=lambda vehicle: vehicle.state.body.rear_right_door
        != DoorState.DOOR_STATE_CLOSED,
    ),
    "frunk": LucidBinarySensorEntityDescription(
        key="front_cargo",
        key_path=["state", "body"],
        translation_key="front_cargo",
        icon="mdi:door",
        device_class=BinarySensorDeviceClass.DOOR,
        is_on_fn=lambda vehicle: vehicle.state.body.front_cargo
        != DoorState.DOOR_STATE_CLOSED,
    ),
    "trunk": LucidBinarySensorEntityDescription(
        key="rear_cargo",
        key_path=["state", "body"],
        translation_key="rear_cargo",
        icon="mdi:door",
        device_class=BinarySensorDeviceClass.DOOR,
        is_on_fn=lambda vehicle: vehicle.state.body.rear_cargo
        != DoorState.DOOR_STATE_CLOSED,
    ),
    "charge_port_door": LucidBinarySensorEntityDescription(
        key="charge_port",
        key_path=["state", "body"],
        translation_key="charge_port_door",
        icon="mdi:door",
        device_class=BinarySensorDeviceClass.DOOR,
        is_on_fn=lambda vehicle: vehicle.state.body.charge_port
        != DoorState.DOOR_STATE_CLOSED,
    ),
    "walkaway_lock": LucidBinarySensorEntityDescription(
        key="walkaway_lock",
        key_path=["state", "body"],
        translation_key="walkaway_lock",
        icon="mdi:upload-lock",
        is_on_fn=lambda vehicle: vehicle.state.body.walkaway_lock
        == WalkawayState.WALKAWAY_ACTIVE,
    ),
    "hvac_power": LucidBinarySensorEntityDescription(
        key="power",
        key_path=["state", "hvac"],
        translation_key="hvac_power",
        icon="mdi:hvac",
        is_on_fn=lambda vehicle: vehicle.state.hvac.power != HvacPower.HVAC_OFF,
    ),
    "charging_cable": LucidBinarySensorEntityDescription(
        key="charge_state",
        key_path=["state", "charging"],
        translation_key="charging_cable",
        icon="mdi:ev-plug-tesla",
        device_class=BinarySensorDeviceClass.PLUG,
        is_on_fn=lambda vehicle: vehicle.state.charging.charge_state
        != ChargeState.Value('CHARGE_STATE_NOT_CONNECTED'),
    ),
    "keyfob_battery_low": LucidBinarySensorEntityDescription(
        key="keyfob_battery_status",
        key_path=["state", "body"],
        translation_key="keyfob_battery_low",
        icon="mdi:key-wireless",
        device_class=BinarySensorDeviceClass.BATTERY,
        is_on_fn=lambda vehicle: vehicle.state.body.keyfob_battery_status
        == KeyfobBatteryStatus.KEYFOB_BATTERY_STATUS_LOW,
    ),
    "lte_connected": LucidBinarySensorEntityDescription(
        key="lte_status",
        key_path=["state", "tcu_internet"],
        translation_key="lte_connected",
        icon="mdi:signal",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        is_on_fn=lambda vehicle: vehicle.state.tcu_internet.lte_status
        == InternetStatus.INTERNET_CONNECTED,
    ),
    "wifi_connected": LucidBinarySensorEntityDescription(
        key="wifi_status",
        key_path=["state", "tcu_internet"],
        translation_key="wifi_connected",
        icon="mdi:wifi",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        is_on_fn=lambda vehicle: vehicle.state.tcu_internet.wifi_status
        == InternetStatus.INTERNET_CONNECTED,
    ),
    "cable_lock": LucidBinarySensorEntityDescription(
        key="cable_lock",
        key_path=["state", "charging"],
        translation_key="charge_cable_lock",
        icon="mdi:lock-outline",
        device_class=BinarySensorDeviceClass.LOCK,
        # BinarySensorDeviceClass.LOCK is on == unlocked. LOCK_STATE_UNKNOWN
        # is reported as unknown rather than being lumped in with unlocked.
        is_on_fn=lambda vehicle: {
            LockState.LOCK_STATE_LOCKED: False,
            LockState.LOCK_STATE_UNLOCKED: True,
        }.get(vehicle.state.charging.cable_lock),
    ),
    "defrost": LucidBinarySensorEntityDescription(
        key="defrost",
        key_path=["state", "hvac"],
        translation_key="defrost",
        icon="mdi:car-defrost-front",
        is_on_fn=lambda vehicle: vehicle.state.hvac.defrost
        == DefrostState.DEFROST_ON,
    ),
    "front_left_tire_soft_warning": LucidBinarySensorEntityDescription(
        key="soft_warn_left_front",
        key_path=["state", "chassis"],
        translation_key="front_left_tire_soft_warning",
        icon="mdi:car-tire-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda vehicle: TIRE_WARNING.get(
            vehicle.state.chassis.soft_warn_left_front
        ),
    ),
    "front_right_tire_soft_warning": LucidBinarySensorEntityDescription(
        key="soft_warn_right_front",
        key_path=["state", "chassis"],
        translation_key="front_right_tire_soft_warning",
        icon="mdi:car-tire-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda vehicle: TIRE_WARNING.get(
            vehicle.state.chassis.soft_warn_right_front
        ),
    ),
    "rear_left_tire_soft_warning": LucidBinarySensorEntityDescription(
        key="soft_warn_left_rear",
        key_path=["state", "chassis"],
        translation_key="rear_left_tire_soft_warning",
        icon="mdi:car-tire-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda vehicle: TIRE_WARNING.get(
            vehicle.state.chassis.soft_warn_left_rear
        ),
    ),
    "rear_right_tire_soft_warning": LucidBinarySensorEntityDescription(
        key="soft_warn_right_rear",
        key_path=["state", "chassis"],
        translation_key="rear_right_tire_soft_warning",
        icon="mdi:car-tire-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda vehicle: TIRE_WARNING.get(
            vehicle.state.chassis.soft_warn_right_rear
        ),
    ),
    "front_left_tire_hard_warning": LucidBinarySensorEntityDescription(
        key="hard_warn_left_front",
        key_path=["state", "chassis"],
        translation_key="front_left_tire_hard_warning",
        icon="mdi:car-tire-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda vehicle: TIRE_WARNING.get(
            vehicle.state.chassis.hard_warn_left_front
        ),
    ),
    "front_right_tire_hard_warning": LucidBinarySensorEntityDescription(
        key="hard_warn_right_front",
        key_path=["state", "chassis"],
        translation_key="front_right_tire_hard_warning",
        icon="mdi:car-tire-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda vehicle: TIRE_WARNING.get(
            vehicle.state.chassis.hard_warn_right_front
        ),
    ),
    "rear_left_tire_hard_warning": LucidBinarySensorEntityDescription(
        key="hard_warn_left_rear",
        key_path=["state", "chassis"],
        translation_key="rear_left_tire_hard_warning",
        icon="mdi:car-tire-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda vehicle: TIRE_WARNING.get(
            vehicle.state.chassis.hard_warn_left_rear
        ),
    ),
    "rear_right_tire_hard_warning": LucidBinarySensorEntityDescription(
        key="hard_warn_right_rear",
        key_path=["state", "chassis"],
        translation_key="rear_right_tire_hard_warning",
        icon="mdi:car-tire-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda vehicle: TIRE_WARNING.get(
            vehicle.state.chassis.hard_warn_right_rear
        ),
    ),
    "front_left_tire_sensor_fault": LucidBinarySensorEntityDescription(
        key="sensor_defective_left_front",
        key_path=["state", "chassis"],
        translation_key="front_left_tire_sensor_fault",
        icon="mdi:car-tire-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda vehicle: TIRE_SENSOR_DEFECTIVE.get(
            vehicle.state.chassis.sensor_defective_left_front
        ),
    ),
    "front_right_tire_sensor_fault": LucidBinarySensorEntityDescription(
        key="sensor_defective_right_front",
        key_path=["state", "chassis"],
        translation_key="front_right_tire_sensor_fault",
        icon="mdi:car-tire-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda vehicle: TIRE_SENSOR_DEFECTIVE.get(
            vehicle.state.chassis.sensor_defective_right_front
        ),
    ),
    "rear_left_tire_sensor_fault": LucidBinarySensorEntityDescription(
        key="sensor_defective_left_rear",
        key_path=["state", "chassis"],
        translation_key="rear_left_tire_sensor_fault",
        icon="mdi:car-tire-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda vehicle: TIRE_SENSOR_DEFECTIVE.get(
            vehicle.state.chassis.sensor_defective_left_rear
        ),
    ),
    "rear_right_tire_sensor_fault": LucidBinarySensorEntityDescription(
        key="sensor_defective_right_rear",
        key_path=["state", "chassis"],
        translation_key="rear_right_tire_sensor_fault",
        icon="mdi:car-tire-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda vehicle: TIRE_SENSOR_DEFECTIVE.get(
            vehicle.state.chassis.sensor_defective_right_rear
        ),
    ),
    "sentry_active": LucidBinarySensorEntityDescription(
        key="sentry_active_state",
        key_path=["state", "sentry_state"],
        translation_key="sentry_active",
        icon="mdi:cctv",
        # Whether Sentry is watching right now, which it may not be while it
        # is enabled (at home, low battery, occupied). Unknown when the car
        # sends no active state at all, rather than off.
        is_on_fn=lambda vehicle: (
            vehicle.state.sentry_state.sentry_active_state.is_active
            if vehicle.state.sentry_state.HasField("sentry_active_state")
            else None
        ),
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Lucid sensors from config entry."""
    coordinator: LucidDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities: list[LucidBinarySensor] = []

    for vehicle in coordinator.api.vehicles:
        entities.extend(
            [
                LucidBinarySensor(coordinator, vehicle, description)
                for (attribute_name, description) in SENSOR_TYPES.items()
            ]
        )

    async_add_entities(entities)


class LucidBinarySensor(LucidBaseEntity, BinarySensorEntity):
    """Representation of a Lucid vehicle sensor."""

    entity_description: LucidBinarySensorEntityDescription
    _attr_has_entity_name: bool = True

    def __init__(
        self,
        coordinator: LucidDataUpdateCoordinator,
        vehicle: Vehicle,
        description: LucidBinarySensorEntityDescription,
    ) -> None:
        """Initialize Lucid binary vehicle sensor."""
        super().__init__(coordinator, vehicle)
        self.entity_description = description
        self._attr_unique_id = f"{vehicle.config.vin}-{description.key}"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _LOGGER.debug(
            "Updating binary sensor '%s' of %s",
            self.entity_description.key,
            self.vehicle.config.nickname,
        )
        state = self.vehicle
        for attr in self.entity_description.key_path:
            state = getattr(state, attr)
        state = getattr(state, self.entity_description.key)
        super()._handle_coordinator_update()

    @property
    def is_on(self) -> bool:
        """Return the state."""
        return self.entity_description.is_on_fn(self.vehicle)

    @property
    def translation_key(self) -> str | None:
        """Return the translation key to translate the entity's states."""
        return self.entity_description.translation_key
