"""Support for reading vehicle status from Lucid API."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import logging

from lucidmotors import Vehicle, WalkawayState, DoorState, HvacPower

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
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


SENSOR_TYPES: dict[str, LucidBinarySensorEntityDescription] = {
    "front_left_door": LucidBinarySensorEntityDescription(
        key="front_left_door",
        key_path=["state", "body"],
        translation_key="front_left_door",
        icon="mdi:door",
        device_class=BinarySensorDeviceClass.DOOR,
        is_on_fn=lambda vehicle: vehicle.state.body.front_left_door != DoorState.DOOR_STATE_CLOSED,
    ),
    "front_right_door": LucidBinarySensorEntityDescription(
        key="front_right_door",
        key_path=["state", "body"],
        translation_key="front_right_door",
        icon="mdi:door",
        device_class=BinarySensorDeviceClass.DOOR,
        is_on_fn=lambda vehicle: vehicle.state.body.front_right_door != DoorState.DOOR_STATE_CLOSED,
    ),
    "rear_left_door": LucidBinarySensorEntityDescription(
        key="rear_left_door",
        key_path=["state", "body"],
        translation_key="rear_left_door",
        icon="mdi:door",
        device_class=BinarySensorDeviceClass.DOOR,
        is_on_fn=lambda vehicle: vehicle.state.body.rear_left_door != DoorState.DOOR_STATE_CLOSED,
    ),
    "rear_right_door": LucidBinarySensorEntityDescription(
        key="rear_right_door",
        key_path=["state", "body"],
        translation_key="rear_right_door",
        icon="mdi:door",
        device_class=BinarySensorDeviceClass.DOOR,
        is_on_fn=lambda vehicle: vehicle.state.body.rear_right_door != DoorState.DOOR_STATE_CLOSED,
    ),
    "frunk": LucidBinarySensorEntityDescription(
        key="front_cargo",
        key_path=["state", "body"],
        translation_key="front_cargo",
        icon="mdi:door",
        device_class=BinarySensorDeviceClass.DOOR,
        is_on_fn=lambda vehicle: vehicle.state.body.front_cargo != DoorState.DOOR_STATE_CLOSED,
    ),
    "trunk": LucidBinarySensorEntityDescription(
        key="rear_cargo",
        key_path=["state", "body"],
        translation_key="rear_cargo",
        icon="mdi:door",
        device_class=BinarySensorDeviceClass.DOOR,
        is_on_fn=lambda vehicle: vehicle.state.body.rear_cargo != DoorState.DOOR_STATE_CLOSED,
    ),
    "charge_port_door": LucidBinarySensorEntityDescription(
        key="charge_port",
        key_path=["state", "body"],
        translation_key="charge_port_door",
        icon="mdi:door",
        device_class=BinarySensorDeviceClass.DOOR,
        is_on_fn=lambda vehicle: vehicle.state.body.charge_port != DoorState.DOOR_STATE_CLOSED,
    ),
    "walkaway_lock": LucidBinarySensorEntityDescription(
        key="walkaway_lock",
        key_path=["state", "body"],
        translation_key="walkaway_lock",
        icon="mdi:upload-lock",
        is_on_fn=lambda vehicle: vehicle.state.body.walkaway_lock == WalkawayState.WALKAWAY_ACTIVE,
    ),
    "hvac_power": LucidBinarySensorEntityDescription(
        key="power",
        key_path=["state", "hvac"],
        translation_key="hvac_power",
        icon="mdi:hvac",
        is_on_fn=lambda vehicle: vehicle.state.hvac.power != HvacPower.HVAC_OFF,
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
