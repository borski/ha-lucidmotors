"""Switch entities for Lucid vehicles."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
import logging
from typing import Any

from lucidmotors import (
    APIError,
    LucidAPI,
    Vehicle,
    DefrostState,
    ChargeState,
    BatteryPreconStatus,
    WalkawayState,
)

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LucidBaseEntity
from .const import DOMAIN
from .coordinator import LucidDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LucidSwitchEntityDescriptionMixin:
    """Mixin to describe a Lucid Switch entity."""

    key_path: list[str]
    turn_on_function: Callable[[LucidAPI, Vehicle], Coroutine[None, None, None]]
    turn_off_function: Callable[[LucidAPI, Vehicle], Coroutine[None, None, None]]
    on_value: Any


@dataclass(frozen=True)
class LucidSwitchEntityDescription(
    SwitchEntityDescription, LucidSwitchEntityDescriptionMixin
):
    """Describes Lucid switch entity."""


SWITCH_TYPES: tuple[LucidSwitchEntityDescription, ...] = (
    LucidSwitchEntityDescription(
        key="charge_state",
        key_path=["state", "charging"],
        translation_key="charging",
        icon="mdi:ev-station",
        device_class=SwitchDeviceClass.SWITCH,
        turn_on_function=lambda api, vehicle: api.start_charging(vehicle),
        turn_off_function=lambda api, vehicle: api.stop_charging(vehicle),
        on_value=ChargeState.CHARGE_STATE_CHARGING,
    ),
    LucidSwitchEntityDescription(
        key="preconditioning_status",
        key_path=["state", "battery"],
        translation_key="battery_preconditioning",
        icon="mdi:battery-plus-variant",
        device_class=SwitchDeviceClass.SWITCH,
        turn_on_function=lambda api, vehicle: api.battery_precon_on(vehicle),
        turn_off_function=lambda api, vehicle: api.battery_precon_off(vehicle),
        on_value=BatteryPreconStatus.BATTERY_PRECON_ON,
    ),
)


@dataclass(frozen=True)
class LucidPreferenceSwitchEntityDescriptionMixin:
    """Mixin to describe a Lucid switch backed by a model preference."""

    get_fn: Callable[[LucidDataUpdateCoordinator, str], bool | None]
    set_fn: Callable[
        [LucidDataUpdateCoordinator, Vehicle, bool], Coroutine[None, None, None]
    ]
    # Reader for the car's own reported state of the same feature, or None
    # where the vehicle reports nothing comparable. These switches write an
    # account-level preference and the vehicle does not necessarily follow
    # it, so where the car does report something we can notice the two
    # disagreeing and say so.
    #
    # No default: SwitchEntityDescription.key has none and is ordered after
    # the mixin's fields, so a default here is a TypeError at import.
    vehicle_state_fn: Callable[[Vehicle], bool | None] | None


@dataclass(frozen=True)
class LucidPreferenceSwitchEntityDescription(
    SwitchEntityDescription, LucidPreferenceSwitchEntityDescriptionMixin
):
    """Describes a preference-backed Lucid switch."""


PREFERENCE_SWITCH_TYPES: tuple[LucidPreferenceSwitchEntityDescription, ...] = (
    LucidPreferenceSwitchEntityDescription(
        key="passive_lock",
        translation_key="passive_lock",
        icon="mdi:car-door-lock",
        device_class=SwitchDeviceClass.SWITCH,
        entity_category=EntityCategory.CONFIG,
        vehicle_state_fn=lambda vehicle: vehicle.state.body.walkaway_lock
        == WalkawayState.WALKAWAY_ACTIVE,
        get_fn=lambda coordinator, vin: coordinator.get_passive_lock(vin),
        set_fn=lambda coordinator, vehicle, on: coordinator.async_set_passive_lock(
            vehicle, on
        ),
    ),
    LucidPreferenceSwitchEntityDescription(
        key="passive_unlock",
        translation_key="passive_unlock",
        icon="mdi:car-key",
        device_class=SwitchDeviceClass.SWITCH,
        entity_category=EntityCategory.CONFIG,
        # The car reports no passive-unlock state of its own to compare with.
        vehicle_state_fn=None,
        get_fn=lambda coordinator, vin: coordinator.get_passive_unlock(vin),
        set_fn=lambda coordinator, vehicle, on: coordinator.async_set_passive_unlock(
            vehicle, on
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Lucid switches from config entry."""
    coordinator: LucidDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities: list[LucidSwitch | LucidPreferenceSwitch] = []

    for vehicle in coordinator.api.vehicles:
        entities.extend(
            LucidSwitch(coordinator, vehicle, description)
            for description in SWITCH_TYPES
        )
        entities.extend(
            LucidPreferenceSwitch(coordinator, vehicle, description)
            for description in PREFERENCE_SWITCH_TYPES
        )

    async_add_entities(entities)


