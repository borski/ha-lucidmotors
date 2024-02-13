"""Coordinator for Lucid."""
from __future__ import annotations

import asyncio
from datetime import timedelta, datetime
import logging

from lucidmotors import APIError, LucidAPI, Vehicle, StatusCode, PowerState

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DEFAULT_UPDATE_INTERVAL,
    AWAKE_UPDATE_INTERVAL,
    FAST_UPDATE_INTERVAL,
    FAST_UPDATE_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


class LucidDataUpdateCoordinator(DataUpdateCoordinator):
    """Lucid API update coordinator."""

    api: LucidAPI
    username: str
    password: str

    # Map of VIN -> Vehicle.
    _vehicles: dict[str, Vehicle]

    # Map of vin -> path -> timeout. Tracks updates we've requested and are
    # expecting to see soon.
    _expected_updates: dict[str, dict[tuple[str, ...], datetime]]

    def __init__(
        self, hass: HomeAssistant, api: LucidAPI, username: str, password: str
    ) -> None:
        """Initialize the Lucid data update coordinator."""
        assert api.user is not None

        super().__init__(
            hass,
            _LOGGER,
            name=f"Lucid account {api.user.username}",
            update_interval=timedelta(seconds=DEFAULT_UPDATE_INTERVAL),
        )
        self.api = api
        self.username = username
        self.password = password
        self._vehicles = {}
        self._expected_updates = {}

    async def _async_update_data(self) -> None:
        """Fetch new data from API."""
        try:
            # If session will expire before our next update (* 1.5 for some wiggle
            # room), we should refresh our token now.
            async with asyncio.timeout(10):
                if self.api.session_time_remaining < (self.update_interval * 1.5):
                    _LOGGER.info(
                        "Session expires in %r, refreshing token",
                        self.api.session_time_remaining,
                    )
                    await self.api.authentication_refresh()
            async with asyncio.timeout(10):
                await self.api.fetch_vehicles()
                _LOGGER.info("Vehicles: %r", self.api.vehicles)
        except APIError as err:
            if err.code == StatusCode.UNAUTHENTICATED:  # token expired
                # NOTE: This also updates vehicles. If we switch to a
                # token-refreshing API, we'd have to also fetch_vehicles()
                # here.
                _LOGGER.info("Session expired, reauthenticating")
                await self.api.login(self.username, self.password)
            else:
                raise UpdateFailed(f"Error communicating with API: {err}") from err

        # Adjust our update interval based on vehicle state
        idle_update_interval = DEFAULT_UPDATE_INTERVAL

        # Check if any expected vehicle config/state has changed
        updated_or_expired = []
        current_time = datetime.now()

        for vehicle in self.api.vehicles:
            # If any vehicle is awake, let's poll more often
            if vehicle.state.power != PowerState.POWER_STATE_SLEEP:
                idle_update_interval = AWAKE_UPDATE_INTERVAL

            expected_updates = self._expected_updates.get(vehicle.config.vin, {})
            old_vehicle = self._vehicles.get(vehicle.config.vin, None)

            if expected_updates and old_vehicle is None:
                # The VIN just appeared out of nowhere? That sounds like a
                # change to me.
                self._expected_updates.pop(vehicle.config.vin)
                continue

            for path, timeout in expected_updates.items():
                old_value = old_vehicle
                new_value = vehicle

                for key in path:
                    _LOGGER.info("OLD: get %r from %r", key, old_value)
                    _LOGGER.info("NEW: get %r from %r", key, new_value)
                    old_value = getattr(old_value, key)
                    new_value = getattr(new_value, key)

                # Compare protobuf Messages - they do not have a working __eq__
                if hasattr(old_value, "SerializeToString"):
                    assert old_value is not None
                    assert new_value is not None
                    equal = old_value.SerializeToString(
                        deterministic=True
                    ) == new_value.SerializeToString(deterministic=True)
                # Compare anything else
                else:
                    equal = old_value == new_value

                _LOGGER.info(
                    "State %s => %r equal? %r timeout? %r",
                    vehicle.config.vin,
                    path,
                    equal,
                    timeout <= current_time,
                )

                if not equal or timeout <= current_time:
                    updated_or_expired.append((vehicle.config.vin, path))

        # Clear expected for values which have changed or timed out
        for vin, path in updated_or_expired:
            self._expected_updates[vin].pop(path)
            if not self._expected_updates[vin]:
                self._expected_updates.pop(vin)

        # Rebuild our local vehicle list - this is what entities update from
        self._vehicles.clear()
        for vehicle in self.api.vehicles:
            self._vehicles[vehicle.config.vin] = vehicle

        # In fast update mode, check if we need to drop back down to the regular interval
        if updated_or_expired and not self._expected_updates:
            self.update_interval = timedelta(seconds=idle_update_interval)
            self._fast_update_timeout = None
            _LOGGER.info("Fast update mode DISengaged")
        elif not self._expected_updates:
            # Not in fast update mode, just make sure we switch to either the
            # awake or default update interval depending on vehicle state.
            self.update_interval = timedelta(seconds=idle_update_interval)

    def get_vehicle(self, vin: str) -> Vehicle | None:
        """Look up a Vehicle object by VIN."""
        return self._vehicles.get(vin, None)

    async def expect_update(self, vin: str, path: tuple[str, ...]) -> None:
        """Tell the coordinator to expect a data update to the given field soon.

        The coordinator will check for updates more frequently until the data
        actually changes, or until FAST_UPDATE_TIMEOUT seconds pass.
        """
        _LOGGER.info("Fast update mode engaged")
        self.update_interval = timedelta(seconds=FAST_UPDATE_INTERVAL)

        if vin not in self._expected_updates:
            self._expected_updates[vin] = {}

        expiration_time = datetime.now() + timedelta(seconds=FAST_UPDATE_TIMEOUT)
        self._expected_updates[vin][path] = expiration_time
        await self.async_request_refresh()
