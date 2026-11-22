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
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.core import HomeAssistant, callback
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


def _normalize_credentials(address: str, account: str) -> tuple[str, str, str]:
    """Trim credentials and return their normalized unique-account identity."""

    normalized_address = address.strip()
    normalized_account = account.strip()
    unique_id = f"{normalized_address}|{normalized_account}".casefold()
    return normalized_address, normalized_account, unique_id


def guess_roles(meters: list[MeterData]) -> dict[str, str]:
    """Infer meter roles from titles and units."""

    roles: dict[str, str] = {}
    water_seen = 0
    for m in meters:
        kind = unit_info(m.unit_raw).meter_type
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


async def _validate(
    hass: HomeAssistant, address: str, account: str
) -> list[MeterData]:
    """Validate portal credentials and discover account meters."""

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
    """Configure portal credentials and meter roles."""

    VERSION = 1

    def __init__(self) -> None:
        self._credentials: dict[str, str] = {}
        self._unique_id: str | None = None
        self._meters: list[MeterData] = []

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry | None = None,
    ) -> CarmaOptionsFlow:
        return CarmaOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate credentials and discover account meters."""

        errors: dict[str, str] = {}
        if user_input is not None:
            address, account, unique_id = _normalize_credentials(
                user_input[CONF_ADDRESS], user_input[CONF_ACCOUNT]
            )
            self._unique_id = unique_id
            await self.async_set_unique_id(unique_id)
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

    async def async_step_meters(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose each meter role and history depth."""

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

    async def async_step_reauth(
        self, _entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication for the existing entry."""

        return await self.async_step_reauth_confirm()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Rediscover meters and update account settings."""

        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            address, account, unique_id = _normalize_credentials(
                user_input[CONF_ADDRESS], user_input[CONF_ACCOUNT]
            )
            try:
                self._meters = await _validate(self.hass, address, account)
            except CarmaAuthError:
                errors["base"] = "invalid_auth"
            except CarmaError:
                errors["base"] = "cannot_connect"
            else:
                self._credentials = {CONF_ADDRESS: address, CONF_ACCOUNT: account}
                self._unique_id = unique_id
                return await self.async_step_reconfigure_meters()
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_credentials_schema(user_input or entry.data),
            errors=errors,
        )

    async def async_step_reconfigure_meters(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update meter roles after rediscovery."""

        entry = self._get_reconfigure_entry()
        data = entry.data
        options = dict(entry.options)
        configured_roles = options.get(CONF_METERS, data[CONF_METERS])
        current_roles = (
            configured_roles if isinstance(configured_roles, dict) else {}
        )
        keys = {
            _field_key(meter.meter_id, meter.unit_raw): meter.meter_id
            for meter in self._meters
        }

        if user_input is not None:
            roles = {keys[key]: user_input[key] for key in keys}
            units = {meter.meter_id: meter.unit_raw for meter in self._meters}
            options = {**options, CONF_METERS: dict(roles)}
            data_updates = {
                **self._credentials,
                CONF_METERS: dict(roles),
                CONF_UNITS: dict(units),
            }
            return self.async_update_reload_and_abort(
                entry,
                unique_id=self._unique_id,
                data_updates=data_updates,
                options=options,
                reason="reconfigure_successful",
            )

        guesses = guess_roles(self._meters)
        schema = {
            vol.Required(
                key,
                default=current_roles.get(meter_id, guesses[meter_id]),
            ): _ROLE_SELECTOR
            for key, meter_id in keys.items()
        }
        return self.async_show_form(
            step_id="reconfigure_meters",
            data_schema=vol.Schema(schema),
        )

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate replacement credentials and update the entry."""

        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            address, account, unique_id = _normalize_credentials(
                user_input[CONF_ADDRESS], user_input[CONF_ACCOUNT]
            )
            try:
                await _validate(self.hass, address, account)
            except CarmaAuthError:
                errors["base"] = "invalid_auth"
            except CarmaError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=unique_id,
                    data_updates={CONF_ADDRESS: address, CONF_ACCOUNT: account},
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_credentials_schema(user_input or entry.data),
            errors=errors,
        )


class CarmaOptionsFlow(_OptionsBase):
    """Change the role assigned to each meter."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
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
