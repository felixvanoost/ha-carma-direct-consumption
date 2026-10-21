"""A mock CARMA portal."""

from __future__ import annotations

import calendar
import json
import secrets
from datetime import date, timedelta
from html import escape

from aiohttp import web

MON = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]
LONG = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

ADDRESS, ACCOUNT = "1 TEST ST 101", "12345"
METERS = [
    ("21145135", "m^3-wtr", 0.05),
    ("21167069", "m^3-wtr", 0.02),
    ("M0129495", "kWh", 8.0),
]
TODAY = date(2026, 10, 19)
EARLIEST = date(2026, 6, 1)
VIEWSTATE, EVENTVALIDATION = "/wEPDwUKtest", "/wEdAAtest"


def day_value(idx: int, d: date) -> float:
    """Return the mock reading for a meter and date."""

    base = METERS[idx][2]
    return round(base * (1 + (d.day % 7) / 10), 4)


def prev_month(d: date) -> date:
    """Return the first day of the month before the supplied date."""

    return date(d.year - 1, 12, 1) if d.month == 1 else date(d.year, d.month - 1, 1)


def next_month(d: date) -> date:
    """Return the first day of the month after the supplied date."""

    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


# pylint: disable-next=too-many-instance-attributes
class MockCarma:
    """Stateful mock portal used by client and integration tests."""

    def __init__(self) -> None:
        """Initialise mock portal state and failure controls."""

        self.through = date(2026, 10, 18)
        self.sessions: dict[str, dict] = {}
        self.logins = 0
        self.violations: list[str] = []  # Protocol mistakes by the client.
        self.zero_days: set[date] = set()  # Days with zero usage.
        self.pad_zeros = True  # Pad unpublished days with 0.
        self.current_works = True  # Does 'Current Month' jump to the newest month?
        self.ignore_next_month = (
            False  # Next Month can fail without changing the chart.
        )
        # Reading date minus the newest published day. The live portal uses 1.
        self.reading_lag_days = 1
        self.omit_reading_date = False  # Chart can omit the reading date.
        self.fail_prev_posts = (
            0  # Next N 'Prev Month' posts answer HTTP 500 without moving.
        )
        self.fail_after_moving = (
            0  # Next N 'Prev Month' posts move then answer HTTP 500.
        )
        self.ignore_tab_switch = False
        self.revisions: dict[tuple[int, date], float] = {}

    def value(self, idx: int, d: date) -> float:
        """Return a reading including configured zeroes and revisions."""

        if d in self.zero_days:
            return 0.0
        return round(day_value(idx, d) + self.revisions.get((idx, d), 0.0), 4)

    def all_days(self) -> list[date]:
        """Return all mock dates with available readings."""

        n = (self.through - EARLIEST).days + 1
        return [date.fromordinal(EARLIEST.toordinal() + i) for i in range(n)]

    def total(self, idx: int) -> float:
        """Return the cumulative usage for a meter."""

        return round(sum(self.value(idx, d) for d in self.all_days()), 6)

    def reading(self, idx: int) -> float:
        """Return the mock meter register reading."""

        return round(100 + idx + self.total(idx), 2)

    def _login_page(self) -> str:
        """Render the mock portal login form."""

        return f"""<html><body><form method="post" action="./login.aspx" id="Form1">
<input type="hidden" name="__VIEWSTATE" value="{VIEWSTATE}" />
<input type="hidden" name="__EVENTVALIDATION" value="{EVENTVALIDATION}" />
Please Sign In
<input name="username_txt" type="text" value="" />
<input name="password_txt" type="password" value="" />
<input type="checkbox" name="chkRemember" />
<input type="submit" name="login_btn" value="Login" />
</form></body></html>"""

    def _chart(self, idx: int, month: date) -> str:
        """Render the chart for a meter and month."""

        _, unit, _ = METERS[idx]
        days = calendar.monthrange(month.year, month.month)[1]
        cats = [date(month.year, month.month, d) for d in range(1, days + 1)]
        nxt = next_month(month)
        tail = f"'01/{MON[nxt.month - 1]}/{nxt.year}'" if self.pad_zeros else "''"
        cat_s = (
            ",".join(f"'{c.day:02d}/{MON[c.month - 1]}/{c.year}'" for c in cats)
            + ","
            + tail
        )
        vals = []
        for c in cats:
            if c <= self.through or self.omit_reading_date:
                v = self.value(idx, c)
                vals.append(
                    f"{{y: {v},color: '#99B7DB'}}" if c.weekday() >= 5 else str(v)
                )
            elif self.pad_zeros:
                vals.append("0.0000")
        if self.pad_zeros:
            vals.append("0.0000")
        t = self.through + timedelta(days=self.reading_lag_days)
        subtitle = ""
        if not self.omit_reading_date:
            subtitle = (
                f"Reading as of {calendar.day_name[t.weekday()]}, {t.day} "
                f"{LONG[t.month - 1]} {t.year} "
                f"is {self.reading(idx):.2f} {unit}"
            )
        return (
            "<script language='javascript'>var chart1;"
            "chart1 = new Highcharts.Chart({"
            " chart: { renderTo: 'chartContainer_0' },"
            f" title: {{ text: 'Daily Consumption During "
            f"{LONG[month.month - 1]} {month.year} for {ADDRESS}' }},"
            f" subtitle: {{ text: '{subtitle}' }},"
            f" xAxis: {{ categories: [ {cat_s} ] }}, yAxis: {{ title: {{ text: '{unit}' }} }},"
            f" series: [{{ type: 'column', name: 'Daily Consumption', data: [{','.join(vals)}] }},"
            f"{{ type: 'line', name: 'Average', data:[0.07,0.07] }}] }});</script>"
        )

    def _graph_page(self, sess: dict) -> str:
        """Render the graph page for the session's selected meter and month."""

        state = {
            "ActiveTabIndex": sess["tab"],
            "TabEnabledState": [True] * 3,
            "TabWasLoadedOnceState": [False] * 3,
        }
        tabs = "".join(
            f'<span><a class="ajax__tab_tab" id="__tab_tabMeters_tabMeter{i}" '
            'href="#" style="text-decoration:none;">'
            f"<span>{m[0]}</span></a></span>"
            for i, m in enumerate(METERS)
        )
        return f"""<html><body><form method="post" action="./graphing.aspx" id="Form1">
<input type="hidden" name="__EVENTTARGET" id="__EVENTTARGET" value="" />
<input type="hidden" name="__EVENTARGUMENT" id="__EVENTARGUMENT" value="" />
<input type="hidden" name="__VIEWSTATE" value="{VIEWSTATE}" />
<input type="hidden" name="__EVENTVALIDATION" value="{EVENTVALIDATION}" />
<input type="hidden" name="tabMeters_ClientState" id="tabMeters_ClientState"
value="{escape(json.dumps(state, separators=(',', ':')))}" />
<input type="submit" name="logout_btn" value="Sign Out" id="logout_btn" />
<div id="tabMeters">{tabs}</div>
<input type="submit" name="prevMonth_btn" value="Prev Month" />
<input type="submit" name="currMonth_btn" value="Current Month" />
<input type="submit" name="nextMonth_btn" value="Next Month" />
<input type="submit" name="changeType_btn" value="By Year" />
{self._chart(sess["tab"], sess["month"])}
</form></body></html>"""

    def make_app(self) -> web.Application:
        """Create the aiohttp app used by portal tests."""

        app = web.Application()
        app.router.add_get("/portal/login.aspx", self.login_get)
        app.router.add_post("/portal/login.aspx", self.login_post)
        app.router.add_get("/portal/graphing.aspx", self.graph_get)
        app.router.add_post("/portal/graphing.aspx", self.graph_post)
        return app

    def _session(self, request: web.Request) -> dict | None:
        """Return the portal session associated with the request."""

        return self.sessions.get(request.cookies.get("sid", ""))

    async def login_get(self, _request):
        """Serve the login form."""

        return web.Response(text=self._login_page(), content_type="text/html")

    async def login_post(self, request):
        """Validate credentials and create an authenticated session."""

        form = await request.post()
        if (
            form.get("__VIEWSTATE") != VIEWSTATE
            or form.get("__EVENTVALIDATION") != EVENTVALIDATION
        ):
            self.violations.append("login: hidden fields not round-tripped")
        if (
            form.get("username_txt") == ADDRESS
            and form.get("password_txt") == ACCOUNT
            and "login_btn" in form
        ):
            sid = secrets.token_hex(8)
            self.sessions[sid] = {
                "tab": 0,
                "month": prev_month(date(TODAY.year, TODAY.month, 1)),
            }  # opens on an older month
            self.logins += 1
            resp = web.HTTPFound("graphing.aspx")
            resp.set_cookie("sid", sid, path="/")
            raise resp
        return web.Response(text=self._login_page(), content_type="text/html")

    async def graph_get(self, request):
        """Serve the graph for the authenticated session."""

        sess = self._session(request)
        if not sess:
            raise web.HTTPFound("login.aspx")
        return web.Response(text=self._graph_page(sess), content_type="text/html")

    async def graph_post(self, request):
        """Handle simulated WebForms postbacks."""

        sess = self._session(request)
        if not sess:
            raise web.HTTPFound("login.aspx")
        form = await request.post()
        if (
            form.get("__VIEWSTATE") != VIEWSTATE
            or form.get("__EVENTVALIDATION") != EVENTVALIDATION
        ):
            self.violations.append("graphing: hidden fields not round-tripped")
        if form.get("__EVENTTARGET") == "tabMeters":
            arg = form.get("__EVENTARGUMENT", "")
            n = int(arg.split(":")[1]) if arg.startswith("activeTabChanged:") else -1
            if (
                n < 0
                or json.loads(form.get("tabMeters_ClientState", "{}")).get(
                    "ActiveTabIndex"
                )
                != n
            ):
                self.violations.append(f"bad tab switch: {arg!r}")
            elif not self.ignore_tab_switch:
                sess["tab"] = n
        if "prevMonth_btn" in form and self.fail_prev_posts > 0:
            self.fail_prev_posts -= 1
            return web.Response(status=500, text="Server Error")
        if "currMonth_btn" in form:
            if self.current_works:
                sess["month"] = date(TODAY.year, TODAY.month, 1)
        elif "prevMonth_btn" in form and prev_month(sess["month"]) >= EARLIEST:
            sess["month"] = prev_month(sess["month"])
            if self.fail_after_moving > 0:
                self.fail_after_moving -= 1
                return web.Response(status=500, text="Server Error")
        elif (
            "nextMonth_btn" in form
            and not self.ignore_next_month
            and next_month(sess["month"]) <= date(TODAY.year, TODAY.month, 1)
        ):
            sess["month"] = next_month(sess["month"])
        return web.Response(text=self._graph_page(sess), content_type="text/html")
