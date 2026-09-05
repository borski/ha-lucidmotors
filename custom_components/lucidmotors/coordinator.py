"""Coordinator for Lucid."""

from __future__ import annotations

import asyncio
from datetime import timedelta, datetime
import logging
from typing import Any

import grpc

from lucidmotors import (
    APIError,
    LucidAPI,
    Vehicle,
    StatusCode,
    PowerState,
    Model,
    enum_to_str,
)
from lucidmotors.gen import (
    user_preferences_service_pb2,
    user_preferences_service_pb2_grpc,
)

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DEFAULT_UPDATE_INTERVAL,
    AWAKE_UPDATE_INTERVAL,
    FAST_UPDATE_INTERVAL,
    FAST_UPDATE_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


class LucidDataUpdateCoordinator(DataUpdateCoordinator[None]):
    """Lucid API update coordinator."""

    api: LucidAPI
    username: str
    password: str
    update_interval: timedelta

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
            name=f"Lucid account {api.user.email}",
            update_interval=timedelta(seconds=DEFAULT_UPDATE_INTERVAL),
        )
        self.api = api
        self.username = username
        self.password = password
        self._vehicles = {}
        self._expected_updates = {}
        # Model preferences are stored per model, not per vehicle: the API
        # keys them by model name, so two Airs on one account share a record.
        self._model_prefs: dict[str, Any] = {}
        self._prefs_commit_id: dict[str, int] = {}
        self._user_prefs_stub: Any = None

    async def _async_update_data(self) -> None:
        """Fetch new data from API."""
        try:
            # If session will expire before our next update (* 1.5 for some wiggle
            # room), we should refresh our token now.
            async with asyncio.timeout(10):
                if self.api.session_time_remaining < (self.update_interval * 1.5):
                    _LOGGER.debug(
                        "Session expires in %r, refreshing token",
                        self.api.session_time_remaining,
                    )
                    await self.api.authentication_refresh()
            async with asyncio.timeout(10):
                await self.api.fetch_vehicles()
                _LOGGER.debug("Vehicles: %r", self.api.vehicles)
        except APIError as err:
            if err.code == StatusCode.UNAUTHENTICATED:  # token expired
                # NOTE: This also updates vehicles. If we switch to a
                # token-refreshing API, we'd have to also fetch_vehicles()
                # here.
                _LOGGER.debug("Session expired, reauthenticating")
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
                    _LOGGER.debug("OLD: get %r from %r", key, old_value)
                    _LOGGER.debug("NEW: get %r from %r", key, new_value)
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

                _LOGGER.debug(
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

    # ------------------------------------------------------------------
    # Vehicle model preferences (passive lock / unlock)
    #
    # These live on UserPreferencesService rather than VehicleStateService,
    # which the lucidmotors client does not wrap, so the stub is built here
    # against the client's channel.
    # ------------------------------------------------------------------

    async def async_init_preferences(self, vehicles: list[Vehicle]) -> None:
        """Build the preferences stub and prime the cache for each vehicle."""
        # TODO: lucidmotors exposes no public accessor for its channel. If a
        # UserPreferencesService wrapper lands in the library this should
        # switch to it.
        self._user_prefs_stub = (
            user_preferences_service_pb2_grpc.UserPreferencesServiceStub(
                self.api._channel
            )
        )
        for vehicle in vehicles:
            await self._async_fetch_model_preferences(vehicle)

    def _model_name(self, vehicle: Vehicle) -> str:
        """Return the model name the preferences API keys records by."""
        return enum_to_str(Model, vehicle.config.model)

    async def _async_fetch_model_preferences(self, vehicle: Vehicle) -> None:
        """Fetch and cache the preferences record for a vehicle's model."""
        if self._user_prefs_stub is None:
            return

        model = self._model_name(vehicle)
        request = user_preferences_service_pb2.GetUserModelPreferencesRequest(
            model=model
        )

        try:
            response = await self._user_prefs_stub.GetUserModelPreferences(request)
        except grpc.aio.AioRpcError as err:
            if err.code() is grpc.StatusCode.NOT_FOUND:
                _LOGGER.info(
                    "No model preferences exist for %s yet, creating them", model
                )
                await self._async_create_model_preferences(model)
            else:
                _LOGGER.warning(
                    "Could not read model preferences for %s: %s", model, err
                )
            return

        self._model_prefs[model] = response.preferences
        self._prefs_commit_id[model] = response.commit_id

    async def _async_create_model_preferences(self, model: str) -> None:
        """Create a defaults record for a model, then cache it."""
        try:
            await self._user_prefs_stub.CreateUserModelPreferences(
                user_preferences_service_pb2.CreateUserModelPreferencesRequest(
                    preferences=user_preferences_service_pb2.VehicleModelPreferences(
                        model=model,
                    ),
                )
            )
            response = await self._user_prefs_stub.GetUserModelPreferences(
                user_preferences_service_pb2.GetUserModelPreferencesRequest(
                    model=model
                )
            )
        except grpc.aio.AioRpcError as err:
            _LOGGER.warning(
                "Could not create model preferences for %s: %s", model, err
            )
            return

        self._model_prefs[model] = response.preferences
        self._prefs_commit_id[model] = response.commit_id

    async def _async_set_model_preference(
        self, vehicle: Vehicle, field: str, value: bool
    ) -> None:
        """Write one preference field and confirm the server took it.

        Two things worth knowing about this endpoint.

        First, the wire format cannot express "false". passive_lock and
        passive_unlock are plain proto3 bools with no field presence, and
        SetUserModelPreferencesRequest carries no field mask, so setting
        either to False serialises to nothing and the field is simply absent
        from the request. In practice the server appears to treat the record
        as a replacement and does store False - it reads back as False on
        subsequent Get calls - but that is observed behaviour rather than
        anything the schema guarantees. The read-back below checks it rather
        than assuming it.

        Second, and more importantly, storing the preference is not the same
        as the car honouring it. This is an account-level preference; the
        vehicle reports its own walkaway state separately and has been seen
        to keep reporting WALKAWAY_ACTIVE with the preference set to False.
        There is no VehicleStateService RPC for passive entry in lucidmotors
        1.4.1, so this is the only control the API offers. The switch entity
        compares the two and warns when they disagree.
        """
        if self._user_prefs_stub is None:
            raise APIError("Vehicle preferences service is not available")

        model = self._model_name(vehicle)

        # Re-read first: if the app or another client has written since our
        # last fetch, our commit_id is stale and the server rejects the write.
        await self._async_fetch_model_preferences(vehicle)
        if model not in self._model_prefs:
            raise APIError(f"Could not read current preferences for {model}")

        new_prefs = user_preferences_service_pb2.VehicleModelPreferences()
        new_prefs.CopyFrom(self._model_prefs[model])
        setattr(new_prefs, field, value)

        _LOGGER.debug(
            "Writing %s=%s for %s at prev_commit_id=%d; fields on the wire: %s",
            field,
            value,
            model,
            self._prefs_commit_id.get(model, 0),
            [f.name for f, _ in new_prefs.ListFields()],
        )

        try:
            await self._user_prefs_stub.SetUserModelPreferences(
                user_preferences_service_pb2.SetUserModelPreferencesRequest(
                    preferences=new_prefs,
                    prev_commit_id=self._prefs_commit_id.get(model, 0),
                )
            )
        except grpc.aio.AioRpcError as err:
            raise APIError(f"Could not write {field} for {model}: {err}") from err

        # Confirm against the server rather than trusting the write.
        await self._async_fetch_model_preferences(vehicle)
        applied = getattr(self._model_prefs[model], field)
        _LOGGER.debug(
            "Read back %s=%s for %s at commit_id=%d (wanted %s)",
            field,
            applied,
            model,
            self._prefs_commit_id.get(model, 0),
            value,
        )
        if applied != value:
            raise APIError(
                f"The vehicle did not accept {field}={value}; "
                f"it still reports {applied}"
            )

    def _preference(self, vin: str, field: str) -> bool | None:
        """Return a cached preference for a vehicle, or None if unknown."""
        vehicle = self.get_vehicle(vin)
        if vehicle is None:
            return None
        prefs = self._model_prefs.get(self._model_name(vehicle))
        return getattr(prefs, field) if prefs is not None else None

    def get_passive_lock(self, vin: str) -> bool | None:
        """Return whether passive (walkaway) lock is enabled."""
        return self._preference(vin, "passive_lock")

    def get_passive_unlock(self, vin: str) -> bool | None:
        """Return whether passive (walkaway) unlock is enabled."""
        return self._preference(vin, "passive_unlock")

    async def async_set_passive_lock(self, vehicle: Vehicle, enabled: bool) -> None:
        """Enable or disable passive (walkaway) lock."""
        await self._async_set_model_preference(vehicle, "passive_lock", enabled)

    async def async_set_passive_unlock(self, vehicle: Vehicle, enabled: bool) -> None:
        """Enable or disable passive (walkaway) unlock."""
        await self._async_set_model_preference(vehicle, "passive_unlock", enabled)
