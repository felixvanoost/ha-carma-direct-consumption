"""Async client for the CARMA Direct Consumption portal."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import urljoin

import aiohttp
from homeassistant.util import dt as dt_util

from . import const
from .parser import (
    Chart,
    ChartParseError,
    active_tab,
    parse_chart,
    parse_form,
    parse_tabs,
)

_LOGGER = logging.getLogger(__name__)
TIMEOUT = aiohttp.ClientTimeout(total=60)

# The CARMA portal is quite old and fragile, so we need to pace our requests.
REQUEST_DELAY = 1.0  # Seconds between requests.
REQUEST_RETRY_DELAYS = (2.0, 5.0)
MAX_EMPTY_MONTHS = 3  # Stop walking back after this many months in a row without data.
MAX_FORWARD_STEPS = 12  # Walk forward at most this far to get to the newest month.


class CarmaError(Exception):
    """Base error."""


class CarmaAuthError(CarmaError):
    """Login was rejected."""


class CarmaConnectionError(CarmaError):
    """Could not reach the portal, or it answered with an error."""


class CarmaParseError(CarmaError):
    """The portal's pages could not be parsed."""


def _parse_chart(html: str) -> Chart:
    try:
        chart = parse_chart(html)
    except ChartParseError as err:
        raise CarmaParseError(str(err)) from err
    if chart is None:
        raise CarmaParseError("No chart found on the portal page")
    return chart


@dataclass
# pylint: disable-next=too-many-instance-attributes
class MeterData:
    """Readings for one meter."""

    meter_id: str
    title: str
    unit_raw: str
    rows: list[tuple[date, float]] = field(default_factory=list)
    reading: float | None = None
    reading_date: date | None = None
    months: list[date] = field(default_factory=list)
    stop_reason: str = ""


def create_client(hass, address: str, account: str) -> "CarmaClient":
    """A client with its own cookie jar. The portal's login lives in a session cookie."""

    # pylint: disable-next=import-outside-toplevel
    from homeassistant.helpers.aiohttp_client import (
        async_create_clientsession,
    )

    session = async_create_clientsession(
        hass, cookie_jar=aiohttp.CookieJar(unsafe=True)
    )
    return CarmaClient(session, address, account)


def _prev_month(d: date) -> date:
    return date(d.year - 1, 12, 1) if d.month == 1 else date(d.year, d.month - 1, 1)


