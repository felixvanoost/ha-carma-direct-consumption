"""Test client to be used against the mock portal."""

from datetime import date, datetime, timezone

import aiohttp
import pytest

from custom_components.carma_smart_metering import api, const
from custom_components.carma_smart_metering.api import (
    CarmaAuthError,
    CarmaClient,
    CarmaConnectionError,
    CarmaParseError,
)

from .mock_carma import ACCOUNT, ADDRESS, METERS


@pytest.fixture
# pylint: disable-next=redefined-outer-name, unused-argument
async def session(portal):
    """Create an HTTP session with a cookie jar for the mock portal."""

    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as s:
        yield s


# pylint: disable-next=redefined-outer-name, unused-argument
def client(session, portal, account=ACCOUNT):
    """Create a client pointed at the local mock portal."""

    return CarmaClient(session, ADDRESS, account, base_url=const.BASE_URL)


@pytest.mark.parametrize("current_works", [True, False])
# pylint: disable-next=redefined-outer-name
async def test_login_and_all_meters(session, portal, current_works):
    """Fetch all meters and verify their readings."""

    portal.current_works = current_works
    meters = await client(session, portal).async_fetch(12)
    assert [m.meter_id for m in meters] == [m[0] for m in METERS]
    assert [m.unit_raw for m in meters] == ["m^3-wtr", "m^3-wtr", "kWh"]
    for idx, m in enumerate(meters):
        assert [d for d, _ in m.rows] == portal.all_days()
        assert [v for _, v in m.rows] == [
            portal.value(idx, d) for d in portal.all_days()
        ]
        assert m.reading == portal.reading(idx)
    assert not portal.violations


# pylint: disable-next=redefined-outer-name
async def test_months_limit_and_only(session, portal):
    """Restrict a fetch to the requested meter and months."""

    meters = await client(session, portal).async_fetch(
        {"21167069": 2}, only={"21167069"}
    )
    assert [m.meter_id for m in meters] == ["21167069"]
    assert meters[0].rows[0][0].isoformat() == "2026-09-01"
    assert meters[0].rows[-1][0] == portal.through


# pylint: disable-next=redefined-outer-name
async def test_latest_month_traversal_rejects_no_progress(session, portal):
    """Reject navigation that does not advance toward the latest month."""

    portal.current_works = False
    portal.ignore_next_month = True

    with pytest.raises(CarmaParseError, match="Next Month button did not advance"):
        await client(session, portal).async_fetch(1)


# pylint: disable-next=redefined-outer-name
async def test_latest_month_traversal_rejects_missing_reading_date(session, portal):
    """Reject charts that do not report the latest reading date."""

    portal.omit_reading_date = True

    with pytest.raises(CarmaParseError, match="no reading date"):
        await client(session, portal).async_fetch(1)


# pylint: disable-next=redefined-outer-name
async def test_latest_month_traversal_rejects_step_limit(session, portal, monkeypatch):
    """Reject navigation that reaches its configured step limit."""

    portal.current_works = False
    monkeypatch.setattr(api, "MAX_FORWARD_STEPS", 0)

    with pytest.raises(CarmaParseError, match="after 0 month steps"):
        await client(session, portal).async_fetch(1)


# pylint: disable-next=redefined-outer-name
async def test_trailing_zero_readings_preserve_published_zero_days(
    # pylint: disable-next=redefined-outer-name
    session,
    portal,
    monkeypatch,
):
    """Preserve genuine zero readings while trimming future padded values."""

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


# pylint: disable-next=redefined-outer-name
async def test_bad_login(session, portal):
    """Raise an authentication error when portal credentials are rejected."""

    with pytest.raises(CarmaAuthError):
        await client(session, portal, account="wrong").async_fetch(1)


# pylint: disable-next=redefined-outer-name
async def test_server_errors_before_moving_are_retried(session, portal):
    """Retry server errors that occur before month navigation."""

    portal.fail_prev_posts = 2
    meters = await client(session, portal).async_fetch(12)
    for m in meters:
        assert [d for d, _ in m.rows] == portal.all_days()


# pylint: disable-next=redefined-outer-name
async def test_server_error_after_moving_does_not_skip_a_month(session, portal):
    """Recover when a server error occurs after navigation already moved."""

    portal.fail_after_moving = 2
    meters = await client(session, portal).async_fetch(12)
    for m in meters:
        assert [d for d, _ in m.rows] == portal.all_days()
    for idx, m in enumerate(meters):
        assert [v for _, v in m.rows] == [
            portal.value(idx, d) for d in portal.all_days()
        ]


# pylint: disable-next=redefined-outer-name
async def test_silent_tab_switch_failure_is_refused(session, portal):
    """Reject a meter tab switch that the portal silently ignores."""

    portal.ignore_tab_switch = True
    with pytest.raises(CarmaParseError, match="did not work"):
        await client(session, portal).async_fetch(2)


def test_missing_chart_is_a_parse_error():
    """Convert a missing chart into a CARMA parse error."""

    with pytest.raises(CarmaParseError, match="No chart found"):
        # pylint: disable-next=protected-access
        api._parse_chart("<html></html>")


# pylint: disable-next=redefined-outer-name
async def test_persistent_server_errors_give_up(session, portal):
    """Stop retrying after the portal's retry attempts are exhausted."""

    portal.fail_prev_posts = 3
    with pytest.raises(CarmaConnectionError, match="500"):
        await client(session, portal).async_fetch(12)


@pytest.mark.parametrize("failure", [aiohttp.ClientConnectionError, TimeoutError])
async def test_transient_network_error_is_retried(
    # pylint: disable-next=redefined-outer-name
    session,
    portal,
    monkeypatch,
    failure,
):
    """Retry a temporary connection error or timeout and continue signing in."""

    monkeypatch.setattr(api, "REQUEST_RETRY_DELAYS", (0, 0))
    request = session.request
    login_gets = 0

    def fail_first_login_get(method, url, **kwargs):
        nonlocal login_gets
        if method == "GET" and url.endswith("login.aspx"):
            login_gets += 1
            if login_gets == 1:
                raise failure("temporary network failure")
        return request(method, url, **kwargs)

    monkeypatch.setattr(session, "request", fail_first_login_get)
    await client(session, portal).async_login()

    assert login_gets == 2


@pytest.mark.parametrize("failure", [aiohttp.ClientConnectionError, TimeoutError])
async def test_connection_errors_give_up_after_bounded_retries(
    # pylint: disable-next=redefined-outer-name
    session,
    portal,
    monkeypatch,
    failure,
):
    """Stop retrying after the retry limit is exceeded."""

    monkeypatch.setattr(api, "REQUEST_RETRY_DELAYS", (0, 0))
    login_gets = 0

    def fail_login_get(method, url, **_kwargs):
        nonlocal login_gets
        if method == "GET" and url.endswith("login.aspx"):
            login_gets += 1
            raise failure("portal unavailable")
        raise AssertionError("unexpected request after failed login")

    monkeypatch.setattr(session, "request", fail_login_get)
    with pytest.raises(CarmaConnectionError, match="Cannot reach the CARMA portal"):
        await client(session, portal).async_login()

    assert login_gets == 3
