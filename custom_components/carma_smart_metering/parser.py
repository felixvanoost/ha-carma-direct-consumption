"""HTML parsing for the CARMA Direct Consumption portal."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _month_number(name: str) -> int | None:
    return _MONTHS.get(name[:3].lower())


# ASP.NET WebForms form handling.
@dataclass
class ParsedForm:
    """The first <form> on a page."""

    action: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    buttons: dict[str, str] = field(default_factory=dict)
    text_inputs: list[str] = field(default_factory=list)


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.form = ParsedForm()
        self._seen_form = False
        self._in_form = False
        self._sel_name: str | None = None
        self._sel_default: str | None = None
        self._sel_first: str | None = None

    def handle_starttag(self, tag, attrs):
        a = {k: (v if v is not None else "") for k, v in attrs}
        if tag == "form" and not self._seen_form:
            self._seen_form = self._in_form = True
            self.form.action = a.get("action", "")
        elif not self._in_form:
            return
        elif tag == "input":
            self._input(a)
        elif tag == "select":
            self._sel_name, self._sel_default, self._sel_first = a.get("name"), None, None
        elif tag == "option" and self._sel_name:
            value = a.get("value", "")
            if self._sel_first is None:
                self._sel_first = value
            if "selected" in a and self._sel_default is None:
                self._sel_default = value

    def handle_endtag(self, tag):
        if tag == "select" and self._sel_name:
            self.form.fields[self._sel_name] = (
                self._sel_default if self._sel_default is not None else (self._sel_first or ""))
            self._sel_name = None
        elif tag == "form":
            self._in_form = False

    def _input(self, a: dict[str, str]) -> None:
        name = a.get("name")
        kind = a.get("type", "text").lower()
        if not name or kind in ("reset", "file"):
            return
        if kind in ("submit", "button", "image"):
            self.form.buttons[name] = a.get("value", "")
        elif kind in ("checkbox", "radio"):
            if "checked" in a:
                self.form.fields[name] = a.get("value", "on")
        else:
            self.form.fields[name] = a.get("value", "")
            if kind in ("text", "password", "number", "email", "tel"):
                self.form.text_inputs.append(name)


def parse_form(html: str) -> ParsedForm:
    parser = _FormParser()
    parser.feed(html)
    return parser.form


def parse_tabs(html: str) -> list[tuple[int, str]]:
    """[(tab index, meter number)] from the tab headers."""

    found = re.findall(r'id="__tab_tabMeters_tabMeter(\d+)"[^>]*>\s*<span>([^<]*)</span>', html)
    return sorted((int(i), name.strip()) for i, name in found)


def active_tab(form: ParsedForm) -> int:
    try:
        return int(json.loads(form.fields["tabMeters_ClientState"]).get("ActiveTabIndex", 0))
    except (KeyError, ValueError, TypeError):
        return 0


@dataclass
class Chart:
    title: str
    month: date
    unit_raw: str
    rows: list[tuple[date, float]]
    reading: float | None = None
    reading_date: date | None = None


class ChartParseError(ValueError):
    """The chart data is present but malformed."""


def parse_chart(html: str) -> Chart | None:
    """Parse the month chart (daily consumption) embedded in the page."""

    m = re.search(r"new Highcharts\.Chart\(\{(.*?)\}\);?\s*</script>", html, re.S)
    if not m:
        return None
    cfg = m.group(1)

    title = _first(r"title:\s*\{\s*text:\s*'([^']*)'", cfg)
    if not title:
        raise ChartParseError("Chart title is missing")
    mm = re.search(r"During\s+([A-Za-z]+)\s+(\d{4})", title)
    if not mm or not (n := _month_number(mm.group(1))):
        raise ChartParseError("Chart title has no valid month")
    try:
        month = date(int(mm.group(2)), n, 1)
    except ValueError as err:
        raise ChartParseError("Chart title has an invalid year") from err

    unit_raw = _first(r"yAxis:\s*\{\s*title:\s*\{\s*text:\s*'([^']*)'", cfg)
    if not unit_raw:
        raise ChartParseError("Chart unit is missing")

    reading = reading_date = None
    rm = re.search(r"Reading as of\s+(?:[A-Za-z]+,\s*)?(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s+is\s+(-?[\d,]*\.?\d+)",
                   _first(r"subtitle:\s*\{\s*text:\s*'([^']*)'", cfg))
    if rm:
        reading = float(rm.group(4).replace(",", ""))
        n = _month_number(rm.group(2))
        if not n:
            raise ChartParseError("Invalid reading date")
        try:
            reading_date = date(int(rm.group(3)), n, int(rm.group(1)))
        except ValueError as err:
            raise ChartParseError("Invalid reading date") from err

    categories = re.search(r"categories:\s*\[(.*?)\]", cfg, re.S)
    if not categories or not re.fullmatch(
        r"\s*(?:'[^']*'\s*(?:,\s*'[^']*'\s*)*)?\s*", categories.group(1)
    ):
        raise ChartParseError("Chart categories are missing or malformed")
    cats = re.findall(r"'([^']*)'", categories.group(1))

    # First series = 'Daily Consumption'.
    # Values are plain numbers or {y: n, color: ...} for weekend shading.
    series = cfg.split("series:", 1)[1] if "series:" in cfg else ""
    data = re.search(r"data:\s*\[(.*?)\]", series, re.S)
    if not data:
        raise ChartParseError("Daily consumption data is missing or malformed")
    values = [_parse_chart_value(item) for item in _split_array(data.group(1))]
    parsed_categories: list[date | None] = []
    seen_days: set[date] = set()
    for index, cat in enumerate(cats):
        if not cat:
            if index != len(cats) - 1:
                raise ChartParseError("Chart contains an invalid blank category")
            parsed_categories.append(None)
            continue
        day = _parse_category(cat)
        if day is None:
            raise ChartParseError(f"Invalid chart category: {cat!r}")
        if day in seen_days:
            raise ChartParseError(f"Duplicate chart category: {cat!r}")
        seen_days.add(day)
        parsed_categories.append(day)

    if len(values) > len(cats):
        raise ChartParseError(
            f"Chart has {len(cats)} categories but {len(values)} daily values"
        )
    for index in range(len(values), len(cats)):
        day = parsed_categories[index]
        if day is not None and (reading_date is None or day <= reading_date):
            raise ChartParseError(
                f"Chart has {len(cats)} categories but {len(values)} daily values"
            )

    rows: list[tuple[date, float]] = []
    for day, value in zip(parsed_categories, values):
        if day is None:
            if value not in (None, 0.0):
                raise ChartParseError("Chart contains an invalid blank category")
            continue
        if value is None:
            continue
        if month is not None and (day.year, day.month) != (month.year, month.month):
            continue
        rows.append((day, value))
    return Chart(title, month, unit_raw, rows, reading, reading_date)


def _split_array(value: str) -> list[str]:
    """Split a JavaScript array body at top-level commas."""

    items: list[str] = []
    start = depth = 0
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in ("'", '"'):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                raise ChartParseError("Daily consumption data has unbalanced braces")
        elif char == "," and depth == 0:
            items.append(value[start:index].strip())
            start = index + 1
    if quote or depth:
        raise ChartParseError("Daily consumption data has unbalanced delimiters")
    items.append(value[start:].strip())
    return items


def _parse_chart_value(item: str) -> float | None:
    number = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
    if item == "null":
        return None
    match = re.fullmatch(rf"({number})", item)
    if not match:
        match = re.fullmatch(rf"\{{\s*y\s*:\s*({number})(?:\s*,[^{{}}]*)?\s*\}}", item, re.S)
    if not match:
        raise ChartParseError(f"Invalid daily consumption value: {item!r}")
    value = float(match.group(1) if item.startswith("{") else item)
    if not math.isfinite(value) or value < 0:
        raise ChartParseError(f"Daily consumption value is out of range: {item!r}")
    return value


def _first(pattern: str, text: str) -> str:
    m = re.search(pattern, text)
    return m.group(1) if m else ""


def _parse_category(cat: str) -> date | None:
    m = re.fullmatch(r"(\d{1,2})/([A-Za-z]{3,})/(\d{4})", cat.strip())
    if not m or not (n := _month_number(m.group(2))):
        return None
    try:
        return date(int(m.group(3)), n, int(m.group(1)))
    except ValueError:
        return None


def unit_info(unit_raw: str) -> tuple[str, str | None, str]:
    """Normalize a portal axis label to a Home Assistant unit, unit class, and reading type."""

    u = unit_raw.lower()
    if re.search(r"kwh", u):
        return "kWh", "energy", "electricity"
    if re.search(r"m\^?3|m³|wtr|water", u):
        return "m³", "volume", "water"
    return unit_raw, None, "other"
