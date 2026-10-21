"""
Configuration flow.
Sign in to the CARMA Direct Consumption portal, then pick which meters to import
and what they measure.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .api import CarmaAuthError, CarmaError, MeterData, create_client
from .const import (
    CONF_ACCOUNT,
    CONF_ADDRESS,
    CONF_HISTORY_MONTHS,
    CONF_METERS,
    CONF_UNITS,
    DEFAULT_HISTORY_MONTHS,
    DOMAIN,
    ROLE_COLD,
    ROLE_ELECTRICITY,
    ROLE_HOT,
    ROLE_LABELS,
    ROLE_OTHER,
)
from .parser import unit_info

_ROLE_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[SelectOptionDict(value=k, label=v) for k, v in ROLE_LABELS.items()],
        mode=SelectSelectorMode.DROPDOWN,
    )
)

_OptionsBase = getattr(
    config_entries, "OptionsFlowWithReload", config_entries.OptionsFlow
)


def _field_key(meter_id: str, unit_raw: str) -> str:
    return f"Meter {meter_id} ({unit_raw or 'unit unknown'})"


def guess_roles(meters: list[MeterData]) -> dict[str, str]:
    """Guess the role of each meter based on its title and units."""

    roles: dict[str, str] = {}
    water_seen = 0
    for m in meters:
        kind = unit_info(m.unit_raw)[2]
        text = f"{m.title} {m.unit_raw}".lower()
        if kind == "water":
            if re.search(r"\bhot\b|hwt|\bhw\b", text):
                roles[m.meter_id] = ROLE_HOT
            elif re.search(r"\bcold\b|cwt|\bcw\b", text):
                roles[m.meter_id] = ROLE_COLD
            else:
                roles[m.meter_id] = ROLE_COLD if water_seen == 0 else ROLE_HOT
            water_seen += 1
        elif kind == "electricity":
            roles[m.meter_id] = ROLE_ELECTRICITY
        else:
            roles[m.meter_id] = ROLE_OTHER
    return roles


async def _validate(hass, address: str, account: str) -> list[MeterData]:
    return await create_client(hass, address, account).async_discover()


def _credentials_schema(defaults: Mapping[str, str] | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_ADDRESS, default=d.get(CONF_ADDRESS, "")): TextSelector(),
            vol.Required(CONF_ACCOUNT, default=d.get(CONF_ACCOUNT, "")): TextSelector(),
        }
    )


# pylint: disable-next=abstract-method
class CarmaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure credentials and select the meters to import."""

    VERSION = 1

    def __init__(self) -> None:
        self._credentials: dict[str, str] = {}
        self._meters: list[MeterData] = []

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry | None = None,
    ) -> CarmaOptionsFlow:
        return CarmaOptionsFlow()

    # -- initial setup ------------------------------------------------------- #
    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Validate portal credentials and discover account meters."""

        errors: dict[str, str] = {}
        if user_input is not None:
            address, account = (
                user_input[CONF_ADDRESS].strip(),
                user_input[CONF_ACCOUNT].strip(),
            )
            await self.async_set_unique_id(f"{address}|{account}".lower())
            self._abort_if_unique_id_configured()
            try:
                self._meters = await _validate(self.hass, address, account)
            except CarmaAuthError:
                errors["base"] = "invalid_auth"
            except CarmaError:
                errors["base"] = "cannot_connect"
            else:
                self._credentials = {CONF_ADDRESS: address, CONF_ACCOUNT: account}
                return await self.async_step_meters()
        return self.async_show_form(
            step_id="user", data_schema=_credentials_schema(user_input), errors=errors
        )

    async def async_step_meters(self, user_input: dict[str, Any] | None = None):
        """Select each meter's role and history depth."""

        keys = {_field_key(m.meter_id, m.unit_raw): m.meter_id for m in self._meters}
        if user_input is not None:
            roles = {keys[k]: user_input[k] for k in keys}
            return self.async_create_entry(
                title=self._credentials[CONF_ADDRESS],
                data={
                    **self._credentials,
                    CONF_METERS: roles,
                    CONF_UNITS: {m.meter_id: m.unit_raw for m in self._meters},
                    CONF_HISTORY_MONTHS: int(user_input[CONF_HISTORY_MONTHS]),
                },
            )
        guesses = guess_roles(self._meters)
        schema = {
            vol.Required(k, default=guesses[mid]): _ROLE_SELECTOR
            for k, mid in keys.items()
        }
        schema[vol.Required(CONF_HISTORY_MONTHS, default=DEFAULT_HISTORY_MONTHS)] = (
            NumberSelector(
                NumberSelectorConfig(min=1, max=60, step=1, mode=NumberSelectorMode.BOX)
            )
        )
        return self.async_show_form(step_id="meters", data_schema=vol.Schema(schema))

    async def async_step_reauth(self, _entry_data: Mapping[str, Any]):
        """Begin re-authentication for the existing config entry."""

        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        """Validate replacement credentials and update the config entry."""

        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            address, account = (
                user_input[CONF_ADDRESS].strip(),
                user_input[CONF_ACCOUNT].strip(),
            )
            try:
                await _validate(self.hass, address, account)
            except CarmaAuthError:
                errors["base"] = "invalid_auth"
            except CarmaError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_ADDRESS: address, CONF_ACCOUNT: account}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_credentials_schema(user_input or entry.data),
            errors=errors,
        )


class CarmaOptionsFlow(_OptionsBase):
    """Change what each meter measures."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Update the configured meter roles."""

        data = self.config_entry.data
        current = self.config_entry.options.get(CONF_METERS, data[CONF_METERS])
        units = data.get(CONF_UNITS, {})
        keys = {_field_key(mid, units.get(mid, "")): mid for mid in current}
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_METERS: {keys[k]: user_input[k] for k in keys}}
            )
        schema = {
            vol.Required(k, default=current[mid]): _ROLE_SELECTOR
            for k, mid in keys.items()
        }
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))
