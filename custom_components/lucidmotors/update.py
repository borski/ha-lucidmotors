"""Update entity for Lucid vehicles."""

from __future__ import annotations

import logging
from typing import Any
import httpx
from markdownify import markdownify as md

from lucidmotors import Vehicle, APIError, UpdateState

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LucidBaseEntity
from .const import DOMAIN
from .coordinator import LucidDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Lucid update entity from config entry."""
    coordinator: LucidDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    entities: list[LucidUpdateEntity] = []

    for vehicle in coordinator.api.vehicles:
        entities.append(LucidUpdateEntity(coordinator, vehicle))

    for entity in entities:
        await entity.async_update()

    async_add_entities(entities)


class LucidUpdateEntity(LucidBaseEntity, UpdateEntity):
    """Software update entity for Lucid vehicles."""

    _attr_force_update: bool = False
    _attr_icon: str = "mdi:update"
    _attr_supported_features = (
        UpdateEntityFeature.PROGRESS
        | UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.RELEASE_NOTES
    )

    def __init__(
        self, coordinator: LucidDataUpdateCoordinator, vehicle: Vehicle
    ) -> None:
        """Initialize the vehicle tracker."""
        super().__init__(coordinator, vehicle)

        self._attr_unique_id = f"{vehicle.config.vin}-update"
        self._attr_name = None
        self.api = coordinator.api

    @property
    def installed_version(self) -> str:
        """Return the current software version of the vehicle."""
        return self.vehicle.state.chassis.software_version

    @property
    def latest_version(self) -> str:
        """Return the latest available software version."""
        # The API reports version 0 if there is no update available.
        if self.vehicle.state.software_update.version_available_raw == 0:
            return self.installed_version
        return self.vehicle.state.software_update.version_available

    @property
    def in_progress(self) -> bool | int:
        """Return whether the update is in progress, and at what percentage."""
        if (
            self.vehicle.state.software_update.state
            != UpdateState.UPDATE_STATE_IN_PROGRESS
        ):
            return False

        return self.vehicle.state.software_update.percent_complete

    async def async_release_notes(self) -> str | None:
        """Return the release notes."""

        async with httpx.AsyncClient() as client:
            try:
                assert self._attr_release_url is not None

                response = await client.get(
                    self._attr_release_url,
                    follow_redirects=True,
                    headers={"Accept-Language": "en-US,en;q=0.9"},
                )

                if response.status_code == 200:
                    return md(response.text)
                else:
                    return f"Failed to retrieve content from {self._attr_release_url}. Status code: {response.status_code}"
            except Exception as ex:
                raise HomeAssistantError(ex) from ex

    async def async_update(self) -> None:
        """Update state of entity."""

        _LOGGER.debug(
            "async_update: latest_version = %s",
            self.latest_version,
        )

        update_release_notes = await self.api.get_update_release_notes(
            self.latest_version
        )

        self._attr_release_url = update_release_notes.url
        self._attr_release_summary = update_release_notes.info.description

    async def async_install(self, version, backup: bool, **kwargs: Any) -> None:
        """Install an Update."""

        _LOGGER.debug(
            "Installing update %s on %s",
            self.latest_version,
            self.vehicle.config.nickname,
        )

        try:
            if self.latest_version != self.installed_version:
                await self.api.apply_update(self.vehicle)
                self.async_write_ha_state()
        except APIError as ex:
            raise HomeAssistantError(ex) from ex
