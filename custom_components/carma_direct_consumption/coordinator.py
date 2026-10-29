"""Polling and Home Assistant statistics import."""

from __future__ import annotations

import asyncio
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
    RETRY_INTERVAL,
    ROLE_SKIP,
    UPDATE_INTERVAL,
)
from .parser import unit_info

_LOGGER = logging.getLogger(__name__)
REIMPORT_STATE_KEY = "_reimport"

try:
    # Home Assistant 2025.4 onwards.
    from homeassistant.components.recorder.models import StatisticMeanType

    _NO_MEAN: dict = {"mean_type": StatisticMeanType.NONE}
except ImportError:
    _NO_MEAN = {"has_mean": False}

type CarmaConfigEntry = ConfigEntry["CarmaCoordinator"]


def statistic_id(meter_id: str) -> str:
    """Return the stable Recorder statistic ID for a meter."""

    return f"{DOMAIN}:meter_{re.sub(r'[^a-z0-9]+', '_', meter_id.lower()).strip('_')}"


def configured_roles(entry: ConfigEntry) -> dict[str, str]:
    """Return the configured meter roles."""

    return entry.options.get(CONF_METERS, entry.data[CONF_METERS])


class CarmaCoordinator(DataUpdateCoordinator[dict[str, MeterData]]):
    """Fetches the portal, imports history as external statistics, then serves the latest values."""

    config_entry: CarmaConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: CarmaConfigEntry,
        client: CarmaClient,
        role_names: dict[str, str],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self._role_names = role_names
        self._store: Store[dict] = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self._state: dict[str, dict] = {}
        self._refresh_lock = asyncio.Lock()

    async def async_load_state(self) -> None:
        """Load saved statistics import cursors."""

        self._state = await self._store.async_load() or {}

    @property
    def is_first_run(self) -> bool:
        """Whether the integration has no saved import progress."""

        return not self._state

    def _today(self) -> date:
        """Return today's date in the configured time zone."""

        return dt_util.now().date()

    def _months_needed(self, meter_id: str) -> int:
        """Choose a history depth that catches up the meter while retaining overlap."""

        history = int(
            self.config_entry.data.get(CONF_HISTORY_MONTHS, DEFAULT_HISTORY_MONTHS)
        )
        st = self._state.get(statistic_id(meter_id))
        if not st:
            return history
        cursor = date.fromisoformat(st["cursor"])
        today = self._today()
        behind = (today.year - cursor.year) * 12 + today.month - cursor.month
        return min(max(REFRESH_MONTHS, behind + 2), max(history, REFRESH_MONTHS))

    async def _async_update_data(self) -> dict[str, MeterData]:
        """Fetch readings, import statistics, and return the latest meter data."""

        roles = configured_roles(self.config_entry)
        wanted = {m for m, role in roles.items() if role != ROLE_SKIP}
        async with self._refresh_lock:
            pending_reimport = self._state.get(REIMPORT_STATE_KEY)
            if pending_reimport:
                roles = pending_reimport["roles"]
                months = pending_reimport["months"]
                wanted = {
                    meter_id for meter_id, role in roles.items() if role != ROLE_SKIP
                }
                meters = await self._async_fetch_meters(months, wanted)
                data = self._meter_data_by_id(meters, wanted)
                await self._async_rebuild_statistics(meters, roles, months)
                return data

            months = {meter_id: self._months_needed(meter_id) for meter_id in wanted}
            meters = await self._async_fetch_meters(months, wanted)
            data = self._meter_data_by_id(meters, wanted)
            self._import_meter_statistics(meters, roles, months)
            await self._store.async_save(self._state)
            return data

    async def _async_fetch_meters(
        self, months: dict[str, int], wanted: set[str]
    ) -> list[MeterData]:
        try:
            meters = await self.client.async_fetch(months, only=wanted)
        except CarmaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CarmaError as err:
            self.update_interval = RETRY_INTERVAL
            raise UpdateFailed(str(err)) from err
        self.update_interval = UPDATE_INTERVAL
        return meters

    def _meter_data_by_id(
        self, meters: list[MeterData], wanted: set[str]
    ) -> dict[str, MeterData]:
        data = {meter.meter_id: meter for meter in meters}
        missing = wanted - data.keys()
        unexpected = data.keys() - wanted
        if missing or unexpected or len(data) != len(meters):
            reasons = []
            if missing:
                reasons.append(f"missing meters: {', '.join(sorted(missing))}")
            if unexpected:
                reasons.append(f"unexpected meters: {', '.join(sorted(unexpected))}")
            if len(data) != len(meters):
                reasons.append("duplicate meter IDs")
            self.update_interval = RETRY_INTERVAL
            raise UpdateFailed(
                f"Incomplete response from CARMA portal ({'; '.join(reasons)})"
            )
        return data

    def _import_meter_statistics(
        self,
        meters: list[MeterData],
        roles: dict[str, str],
        months: dict[str, int],
    ) -> None:
        for meter in meters:
            level = (
                logging.INFO
                if months[meter.meter_id] > REFRESH_MONTHS
                else logging.DEBUG
            )
            day_label = "day" if len(meter.rows) == 1 else "days"
            month_label = "month" if len(meter.months) == 1 else "months"
            _LOGGER.log(
                level,
                "Meter %s: read %d %s from %d %s (%s to %s); stop reason: %s.",
                meter.meter_id,
                len(meter.rows),
                day_label,
                len(meter.months),
                month_label,
                meter.months[0].strftime("%b %Y") if meter.months else "-",
                meter.months[-1].strftime("%b %Y") if meter.months else "-",
                meter.stop_reason,
            )
            self._import_statistics(meter, roles[meter.meter_id])

    async def async_reimport(self, months: int) -> None:
        """Clear imported statistics and rebuild them from recent portal readings."""

        roles = configured_roles(self.config_entry)
        wanted = {meter_id for meter_id, role in roles.items() if role != ROLE_SKIP}
        requested_months = {meter_id: months for meter_id in wanted}

        async with self._refresh_lock:
            month_label = "month" if months == 1 else "months"
            meter_label = "meter" if len(wanted) == 1 else "meters"
            _LOGGER.info(
                "Re-importing %d %s of history for %d %s.",
                months,
                month_label,
                len(wanted),
                meter_label,
            )
            meters = await self._async_fetch_meters(requested_months, wanted)
            data = self._meter_data_by_id(meters, wanted)
            await self._async_rebuild_statistics(meters, roles, requested_months)
            self.async_set_updated_data(data)

    async def _async_rebuild_statistics(
        self,
        meters: list[MeterData],
        roles: dict[str, str],
        months: dict[str, int],
    ) -> None:
        """Clear and rebuild statistics."""

        marker = {"roles": roles, "months": months}
        pending_state = {**self._state, REIMPORT_STATE_KEY: marker}
        await self._store.async_save(pending_state)
        self._state = {REIMPORT_STATE_KEY: marker}

        cleared = asyncio.Event()
        get_instance(self.hass).async_clear_statistics(
            [statistic_id(meter_id) for meter_id in months],
            on_done=lambda: self.hass.loop.call_soon_threadsafe(cleared.set),
        )
        await cleared.wait()

        self._import_meter_statistics(meters, roles, months)
        completed_state = {
            key: value
            for key, value in self._state.items()
            if key != REIMPORT_STATE_KEY
        }
        await self._store.async_save(completed_state)
        self._state = completed_state

    def _import_statistics(self, meter: MeterData, role: str) -> None:
        """Add the daily consumption as cumulative statistics (one value per local midnight)."""

        stat_id = statistic_id(meter.meter_id)
        st = self._state.get(stat_id, {})
        cursor: str | None = st.get("cursor")
        sum_before: float = st.get("sum_before", 0.0)

        today = self._today()
        new = [
            (d, v)
            for d, v in meter.rows
            if d <= today and (cursor is None or d.isoformat() >= cursor)
        ]
        if not new:
            self._warn_if_cursor_is_ahead(meter, cursor)
            return

        stats = self._statistics_for_readings(new, sum_before)
        metadata = self._statistic_metadata(meter, role, stat_id)
        async_add_external_statistics(self.hass, metadata, stats)
        self._save_import_cursor(stat_id, new, sum_before)
        day_label = "day" if len(stats) == 1 else "days"
        _LOGGER.debug(
            "Imported statistics for %d %s for meter %s.",
            len(stats),
            day_label,
            meter.meter_id,
        )

    @staticmethod
    def _warn_if_cursor_is_ahead(meter: MeterData, cursor: str | None) -> None:
        """Warn when saved progress is later than data returned by the portal."""

        if cursor and meter.rows and cursor > meter.rows[-1][0].isoformat():
            _LOGGER.warning(
                "Meter %s: saved import cursor (%s) is later than the newest date returned by "
                "the CARMA portal (%s). Run the 'Re-import history' action to rebuild statistics.",
                meter.meter_id,
                cursor,
                meter.rows[-1][0],
            )

    @staticmethod
    def _statistics_for_readings(
        readings: list[tuple[date, float]], sum_before: float
    ) -> list[StatisticData]:
        """Create cumulative Recorder statistics for daily readings."""

        timezone = dt_util.get_default_time_zone()
        running = sum_before
        statistics = []
        for day, value in readings:
            running = round(running + value, 6)
            statistics.append(
                StatisticData(
                    start=datetime.combine(day, time.min, tzinfo=timezone),
                    state=running,
                    sum=running,
                )
            )
        return statistics

    def _statistic_metadata(
        self, meter: MeterData, role: str, stat_id: str
    ) -> StatisticMetaData:
        """Build Recorder metadata for a meter's external statistics."""

        unit, unit_class, _ = unit_info(meter.unit_raw)
        return StatisticMetaData(
            **_NO_MEAN,
            has_sum=True,
            name=f"{self._role_names[role]} (meter {meter.meter_id})",
            source=DOMAIN,
            statistic_id=stat_id,
            unit_class=unit_class,
            unit_of_measurement=unit,
        )

    def _save_import_cursor(
        self,
        stat_id: str,
        readings: list[tuple[date, float]],
        sum_before: float,
    ) -> None:
        """Keep the newest readings open for possible portal revisions."""

        index = max(0, len(readings) - OVERLAP_DAYS)
        self._state[stat_id] = {
            "cursor": readings[index][0].isoformat(),
            "sum_before": round(
                sum_before + sum(value for _, value in readings[:index]), 6
            ),
        }
