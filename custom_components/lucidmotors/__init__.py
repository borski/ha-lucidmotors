"""The Lucid Motors integration."""

from __future__ import annotations

from typing import Any

import logging

from lucidmotors import LucidAPI, Vehicle, Model, ModelVariant, enum_to_str

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import IntegrationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import LucidDataUpdateCoordinator
from .config_flow import region_by_name

PLATFORMS: list[Platform] = [
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.UPDATE,
    Platform.BUTTON,
    Platform.SWITCH,
    Platform.LIGHT,
    Platform.LOCK,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.NUMBER,
    Platform.SELECT,
]

_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry."""
    _LOGGER.debug("Migrating from version %s", config_entry.version)

    if config_entry.version > 1:
        # This means the user has downgraded from a future version
        return False
    if config_entry.version == 1:
        new = {**config_entry.data}
        if config_entry.minor_version < 2 and "region" not in new:
            # Region was hardcoded to US previously
            new["region"] = "United States"
        hass.config_entries.async_update_entry(
            config_entry, data=new, version=1, minor_version=2
        )

    _LOGGER.debug("Migration to version %s successful", config_entry.version)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Lucid Motors from a config entry."""

    hass.data.setdefault(DOMAIN, {})

    _LOGGER.debug(
        "Starting setup with config version %s.%s", entry.version, entry.minor_version
    )
    region = region_by_name(entry.data["region"])
    api = LucidAPI(auto_wake=True, region=region)
    await api.login(entry.data["username"], entry.data["password"])
    assert api.user is not None

    coordinator = LucidDataUpdateCoordinator(
        hass, api, entry.data["username"], entry.data["password"]
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.api.close()

    return unload_ok


class LucidBaseEntity(CoordinatorEntity[LucidDataUpdateCoordinator]):
    """Common base for Lucid vehicle entities."""

    coordinator: LucidDataUpdateCoordinator
    vin: str

    _attr_attribution: str = ATTRIBUTION
    _attr_has_entity_name: bool = True
    _attrs: dict[str, Any]

    def __init__(
        self, coordinator: LucidDataUpdateCoordinator, vehicle: Vehicle
    ) -> None:
        """Initialize entity."""
        super().__init__(coordinator)

        self.vin = vehicle.config.vin

        self._attrs = {
            "car": vehicle.config.nickname,
            "vin": self.vin,
        }
        model_str = enum_to_str(Model, vehicle.config.model)
        variant_str = enum_to_str(ModelVariant, vehicle.config.variant)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, vehicle.config.vin)},
            manufacturer="Lucid Motors",
            model=f"{model_str} {variant_str}",
            name=vehicle.config.nickname,
        )

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        self._handle_coordinator_update()

    @property
    def vehicle(self) -> Vehicle:
        """Get the vehicle associated with this Entity."""
        vehicle = self.coordinator.get_vehicle(self.vin)
        if vehicle is None:
            raise IntegrationError(f"Vehicle {self.vin} disappeared")
        return vehicle
