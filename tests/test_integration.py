"""Integration inside a real Home Assistant instance against the mock portal."""

from datetime import date, timedelta

import pytest
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    get_metadata,
    statistics_during_period,
)
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    MockConfigEntry,
)

from custom_components.carma_direct_consumption import const
from custom_components.carma_direct_consumption.const import (
    CONF_ACCOUNT,
    CONF_ADDRESS,
    CONF_HISTORY_MONTHS,
    CONF_METERS,
    CONF_UNITS,
    DOMAIN,
)
from custom_components.carma_direct_consumption import coordinator as coordinator_module
from custom_components.carma_direct_consumption.coordinator import (
    CarmaCoordinator,
    REIMPORT_STATE_KEY,
    statistic_id,
)

from .mock_carma import ACCOUNT, ADDRESS, METERS, TODAY

COLD, HOT, ELEC = "21145135", "21167069", "M0129495"
ROLES = {COLD: "cold_water", HOT: "hot_water", ELEC: "electricity"}


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    """Use a stable date for coordinator history filtering."""

    monkeypatch.setattr(CarmaCoordinator, "_today", lambda self: TODAY)


async def setup_entry(hass, roles=None, history=12) -> MockConfigEntry:
    """Create and set up an entry with the requested meter roles."""

    if roles is None:
        roles = ROLES
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=ADDRESS,
        unique_id=f"{ADDRESS}|{ACCOUNT}".lower(),
        data={
            CONF_ADDRESS: ADDRESS,
            CONF_ACCOUNT: ACCOUNT,
            CONF_METERS: roles,
            CONF_UNITS: {m[0]: m[1] for m in METERS},
            CONF_HISTORY_MONTHS: history,
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)
    return entry


async def stats(hass, meter_id):
    """Return recorded statistics for a meter."""

    sid = statistic_id(meter_id)
    result = await get_instance(hass).async_add_executor_job(
        lambda: statistics_during_period(
            hass,
            dt_util.utc_from_timestamp(0),
            None,
            {sid},
            "hour",
            None,
            {"sum", "state"},
        )
    )
    return result.get(sid, [])


async def meta(hass, meter_id):
    """Return Recorder metadata for a meter."""

    sid = statistic_id(meter_id)
    found = await get_instance(hass).async_add_executor_job(
        lambda: get_metadata(hass, statistic_ids={sid})
    )
    return found.get(sid, (None, None))[1]


# --------------------------------------------------------------------------- #
async def test_first_import_hot_cold_electricity(
    # pylint: disable-next=unused-argument
    recorder_mock,
    # pylint: disable-next=unused-argument
    enable_custom_integrations,
    hass,
    portal,
):
    """Import all meter roles and create corresponding sensors."""

    entry = await setup_entry(hass)
    assert not portal.violations
    days = len(portal.all_days())

    for idx, (mid, *_rest) in enumerate(METERS):
        rows = await stats(hass, mid)
        assert len(rows) == days
        assert rows[-1]["sum"] == pytest.approx(portal.total(idx))
        assert rows[0]["sum"] == pytest.approx(portal.value(idx, portal.all_days()[0]))

    assert (await meta(hass, COLD))["name"] == f"Cold water (meter {COLD})"
    assert (await meta(hass, HOT))["name"] == f"Hot water (meter {HOT})"
    assert (await meta(hass, ELEC))["name"] == f"Electricity (meter {ELEC})"
    assert (await meta(hass, COLD))["unit_of_measurement"] == "m³"
    assert (await meta(hass, ELEC))["unit_of_measurement"] == "kWh"
    assert (await meta(hass, COLD))["unit_class"] == "volume"
    assert (await meta(hass, ELEC))["unit_class"] == "energy"

    last = hass.states.get("sensor.cold_water_last_daily_usage")
    assert float(last.state) == pytest.approx(portal.value(0, portal.through))
    assert (
        last.attributes["device_class"] == "water"
        and last.attributes["unit_of_measurement"] == "m³"
    )
    assert last.attributes["date"] == portal.through.isoformat()
    reading = hass.states.get("sensor.hot_water_meter_reading")
    assert float(reading.state) == pytest.approx(portal.reading(1))
    assert (
        hass.states.get(
            "sensor.electricity_last_daily_usage"
        ).attributes["device_class"]
        == "energy"
    )
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, COLD), entry.entry_id
    )
    assert device.name == "Cold water"
    assert device.serial_number == COLD
    sensor_entry = er.async_get(hass).async_get("sensor.cold_water_last_daily_usage")
    assert sensor_entry.unique_id == f"{COLD}_last_day"
    assert sensor_entry.translation_key == "last_daily_usage"


