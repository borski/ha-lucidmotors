"""Switch entities for Lucid vehicles."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
import logging
from typing import Any

from lucidmotors import APIError, LucidAPI, Vehicle, AlarmMode

from homeassistant.components.select import (
    SelectEntity,
    SelectEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LucidBaseEntity
from .const import DOMAIN
from .coordinator import LucidDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

OPTION_TO_MODE_MAP = {
    "Off": AlarmMode.ALARM_MODE_OFF,
    "On": AlarmMode.ALARM_MODE_ON,
    "Push Notifications Only": AlarmMode.ALARM_MODE_SILENT,
}
MODE_TO_OPTION_MAP = {
    AlarmMode.ALARM_MODE_OFF: "Off",
    AlarmMode.ALARM_MODE_ON: "On",
    AlarmMode.ALARM_MODE_SILENT: "Push Notifications Only",
}


@dataclass(frozen=True)
class LucidSelectEntityDescriptionMixin:
    """Mixin to describe a Lucid select entity."""

    key_path: list[str]
    select_fn: Callable[[LucidAPI, Vehicle, AlarmMode], Coroutine[None, None, None]]
    current_value_fn: Callable[[Vehicle], str]


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
        options=[*OPTION_TO_MODE_MAP],
        select_fn=lambda api, vehicle, mode: api.alarm_control(vehicle, mode),
        current_value_fn=lambda vehicle: MODE_TO_OPTION_MAP[vehicle.state.alarm.mode],
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
        state = getattr(state, self.entity_description.key)

        super()._handle_coordinator_update()

    @property
    def current_option(self) -> str | None:
        """Return the selected entity option to represent the entity state."""
        return self.entity_description.current_value_fn(self.vehicle)

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        await self.entity_description.select_fn(
            self.api, self.vehicle, OPTION_TO_MODE_MAP[option]
        )
