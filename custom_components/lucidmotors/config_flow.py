"""Config flow for Lucid Motors integration."""

from __future__ import annotations

import logging
from typing import Any

from lucidmotors import APIError, LucidAPI, Region
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import selector

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("username"): str,
        vol.Required("password"): str,
        vol.Required("region"): selector(
            {
                "select": {
                    "options": ["United States", "Saudi Arabia", "Europe"],
                },
            }
        ),
    }
)


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry."""
    _LOGGER.debug("Migrating from version %s", config_entry.version)

    if config_entry.version > 1:
        # This means the user has downgraded from a future version
        return False
    if config_entry.version == 1:
        new = {**config_entry.data}
        if config_entry.minor_version < 2:
            # Region was hardcoded to US previously
            new["region"] = "United States"
        hass.config_entries.async_update_entry(
            config_entry, data=new, version=1, minor_version=2
        )

    _LOGGER.debug("Migration to version %s successful", config_entry.version)

    return True


def region_by_name(name: str) -> Region:
    match name:
        case "United States":
            return Region.US
        case "Saudi Arabia":
            return Region.SA
        case "Europe":
            return Region.EU
        case _:
            raise ValueError("Unsupported region")


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """

    region = region_by_name(data["region"])

    api = LucidAPI(region=region)

    try:
        await api.login(data["username"], data["password"])

    except APIError as e:
        _LOGGER.error("Authentication failed: %s", e)
        raise InvalidAuth

    user = api.user

    assert user is not None  # if we logged in, we are a user

    return {"title": user.username}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Lucid Motors."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info["title"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