class CarmaClient:
    """Logs in and walks the 'Graphing' page (meter tabs x months)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        address: str,
        account: str,
        base_url: str | None = None,
    ) -> None:
        self._session = session
        self._credentials = (address, account)
        base = base_url or const.BASE_URL
        self._login_url = urljoin(base, "login.aspx")
        self._graph_url = urljoin(base, "graphing.aspx")
        self._made_request = False
        self._last_url = ""

    async def _request(
        self, method: str, url: str, data: dict[str, str] | None = None
    ) -> tuple[str, str]:
        if self._made_request:
            await asyncio.sleep(REQUEST_DELAY)
        self._made_request = True
        for attempt, delay in enumerate((*REQUEST_RETRY_DELAYS, None)):
            try:
                async with self._session.request(
                    method, url, data=data, timeout=TIMEOUT
                ) as resp:
                    if resp.status < 500 or delay is None:
                        resp.raise_for_status()
                        self._last_url = str(resp.url)
                        return self._last_url, await resp.text(errors="replace")
            except aiohttp.ClientResponseError as err:
                raise CarmaConnectionError(
                    f"HTTP {err.status} from the CARMA portal"
                ) from err
            except (aiohttp.ClientError, TimeoutError) as err:
                if attempt >= len(REQUEST_RETRY_DELAYS):
                    raise CarmaConnectionError(
                        f"Cannot reach the CARMA portal: {err}"
                    ) from err
                connection_delay = REQUEST_RETRY_DELAYS[attempt]
                _LOGGER.debug(
                    "Connection to the CARMA portal failed. Retrying in %.0f seconds.",
                    connection_delay,
                )
                await asyncio.sleep(connection_delay)
                continue
            _LOGGER.debug(
                "CARMA portal returned a server error. Retrying in %.0f seconds.", delay
            )
            await asyncio.sleep(delay)
        raise CarmaConnectionError("unreachable")

    async def _post_form(
        self,
        page_url: str,
        html: str,
        overrides: dict[str, str] | None = None,
        event_target: str | None = None,
        button: str | None = None,
    ) -> str:
        """Re-submit the page's form the way a browser would."""

        form = parse_form(html)
        if not form.fields:
            raise CarmaParseError("Page has no form to submit")
        fields = dict(form.fields)
        fields.update(overrides or {})
        if event_target:
            fields["__EVENTTARGET"] = event_target
            fields.setdefault("__EVENTARGUMENT", "")
        if button:
            fields[button] = form.buttons.get(button, "")
        _, text = await self._request("POST", urljoin(page_url, form.action), fields)
        return text

    async def async_login(self) -> str:
        """Sign into the portal and return the 'Graphing' page HTML."""

        self._session.cookie_jar.clear()
        _, html = await self._request("GET", self._login_url)
        form = parse_form(html)
        names = form.text_inputs
        address, account = self._credentials
        if len(names) < 2:
            raise CarmaParseError("Could not find the sign-in fields")
        addr_field = "username_txt" if "username_txt" in names else names[0]
        acct_field = (
            "password_txt"
            if "password_txt" in names
            else next(n for n in names if n != addr_field)
        )
        button = next(
            (
                n
                for n, v in form.buttons.items()
                if re.search(r"sign\s*in|log\s*in|submit|enter", f"{n} {v}", re.I)
                and not re.search(r"out", f"{n} {v}", re.I)
            ),
            next(iter(form.buttons), None),
        )

        html = await self._post_form(
            self._login_url,
            html,
            {addr_field: address, acct_field: account},
            button=button,
        )
        signed_in = "logout_btn" in html or "graphing.aspx" in self._last_url.lower()
        if not signed_in:
            _, html = await self._request("GET", self._graph_url)
            signed_in = "logout_btn" in html
        if not signed_in:
            raise CarmaAuthError(
                "The portal did not accept the service address or account number"
            )
        if "tabMeters" not in html:
            _, html = await self._request("GET", self._graph_url)
        return html

    async def _select_tab(self, html: str, index: int) -> str:
        if active_tab(parse_form(html)) == index:
            return html
        form = parse_form(html)
        state = json.loads(form.fields["tabMeters_ClientState"])
        state["ActiveTabIndex"] = index
        html = await self._post_form(
            self._graph_url,
            html,
            {
                "tabMeters_ClientState": json.dumps(state, separators=(",", ":")),
                "__EVENTARGUMENT": f"activeTabChanged:{index}",
            },
            event_target="tabMeters",
        )
        if active_tab(parse_form(html)) != index:
            # Raise an error to avoid reading the wrong meter's data.
            raise CarmaParseError(f"Switching to meter tab {index} did not work")
        return html

    async def _press(self, html: str, button: str) -> str:
        if button not in parse_form(html).buttons:
            raise CarmaParseError(f"Button {button} not found on the page")
        return await self._post_form(self._graph_url, html, button=button)

    async def _to_latest_month(self, html: str) -> str:
        """Show the latest chart month containing the reported reading."""

        html = await self._press(html, "currMonth_btn")
        for steps in range(MAX_FORWARD_STEPS + 1):
            chart = _parse_chart(html)
            if not chart.reading_date:
                raise CarmaParseError(
                    "The chart has no reading date to identify the latest month"
                )
            latest_month = chart.reading_date.replace(day=1)
            if chart.month >= latest_month:
                return html
            if steps == MAX_FORWARD_STEPS:
                raise CarmaParseError(
                    f"Could not reach the latest reading month {latest_month:%Y-%m}; "
                    f"stopped at {chart.month:%Y-%m} after {MAX_FORWARD_STEPS} month steps"
                )
            nxt = await self._press(html, "nextMonth_btn")
            moved = _parse_chart(nxt)
            if moved.month <= chart.month:
                raise CarmaParseError(
                    f"CARMA's Next Month button did not advance beyond {chart.month:%Y-%m}"
                )
            html = nxt
        raise CarmaParseError(
            f"Could not reach the latest reading month after {MAX_FORWARD_STEPS} month steps"
        )

    async def _read_meter(
        self, html: str, number: str, depth: int
    ) -> tuple[MeterData, str]:
        """Walk back `depth` months from the newest one."""

        meter = MeterData(meter_id=number, title="", unit_raw="")
        rows: dict[date, float] = {}
        seen: set[date] = set()
        empty_run = 0
        stop = f"reached the requested {depth} month(s)"
        for step in range(max(depth, 1)):
            chart = _parse_chart(html)
            month = chart.month
            if month in seen:
                stop = "Prev Month stopped moving"
                break
            if not chart.rows:
                empty_run += 1
                if empty_run >= MAX_EMPTY_MONTHS:
                    stop = f"{MAX_EMPTY_MONTHS} months in a row with no data"
                    break
            else:
                empty_run = 0
            seen.add(month)
            rows.update(chart.rows)
            if not meter.unit_raw:
                meter.title, meter.unit_raw = chart.title, chart.unit_raw
            if meter.reading is None:
                meter.reading, meter.reading_date = chart.reading, chart.reading_date
            if step < depth - 1:
                html = await self._previous_month(html, month)
        if not seen:
            raise CarmaParseError(f"No chart found for meter {number}")
        meter.rows = sorted(rows.items())

        self._discard_future_zeroes(meter)
        meter.months = sorted(seen)
        meter.stop_reason = stop
        return meter, html

    async def _previous_month(self, html: str, month: date) -> str:
        """Move to the previous month and correct portal navigation overshoots."""

        html = await self._press(html, "prevMonth_btn")
        for _ in range(2):
            chart = _parse_chart(html)
            if chart.month >= _prev_month(month):
                break
            html = await self._press(html, "nextMonth_btn")
        return html

    @staticmethod
    def _discard_future_zeroes(meter: MeterData) -> None:
        """Remove trailing zero placeholders after the newest published day.

        CARMA derives daily usage from the following day's reading, so the newest
        published day is the day before the "Reading as of" date. The chart pads
        the rest of the month with 0, including the reading date.
        """

        today = dt_util.now().date()
        reading_day = min(today, meter.reading_date) if meter.reading_date else today
        newest_published_day = reading_day - timedelta(days=1)
        while (
            meter.rows
            and meter.rows[-1][0] > newest_published_day
            and meter.rows[-1][1] == 0
        ):
            meter.rows.pop()

    async def async_fetch(
        self, months: int | dict[str, int], only: set[str] | None = None
    ) -> list[MeterData]:
        """Read every meter for the requested number of months."""

        html = await self.async_login()
        tabs = parse_tabs(html)
        if not tabs:
            raise CarmaParseError("No meter tabs found on the 'Graphing' page")

        result: list[MeterData] = []
        for index, number in tabs:
            if only is not None and number not in only:
                continue
            depth = (
                months
                if isinstance(months, int)
                else months.get(number, const.REFRESH_MONTHS)
            )
            html = await self._select_tab(html, index)
            html = await self._to_latest_month(html)
            meter, html = await self._read_meter(html, number, depth)
            result.append(meter)

        return result

    async def async_discover(self) -> list[MeterData]:
        """Meters on the account."""

        return await self.async_fetch(1)
