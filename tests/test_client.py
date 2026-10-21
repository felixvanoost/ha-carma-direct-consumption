"""Test client to be used against the mock portal."""

from datetime import date, datetime, timezone

import aiohttp
import pytest

from custom_components.carma_smart_metering import api
from custom_components.carma_smart_metering.api import CarmaAuthError, CarmaClient, CarmaConnectionError, CarmaParseError

from .mock_carma import ACCOUNT, ADDRESS, METERS


@pytest.fixture
async def session(portal):
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as s:
        yield s


def client(session, portal, account=ACCOUNT):
    from custom_components.carma_smart_metering import const
    return CarmaClient(session, ADDRESS, account, base_url=const.BASE_URL)


@pytest.mark.parametrize("current_works", [True, False])
async def test_login_and_all_meters(session, portal, current_works):
    portal.current_works = current_works
    meters = await client(session, portal).async_fetch(12)
    assert [m.meter_id for m in meters] == [m[0] for m in METERS]
    assert [m.unit_raw for m in meters] == ["m^3-wtr", "m^3-wtr", "kWh"]
    for idx, m in enumerate(meters):
        assert [d for d, _ in m.rows] == portal.all_days()
        assert [v for _, v in m.rows] == [portal.value(idx, d) for d in portal.all_days()]
        assert m.reading == portal.reading(idx)
    assert not portal.violations


async def test_months_limit_and_only(session, portal):
    meters = await client(session, portal).async_fetch({"21167069": 2}, only={"21167069"})
    assert [m.meter_id for m in meters] == ["21167069"]
    assert meters[0].rows[0][0].isoformat() == "2026-09-01"
    assert meters[0].rows[-1][0] == portal.through


async def test_trailing_zero_readings_preserve_published_zero_days(
    session, portal, monkeypatch
):
    portal.through = date(2026, 10, 18)
    portal.zero_days.update({date(2026, 10, 17), portal.through})
    portal.pad_zeros = True
    monkeypatch.setattr(
        api.dt_util,
        "now",
        lambda: datetime(2026, 10, 21, tzinfo=timezone.utc),
    )

    meters = await client(session, portal).async_fetch(
        {"21167069": 2}, only={"21167069"}
    )

    rows = meters[0].rows
    assert rows[-2:] == [(date(2026, 10, 17), 0.0), (portal.through, 0.0)]
    assert rows[-1][0] == portal.through


async def test_bad_login(session, portal):
    with pytest.raises(CarmaAuthError):
        await client(session, portal, account="wrong").async_fetch(1)


async def test_server_errors_before_moving_are_retried(session, portal):
    portal.fail_prev_posts = 2
    meters = await client(session, portal).async_fetch(12)
    for idx, m in enumerate(meters):
        assert [d for d, _ in m.rows] == portal.all_days()


async def test_server_error_after_moving_does_not_skip_a_month(session, portal):
    portal.fail_after_moving = 2
    meters = await client(session, portal).async_fetch(12)
    for idx, m in enumerate(meters):
        assert [d for d, _ in m.rows] == portal.all_days()
        assert [v for _, v in m.rows] == [portal.value(idx, d) for d in portal.all_days()]


async def test_silent_tab_switch_failure_is_refused(session, portal):
    portal.ignore_tab_switch = True
    with pytest.raises(CarmaParseError, match="did not work"):
        await client(session, portal).async_fetch(2)


def test_missing_chart_is_a_parse_error():
    with pytest.raises(CarmaParseError, match="No chart found"):
        api._parse_chart("<html></html>")


async def test_persistent_server_errors_give_up(session, portal):
    portal.fail_prev_posts = 3
    with pytest.raises(CarmaConnectionError, match="500"):
        await client(session, portal).async_fetch(12)
