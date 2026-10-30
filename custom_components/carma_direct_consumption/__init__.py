"""CARMA Direct Consumption integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.storage import Store
from homeassistant.helpers.translation import async_get_translations

from .api import create_client
from .const import (
    CONF_ACCOUNT,
    CONF_ADDRESS,
    DOMAIN,
    ROLE_SKIP,
    SERVICE_REIMPORT,
)
from .coordinator import CarmaConfigEntry, CarmaCoordinator, configured_roles

# Home Assistant requires this integration constant to use uppercase naming.
# pylint: disable-next=invalid-name
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = ["sensor"]
_REIMPORT_SCHEMA = vol.Schema(
    {
        vol.Optional("months", default=24): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=120)
        )
    }
)
_AUTO_RELOAD = hasattr(config_entries, "OptionsFlowWithReload")


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    """Register integration services."""

    async def _reimport(call: ServiceCall) -> None:
        entries = [
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.state is ConfigEntryState.LOADED
        ]
        if not entries:
            raise ServiceValidationError(
                "The CARMA Direct Consumption integration is not loaded"
            )
        for entry in entries:
            entry.async_create_background_task(
                hass,
                entry.runtime_data.async_reimport(call.data["months"]),
                f"{DOMAIN} reimport",
            )

    hass.services.async_register(
        DOMAIN, SERVICE_REIMPORT, _reimport, schema=_REIMPORT_SCHEMA
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CarmaConfigEntry) -> bool:
    """Set up a configured CARMA account."""

    client = create_client(hass, entry.data[CONF_ADDRESS], entry.data[CONF_ACCOUNT])
    translations = await async_get_translations(
        hass, hass.config.language, "device", [DOMAIN]
    )
    role_names = {
        role: translations.get(f"component.{DOMAIN}.device.{role}.name", role)
        for role in configured_roles(entry).values()
        if role != ROLE_SKIP
    }
    coordinator = CarmaCoordinator(hass, entry, client, role_names)
    await coordinator.async_load_state()
    entry.runtime_data = coordinator

    if coordinator.is_first_run:
        # The first run can read several years of history, so execute it in the background.
        coordinator.data = {}
        entry.async_create_background_task(
            hass, coordinator.async_refresh(), f"{DOMAIN} first import"
        )
    else:
        await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if not _AUTO_RELOAD:
        entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: CarmaConfigEntry) -> None:
    """Reload the integration after its options change."""

    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: CarmaConfigEntry) -> bool:
    """Unload the sensor platform."""

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    _hass: HomeAssistant, entry: CarmaConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Allow removal of devices that no longer correspond to a configured meter."""

    meters = configured_roles(entry)
    return not any(
        domain == DOMAIN and meter_id in meters
        for domain, meter_id in device_entry.identifiers
    )


async def async_remove_entry(hass: HomeAssistant, entry: CarmaConfigEntry) -> None:
    """Forget the import progress."""
    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}").async_remove()