async def test_role_mismatch_does_not_apply_incompatible_device_class(
    # pylint: disable-next=unused-argument
    recorder_mock,
    # pylint: disable-next=unused-argument
    enable_custom_integrations,
    hass,
    # pylint: disable-next=unused-argument
    portal,
):
    """Do not assign the water class to a meter configured as electricity."""

    await setup_entry(hass, roles={COLD: "electricity", HOT: "skip", ELEC: "skip"})

    last = hass.states.get("sensor.electricity_last_daily_usage")
    reading = hass.states.get("sensor.electricity_meter_reading")
    assert last.attributes["unit_of_measurement"] == "m³"
    assert reading.attributes["unit_of_measurement"] == "m³"
    assert "device_class" not in last.attributes
    assert "device_class" not in reading.attributes


async def test_next_day_and_revision_keep_totals_exact(
    # pylint: disable-next=unused-argument
    recorder_mock,
    # pylint: disable-next=unused-argument
    enable_custom_integrations,
    hass,
    portal,
):
    """Apply revised and new readings without changing older totals."""

    entry = await setup_entry(hass)
    before = {mid: await stats(hass, mid) for mid, *_ in METERS}

    portal.through = date(2026, 10, 19)
    portal.revisions[(0, date(2026, 10, 17))] = 0.5
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)

    for idx, (mid, *_) in enumerate(METERS):
        rows = await stats(hass, mid)
        assert len(rows) == len(before[mid]) + 1
        assert rows[-1]["sum"] == pytest.approx(portal.total(idx))
        assert rows[10]["sum"] == pytest.approx(before[mid][10]["sum"])

    cold = await stats(hass, COLD)
    assert cold[-1]["sum"] - before[COLD][-1]["sum"] == pytest.approx(
        0.5 + portal.value(0, date(2026, 10, 19))
    )
    assert (
        hass.states.get("sensor.cold_water_last_daily_usage").attributes["date"]
        == "2026-10-19"
    )


async def test_skipped_meter_is_not_imported(
    # pylint: disable-next=unused-argument
    recorder_mock,
    # pylint: disable-next=unused-argument
    enable_custom_integrations,
    hass,
    portal,
):
    """Do not create statistics or sensors for skipped meters."""

    await setup_entry(hass, roles={COLD: "cold_water", HOT: "hot_water", ELEC: "skip"})
    assert await stats(hass, ELEC) == []
    assert hass.states.get("sensor.electricity_last_daily_usage") is None
    assert len(await stats(hass, COLD)) == len(portal.all_days())


async def test_swapping_hot_and_cold_renames_without_duplicating(
    # pylint: disable-next=unused-argument
    recorder_mock,
    # pylint: disable-next=unused-argument
    enable_custom_integrations,
    hass,
    # pylint: disable-next=unused-argument
    portal,
):
    """Update meter labels when the user swaps water roles."""

    entry = await setup_entry(hass)
    n = len(await stats(hass, COLD))

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    swapped = {
        f"Meter {COLD} (m^3-wtr)": "hot_water",
        f"Meter {HOT} (m^3-wtr)": "cold_water",
        f"Meter {ELEC} (kWh)": "electricity",
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], swapped
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)

    assert (await meta(hass, COLD))["name"] == f"Hot water (meter {COLD})"
    assert (await meta(hass, HOT))["name"] == f"Cold water (meter {HOT})"
    assert len(await stats(hass, COLD)) == n


