"""Polling and Home Assistant statistics import."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import CarmaAuthError, CarmaClient, CarmaError, MeterData
from .const import (
    CONF_HISTORY_MONTHS,
    CONF_METERS,
    DEFAULT_HISTORY_MONTHS,
    DOMAIN,
    OVERLAP_DAYS,
    REFRESH_MONTHS,
    ROLE_LABELS,
    RETRY_INTERVAL,
    ROLE_SKIP,
    UPDATE_INTERVAL,
)
from .parser import unit_info

_LOGGER = logging.getLogger(__name__)

try:  
    # Home Assistant 2025.4 onwards.
    from homeassistant.components.recorder.models import StatisticMeanType

    _NO_MEAN: dict = {"mean_type": StatisticMeanType.NONE}
except ImportError:
    _NO_MEAN = {"has_mean": False}

type CarmaConfigEntry = ConfigEntry["CarmaCoordinator"]


def statistic_id(meter_id: str) -> str:
    return f"{DOMAIN}:meter_{re.sub(r'[^a-z0-9]+', '_', meter_id.lower()).strip('_')}"


def configured_roles(entry: ConfigEntry) -> dict[str, str]:
    return entry.options.get(CONF_METERS, entry.data[CONF_METERS])


class CarmaCoordinator(DataUpdateCoordinator[dict[str, MeterData]]):
    """Fetches the portal, imports history as external statistics, then serves the latest values."""

    config_entry: CarmaConfigEntry

    def __init__(self, hass: HomeAssistant, entry: CarmaConfigEntry, client: CarmaClient) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=UPDATE_INTERVAL)
        self.client = client
        self._store: Store[dict] = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self._state: dict[str, dict] = {}
        self._force_months: int | None = None

    async def async_load_state(self) -> None:
        self._state = await self._store.async_load() or {}

    @property
    def is_first_run(self) -> bool:
        return not self._state

    def _today(self) -> date:
        return dt_util.now().date()

    def _months_needed(self, meter_id: str) -> int:
        history = int(self.config_entry.data.get(CONF_HISTORY_MONTHS, DEFAULT_HISTORY_MONTHS))
        if self._force_months:
            return self._force_months
        st = self._state.get(statistic_id(meter_id))
        if not st:
            return history
        cursor = date.fromisoformat(st["cursor"])
        today = self._today()
        behind = (today.year - cursor.year) * 12 + today.month - cursor.month
        return min(max(REFRESH_MONTHS, behind + 2), max(history, REFRESH_MONTHS))

    async def _async_update_data(self) -> dict[str, MeterData]:
        roles = configured_roles(self.config_entry)
        wanted = {m for m, role in roles.items() if role != ROLE_SKIP}
        months = {m: self._months_needed(m) for m in wanted}
        try:
            meters = await self.client.async_fetch(months, only=wanted)
        except CarmaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CarmaError as err:
            self.update_interval = RETRY_INTERVAL
            raise UpdateFailed(str(err)) from err
        self.update_interval = UPDATE_INTERVAL

        data: dict[str, MeterData] = {}
        for meter in meters:
            data[meter.meter_id] = meter
            level = logging.INFO if months[meter.meter_id] > REFRESH_MONTHS else logging.DEBUG
            _LOGGER.log(
                level, "Meter %s: read %d day(s) from %d month(s) (%s to %s); stopped because: %s",
                meter.meter_id, len(meter.rows), len(meter.months),
                meter.months[0].strftime("%b %Y") if meter.months else "-",
                meter.months[-1].strftime("%b %Y") if meter.months else "-", meter.stop_reason)
            self._import_statistics(meter, roles[meter.meter_id])
        await self._store.async_save(self._state)
        return data

    async def async_reimport(self, months: int) -> None:
        """Throw away the imported statistics and read the last `months` readings again from scratch."""

        roles = configured_roles(self.config_entry)
        ids = [statistic_id(m) for m, role in roles.items() if role != ROLE_SKIP]
        _LOGGER.info("Re-importing %d month(s) of history for %d meter(s)", months, len(ids))
        self._force_months = months
        try:
            get_instance(self.hass).async_clear_statistics(ids)
            self._state = {}
            await self.async_refresh()
        finally:
            self._force_months = None

    def _import_statistics(self, meter: MeterData, role: str) -> None:
        """Add the daily consumption as cumulative statistics (one value per local midnight)."""

        stat_id = statistic_id(meter.meter_id)
        st = self._state.get(stat_id, {})
        cursor: str | None = st.get("cursor")
        sum_before: float = st.get("sum_before", 0.0)

        today = self._today()
        new = [(d, v) for d, v in meter.rows if d <= today and (cursor is None or d.isoformat() >= cursor)]
        if not new:
            if cursor and meter.rows and cursor > meter.rows[-1][0].isoformat():
                _LOGGER.warning(
                    "Meter %s: saved import progress (%s) is ahead of the newest day the portal has (%s). "
                    "Run the 'Re-import history' action to rebuild it.",
                    meter.meter_id, cursor, meter.rows[-1][0])
            return

        tz = dt_util.get_default_time_zone()
        running = sum_before
        stats: list[StatisticData] = []
        for day, value in new:
            running = round(running + value, 6)
            stats.append(StatisticData(start=datetime.combine(day, time.min, tzinfo=tz),
                                       state=running, sum=running))

        unit, unit_class, _ = unit_info(meter.unit_raw)
        metadata = StatisticMetaData(
            **_NO_MEAN,
            has_sum=True,
            name=f"{ROLE_LABELS.get(role, role)} (meter {meter.meter_id})",
            source=DOMAIN,
            statistic_id=stat_id,
            unit_class=unit_class,
            unit_of_measurement=unit,
        )
        async_add_external_statistics(self.hass, metadata, stats)

        # Keep the newest OVERLAP_DAYS 'open'.
        idx = max(0, len(new) - OVERLAP_DAYS)
        self._state[stat_id] = {
            "cursor": new[idx][0].isoformat(),
            "sum_before": round(sum_before + sum(v for _, v in new[:idx]), 6),
        }
        _LOGGER.debug("Imported %d day(s) for meter %s", len(stats), meter.meter_id)

    async def async_remove_state(self) -> None:
        await self._store.async_remove()
