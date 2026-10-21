"""Define per-meter sensors for daily usage and the meter's cumulative reading."""

from __future__ import annotations

from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import MeterData
from .const import DOMAIN, ROLE_LABELS, ROLE_SKIP, WATER_ROLES
from .coordinator import CarmaConfigEntry, CarmaCoordinator, configured_roles
from .parser import unit_info

_HA_UNITS = {
    "m³": UnitOfVolume.CUBIC_METERS,
    "gal": UnitOfVolume.GALLONS,
    "kWh": UnitOfEnergy.KILO_WATT_HOUR,
}


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: CarmaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the selected meter sensors."""

    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for meter_id, role in configured_roles(entry).items():
        if role != ROLE_SKIP:
            entities.append(CarmaLastDaySensor(coordinator, entry, meter_id, role))
            entities.append(CarmaReadingSensor(coordinator, entry, meter_id, role))
    async_add_entities(entities)


class _CarmaSensor(CoordinatorEntity[CarmaCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CarmaCoordinator,
        entry: CarmaConfigEntry,
        meter_id: str,
        role: str,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        self._meter_id = meter_id
        self._role = role
        self._attr_unique_id = f"{entry.entry_id}_{meter_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{meter_id}")},
            name=f"{ROLE_LABELS.get(role, role)} meter {meter_id}",
            manufacturer="CARMA",
            model="Smart meter",
        )

    @property
    def _meter(self) -> MeterData | None:
        return (self.coordinator.data or {}).get(self._meter_id)

    @property
    def available(self) -> bool:
        return super().available and self._meter is not None

    @property
    def native_unit_of_measurement(self) -> str | None:
        if (m := self._meter) is None:
            return None
        unit = unit_info(m.unit_raw)[0]
        return _HA_UNITS.get(unit, unit)

    @property
    def device_class(self) -> SensorDeviceClass | None:
        if (m := self._meter) is None:
            return None
        _, unit_class, _ = unit_info(m.unit_raw)
        if self._role in WATER_ROLES and unit_class == "volume":
            return SensorDeviceClass.WATER
        if unit_class == "energy" and unit_info(m.unit_raw)[0] == "kWh":
            return SensorDeviceClass.ENERGY
        return None


class CarmaLastDaySensor(_CarmaSensor):
    """Usage on the most recent day displayed in the portal."""

    _attr_name = "Last daily usage"

    def __init__(self, coordinator, entry, meter_id, role) -> None:
        super().__init__(coordinator, entry, meter_id, role, "last_day")

    @property
    def native_value(self) -> float | None:
        return self._meter.rows[-1][1] if self._meter and self._meter.rows else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        day: date | None = (
            self._meter.rows[-1][0] if self._meter and self._meter.rows else None
        )
        return {"date": day.isoformat()} if day else {}


class CarmaReadingSensor(_CarmaSensor):
    """The meter reading displayed by the portal."""

    _attr_name = "Meter reading"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator, entry, meter_id, role) -> None:
        super().__init__(coordinator, entry, meter_id, role, "reading")

    @property
    def available(self) -> bool:
        return super().available and self._meter.reading is not None

    @property
    def native_value(self) -> float | None:
        return self._meter.reading if self._meter else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self._meter.reading_date if self._meter else None
        return {"as_of": d.isoformat()} if d else {}