async def test_config_flow_roles_and_import(
    # pylint: disable-next=unused-argument
    recorder_mock,
    # pylint: disable-next=unused-argument
    enable_custom_integrations,
    hass,
    # pylint: disable-next=unused-argument
    portal,
):
    """Configure portal credentials, roles, and history depth."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == FlowResultType.FORM and result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS, CONF_ACCOUNT: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS, CONF_ACCOUNT: ACCOUNT}
    )
    assert result["step_id"] == "meters"
    guesses = {
        str(k): k.default()
        for k in result["data_schema"].schema
        if k.default is not None and callable(k.default)
    }
    assert guesses[f"Meter {COLD} (m^3-wtr)"] == "cold_water"
    assert guesses[f"Meter {HOT} (m^3-wtr)"] == "hot_water"
    assert guesses[f"Meter {ELEC} (kWh)"] == "electricity"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            f"Meter {COLD} (m^3-wtr)": "hot_water",
            f"Meter {HOT} (m^3-wtr)": "cold_water",
            f"Meter {ELEC} (kWh)": "electricity",
            CONF_HISTORY_MONTHS: 3,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_METERS] == {
        COLD: "hot_water",
        HOT: "cold_water",
        ELEC: "electricity",
    }
    assert result["data"][CONF_HISTORY_MONTHS] == 3

    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)
    assert (await meta(hass, COLD))["name"] == f"Hot water (meter {COLD})"
    first_day = (await stats(hass, COLD))[0]
    assert first_day["start"] == pytest.approx(
        dt_util.as_timestamp(dt_util.start_of_local_day(date(2026, 8, 1)))
    )

    again = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    again = await hass.config_entries.flow.async_configure(
        again["flow_id"], {CONF_ADDRESS: ADDRESS, CONF_ACCOUNT: ACCOUNT}
    )
    assert (
        again["type"] == FlowResultType.ABORT
        and again["reason"] == "already_configured"
    )


async def test_config_flow_cannot_connect(
    # pylint: disable-next=unused-argument
    recorder_mock,
    # pylint: disable-next=unused-argument
    enable_custom_integrations,
    hass,
    monkeypatch,
    # pylint: disable-next=unused-argument
    socket_enabled,
):
    """Show a connection error when the portal cannot be reached."""

    monkeypatch.setattr(const, "BASE_URL", "http://127.0.0.1:9/portal/")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS, CONF_ACCOUNT: ACCOUNT}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_history_walk_is_logged(
    # pylint: disable-next=unused-argument
    recorder_mock,
    # pylint: disable-next=unused-argument
    enable_custom_integrations,
    hass,
    # pylint: disable-next=unused-argument
    portal,
    caplog,
):
    """Log the history range and reason month navigation stopped."""

    caplog.set_level("INFO")
    await setup_entry(hass)
    assert (
        f"Meter {COLD}: read {len(portal.all_days())} days from 5 months (Jun 2026 to Oct 2026)"
        in caplog.text
    )
    assert "stop reason: Prev Month stopped moving." in caplog.text


async def test_reimport_service_rebuilds_history_consistently(
    # pylint: disable-next=unused-argument
    recorder_mock,
    # pylint: disable-next=unused-argument
    enable_custom_integrations,
    hass,
    portal,
    monkeypatch,
):
    """Rebuild the requested history and preserve cumulative totals."""

    entry = await setup_entry(hass, history=2)
    assert len(await stats(hass, COLD)) < len(portal.all_days())
    fetch_months = []
    async_fetch = entry.runtime_data.client.async_fetch

    async def track_fetch(months, only=None):
        fetch_months.append(months.copy())
        return await async_fetch(months, only=only)

    monkeypatch.setattr(entry.runtime_data.client, "async_fetch", track_fetch)

    await hass.services.async_call(
        DOMAIN, "reimport_history", {"months": 12}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)
    assert fetch_months == [{meter_id: 12 for meter_id in ROLES}]

    for idx, (mid, *_) in enumerate(METERS):
        rows = await stats(hass, mid)
        assert len(rows) == len(portal.all_days())
        assert rows[-1]["sum"] == pytest.approx(portal.total(idx))
        sums = [r["sum"] for r in rows]
        assert sums == sorted(sums)

    await hass.services.async_call(
        DOMAIN, "reimport_history", {"months": 2}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)
    assert len(fetch_months) == 2
    assert fetch_months[-1] == {meter_id: 2 for meter_id in ROLES}
    for idx, (mid, *_) in enumerate(METERS):
        rows = await stats(hass, mid)
        sums = [r["sum"] for r in rows]
        assert sums == sorted(sums) and 0 < len(rows) < len(portal.all_days())
        assert rows[-1]["sum"] == pytest.approx(
            sum(
                portal.value(idx, d) for d in portal.all_days() if d >= date(2026, 9, 1)
            )
        )


async def test_failed_reimport_preserves_existing_history(
    # pylint: disable-next=unused-argument
    recorder_mock,
    # pylint: disable-next=unused-argument
    enable_custom_integrations,
    hass,
    portal,
):
    """Keep existing statistics when the re-import fetch fails."""

    entry = await setup_entry(hass)
    before = await stats(hass, COLD)
    portal.fail_prev_posts = 99

    with pytest.raises(UpdateFailed, match="HTTP 500 from the CARMA portal"):
        await entry.runtime_data.async_reimport(12)
    await async_wait_recording_done(hass)

    assert await stats(hass, COLD) == before


async def test_incomplete_reimport_preserves_existing_history(
    # pylint: disable-next=unused-argument
    recorder_mock,
    # pylint: disable-next=unused-argument
    enable_custom_integrations,
    hass,
    # pylint: disable-next=unused-argument
    portal,
    monkeypatch,
):
    """Keep existing statistics when the re-import response omits a meter."""

    entry = await setup_entry(hass)
    before = await stats(hass, COLD)
    client = entry.runtime_data.client
    async_fetch = client.async_fetch

    async def omit_meter(months, only=None):
        meters = await async_fetch(months, only=only)
        return meters[:-1]

    monkeypatch.setattr(client, "async_fetch", omit_meter)
    with pytest.raises(UpdateFailed, match="missing meters"):
        await entry.runtime_data.async_reimport(12)

    assert await stats(hass, COLD) == before


async def test_reimport_recovers_when_statistics_import_fails(
    # pylint: disable-next=unused-argument
    recorder_mock,
    # pylint: disable-next=unused-argument
    enable_custom_integrations,
    hass,
    portal,
    monkeypatch,
):
    """Rebuild all cleared statistics on the next refresh after an import failure."""

    entry = await setup_entry(hass)
    coordinator = entry.runtime_data
    add_statistics = coordinator_module.async_add_external_statistics
    calls = 0

    def fail_second_import(hass, metadata, statistics):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated statistic import failure")
        add_statistics(hass, metadata, statistics)

    monkeypatch.setattr(
        coordinator_module, "async_add_external_statistics", fail_second_import
    )
    with pytest.raises(RuntimeError, match="simulated statistic import failure"):
        await coordinator.async_reimport(12)

    await coordinator.async_load_state()
    monkeypatch.setattr(
        coordinator_module, "async_add_external_statistics", add_statistics
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)

    for idx, (meter_id, *_) in enumerate(METERS):
        rows = await stats(hass, meter_id)
        assert len(rows) == len(portal.all_days())
        assert rows[-1]["sum"] == pytest.approx(portal.total(idx))


async def test_reimport_recovers_when_saving_state_fails(
    # pylint: disable-next=unused-argument
    recorder_mock,
    # pylint: disable-next=unused-argument
    enable_custom_integrations,
    hass,
    portal,
    monkeypatch,
):
    """Rebuild statistics again if saving completed import cursors fails."""

    entry = await setup_entry(hass)
    coordinator = entry.runtime_data
    save_state = Store.async_save

    async def fail_completed_save(store, state):
        if REIMPORT_STATE_KEY not in state:
            raise RuntimeError("simulated state save failure")
        await save_state(store, state)

    monkeypatch.setattr(Store, "async_save", fail_completed_save)
    with pytest.raises(RuntimeError, match="simulated state save failure"):
        await coordinator.async_reimport(12)

    await coordinator.async_load_state()
    monkeypatch.setattr(Store, "async_save", save_state)
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)

    for idx, (meter_id, *_) in enumerate(METERS):
        rows = await stats(hass, meter_id)
        assert len(rows) == len(portal.all_days())
        assert rows[-1]["sum"] == pytest.approx(portal.total(idx))


async def test_failed_refresh_retries_in_30_minutes(
    # pylint: disable-next=unused-argument
    recorder_mock,
    # pylint: disable-next=unused-argument
    enable_custom_integrations,
    hass,
    portal,
):
    """Retry failed scheduled updates after the configured delay."""

    portal.fail_prev_posts = 99
    entry = await setup_entry(hass)
    assert entry.runtime_data.last_update_success is False
    assert entry.runtime_data.update_interval == const.RETRY_INTERVAL


async def test_scheduled_refresh_picks_up_new_day_without_manual_refresh(
    # pylint: disable-next=unused-argument
    recorder_mock,
    # pylint: disable-next=unused-argument
    enable_custom_integrations,
    hass,
    portal,
):
    """Fetch a newly published day during the scheduled refresh."""

    await setup_entry(hass)
    n = len(await stats(hass, COLD))
    sensor = "sensor.cold_water_last_daily_usage"
    assert hass.states.get(sensor).attributes["date"] == "2026-10-18"

    portal.through = date(2026, 10, 19)
    async_fire_time_changed(
        hass, dt_util.utcnow() + const.UPDATE_INTERVAL + timedelta(seconds=5)
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)

    assert hass.states.get(sensor).attributes["date"] == "2026-10-19"
    assert len(await stats(hass, COLD)) == n + 1


async def test_unpublished_zero_days_are_not_imported_and_later_days_arrive(
    # pylint: disable-next=unused-argument
    recorder_mock,
    # pylint: disable-next=unused-argument
    enable_custom_integrations,
    hass,
    portal,
):
    """Skip padded future zeroes and import them once published."""

    portal.pad_zeros = True
    portal.through = date(2026, 10, 15)
    entry = await setup_entry(hass)

    for idx, (mid, *_) in enumerate(METERS):
        rows = await stats(hass, mid)
        assert rows[-1]["start"] == dt_util.as_timestamp(
            dt_util.start_of_local_day(portal.through)
        )
        assert rows[-1]["sum"] == pytest.approx(portal.total(idx))
    sensor = hass.states.get("sensor.cold_water_last_daily_usage")
    assert sensor.attributes["date"] == "2026-10-15"
    assert float(sensor.state) == pytest.approx(portal.value(0, date(2026, 10, 15)))

    before = {mid: len(await stats(hass, mid)) for mid, *_ in METERS}
    portal.through = date(2026, 10, 19)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)

    for idx, (mid, *_) in enumerate(METERS):
        rows = await stats(hass, mid)
        assert len(rows) == before[mid] + 4
        assert rows[-1]["sum"] == pytest.approx(portal.total(idx))
    assert (
        hass.states.get("sensor.cold_water_last_daily_usage").attributes["date"]
        == "2026-10-19"
    )


async def test_real_zero_days_inside_the_month_are_kept(
    # pylint: disable-next=unused-argument
    recorder_mock,
    # pylint: disable-next=unused-argument
    enable_custom_integrations,
    hass,
    portal,
):
    """Keep valid zero-usage dates within the reported month."""

    portal.pad_zeros = True
    portal.through = date(2026, 10, 19)
    portal.zero_days = {
        date(2026, 10, 2),
        date(2026, 10, 3),
        date(2026, 10, 10),
        date(2026, 10, 11),
    }
    await setup_entry(hass)

    def ts(d):
        return dt_util.as_timestamp(dt_util.start_of_local_day(d))

    for idx, (mid, *_) in enumerate(METERS):
        rows = await stats(hass, mid)
        by_start = {r["start"]: r for r in rows}
        assert len(rows) == len(portal.all_days())
        assert rows[-1]["start"] == ts(date(2026, 10, 19))
        assert rows[-1]["sum"] == pytest.approx(portal.total(idx))
        assert by_start[ts(date(2026, 10, 3))]["sum"] == pytest.approx(
            by_start[ts(date(2026, 10, 1))]["sum"]
        )
        assert (
            by_start[ts(date(2026, 10, 4))]["sum"]
            > by_start[ts(date(2026, 10, 3))]["sum"]
        )