class LucidSwitch(LucidBaseEntity, SwitchEntity):
    """Representation of a Lucid vehicle switch."""

    entity_description: LucidSwitchEntityDescription
    _attr_has_entity_name: bool = True
    _is_on: bool

    def __init__(
        self,
        coordinator: LucidDataUpdateCoordinator,
        vehicle: Vehicle,
        description: LucidSwitchEntityDescription,
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
            "Updating switch '%s' of %s",
            self.entity_description.key,
            self.vehicle.config.nickname,
        )
        state = self.vehicle
        for attr in self.entity_description.key_path:
            state = getattr(state, attr)
        state = getattr(state, self.entity_description.key)

        self._is_on = state == self.entity_description.on_value
        super()._handle_coordinator_update()

    async def _expect_update(self) -> None:
        await self.coordinator.expect_update(
            self.vehicle.config.vin,
            tuple([*self.entity_description.key_path, self.entity_description.key]),
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch."""
        try:
            await self.entity_description.turn_on_function(self.api, self.vehicle)
            # Update our local state for the entity so that it doesn't appear
            # to revert to its previous state until the next API update
            self._is_on = True
            self.async_write_ha_state()
            await self._expect_update()
        except APIError as ex:
            raise HomeAssistantError(ex) from ex

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch."""
        try:
            await self.entity_description.turn_off_function(self.api, self.vehicle)
            self._is_on = False
            self.async_write_ha_state()
            await self._expect_update()
        except APIError as ex:
            raise HomeAssistantError(ex) from ex

    @property
    def is_on(self) -> bool:
        """Get the current state of the switch."""
        return self._is_on


class LucidPreferenceSwitch(LucidBaseEntity, SwitchEntity):
    """A Lucid switch backed by an account-level vehicle model preference.

    These write to UserPreferencesService, which stores a preference against
    the account and model. That is a different thing from the vehicle's own
    state, and the car is not obliged to follow it: as of lucidmotors 1.4.1
    there is no VehicleStateService RPC for passive entry at all, so this
    preference is the only knob the API exposes.

    In testing the server accepted and returned passive_lock=False while the
    car continued to report WALKAWAY_ACTIVE and kept unlocking on approach.
    Whether the setting reaches the vehicle is therefore up to Lucid, not to
    this integration. Where the car reports a comparable state we compare the
    two and log a warning when they disagree, so a preference that has not
    been honoured is visible rather than silent.
    """

    entity_description: LucidPreferenceSwitchEntityDescription
    _attr_has_entity_name: bool = True

    def __init__(
        self,
        coordinator: LucidDataUpdateCoordinator,
        vehicle: Vehicle,
        description: LucidPreferenceSwitchEntityDescription,
    ) -> None:
        """Initialize the preference switch."""
        super().__init__(coordinator, vehicle)
        self.entity_description = description
        self._attr_unique_id = f"{vehicle.config.vin}-{description.key}"
        self._attr_is_on = False
        # Preferences are read once at setup, separately from the vehicle
        # poll, so an entity can exist before its value is known.
        self._preference_known = False
        self._warned_divergence = False
        self._read_preference()

    @property
    def available(self) -> bool:
        """Report unavailable until the preferences record has been read.

        CoordinatorEntity implements available as a property, so setting
        _attr_available here would have no effect.
        """
        return super().available and self._preference_known

    def _read_preference(self) -> None:
        """Refresh local state from the coordinator's preference cache."""
        value = self.entity_description.get_fn(self.coordinator, self.vin)
        _LOGGER.debug(
            "Preference switch '%s' of %s reads as %s",
            self.entity_description.key,
            self.vehicle.config.nickname,
            value,
        )
        self._preference_known = value is not None
        if value is not None:
            self._attr_is_on = value
            self._check_vehicle_agrees(value)

    def _check_vehicle_agrees(self, preference: bool) -> None:
        """Warn once if the car's reported state contradicts the preference."""
        reader = self.entity_description.vehicle_state_fn
        if reader is None:
            return
        reported = reader(self.vehicle)
        if reported is None or reported == preference:
            self._warned_divergence = False
            return
        if not self._warned_divergence:
            self._warned_divergence = True
            _LOGGER.warning(
                "%s is set to %s for %s, but the car reports it as %s. The "
                "preference was stored, so this is the vehicle not applying "
                "it rather than a failed write",
                self.entity_description.key,
                preference,
                self.vehicle.config.nickname,
                reported,
            )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._read_preference()
        super()._handle_coordinator_update()

    async def _async_set(self, enabled: bool) -> None:
        try:
            await self.entity_description.set_fn(
                self.coordinator, self.vehicle, enabled
            )
        except APIError as ex:
            raise HomeAssistantError(ex) from ex
        self._read_preference()
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the preference."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the preference."""
        await self._async_set(False)
