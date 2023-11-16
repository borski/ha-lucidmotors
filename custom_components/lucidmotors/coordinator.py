"""Coordinator for Lucid."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

from lucidmotors import APIError, LucidAPI, Vehicle

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)


class LucidDataUpdateCoordinator(DataUpdateCoordinator):
    """Lucid API update coordinator."""

    api: LucidAPI
    username: str
    password: str

    # Map of VIN -> Vehicle.
    _vehicles: dict[str, Vehicle]

    def __init__(
        self, hass: HomeAssistant, api: LucidAPI, username: str, password: str
    ) -> None:
        """Initialize the Lucid data update coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"Lucid account {api.user.username}",
            update_interval=timedelta(seconds=30),
        )
        self.api = api
        self.username = username
        self.password = password
        self._vehicles = {}

    async def _async_update_data(self) -> None:
        """Fetch new data from API."""
        try:
            async with asyncio.timeout(10):
                await self.api.fetch_vehicles()
        except APIError as err:
            if err.code == 16:  # token expired
                # NOTE: This also updates vehicles. If we switch to a
                # token-refreshing API, we'd have to also fetch_vehicles()
                # here.
                _LOGGER.info("Renewing session")
                await self.api.login(self.username, self.password)
            else:
                raise UpdateFailed(f"Error communicating with API: {err}") from err

        self._vehicles.clear()
        for vehicle in self.api.vehicles:
            self._vehicles[vehicle.config.vin] = vehicle

    def get_vehicle(self, vin: str) -> Vehicle | None:
        """Look up a Vehicle object by VIN."""
        return self._vehicles.get(vin, None)
