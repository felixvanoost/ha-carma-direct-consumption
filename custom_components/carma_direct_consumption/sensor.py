"""Define per-meter sensors for daily usage and the meter's cumulative reading."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import MeterData
from .const import (
    CONF_UNITS,
    DOMAIN,
    ROLE_COLD,
    ROLE_ELECTRICITY,
    ROLE_HOT,
    ROLE_OTHER,
    ROLE_SKIP,
)
from .coordinator import CarmaConfigEntry, CarmaCoordinator, configured_roles
from .parser import unit_info

_HA_UNITS = {
    "m³": UnitOfVolume.CUBIC_METERS,
    "gal": UnitOfVolume.GALLONS,
    "kWh": UnitOfEnergy.KILO_WATT_HOUR,
}

_ROLE_DESCRIPTIONS = {
    ROLE_COLD: (
        SensorEntityDescription(
            key="last_day",
            translation_key="last_daily_usage",
            device_class=SensorDeviceClass.WATER,
        ),
        SensorEntityDescription(
            key="reading",
            translation_key="meter_reading",
            device_class=SensorDeviceClass.WATER,
            state_class=SensorStateClass.TOTAL_INCREASING,
        ),
    ),
    ROLE_HOT: (
        SensorEntityDescription(
            key="last_day",
            translation_key="last_daily_usage",
            device_class=SensorDeviceClass.WATER,
        ),
        SensorEntityDescription(
            key="reading",
            translation_key="meter_reading",
            device_class=SensorDeviceClass.WATER,
            state_class=SensorStateClass.TOTAL_INCREASING,
        ),
    ),
    ROLE_ELECTRICITY: (
        SensorEntityDescription(
            key="last_day",
            translation_key="last_daily_usage",
            device_class=SensorDeviceClass.ENERGY,
        ),
        SensorEntityDescription(
            key="reading",
            translation_key="meter_reading",
            device_class=SensorDeviceClass.ENERGY,
            state_class=SensorStateClass.TOTAL_INCREASING,
        ),
    ),
    ROLE_OTHER: (
        SensorEntityDescription(
            key="last_day",
            translation_key="last_daily_usage",
        ),
        SensorEntityDescription(
            key="reading",
            translation_key="meter_reading",
            state_class=SensorStateClass.TOTAL_INCREASING,
        ),
    ),
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
        description_index: int,
    ) -> None:
        super().__init__(coordinator)
        description = _ROLE_DESCRIPTIONS[role][description_index]
        unit_raw = entry.data.get(CONF_UNITS, {}).get(meter_id, "")
        unit, unit_class, _ = unit_info(unit_raw)
        compatible = (
            (role in (ROLE_COLD, ROLE_HOT) and unit_class == "volume")
            or (role == ROLE_ELECTRICITY and unit == "kWh")
        )
        self.entity_description = (
            description
            if compatible or description.device_class is None
            else replace(description, device_class=None)
        )
        self._meter_id = meter_id
        self._attr_unique_id = f"{meter_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, meter_id)},
            translation_key=role,
            serial_number=meter_id,
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


class CarmaLastDaySensor(_CarmaSensor):
    """Usage on the most recent day displayed in the portal."""

    def __init__(self, coordinator, entry, meter_id, role) -> None:
        super().__init__(coordinator, entry, meter_id, role, 0)

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

    def __init__(self, coordinator, entry, meter_id, role) -> None:
        super().__init__(coordinator, entry, meter_id, role, 1)

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
