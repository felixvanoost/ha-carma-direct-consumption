"""Tests for validating CARMA chart categories and readings."""

import pytest

from custom_components.carma_direct_consumption.parser import (
    ChartParseError,
    UnitInfo,
    parse_chart,
    unit_info,
)


def chart_html(categories: str, values: str) -> str:
    """Build a minimal Highcharts chart snippet for parser tests."""

    return (
        "<script>chart = new Highcharts.Chart({"
        "title: { text: 'Daily Consumption During October 2026 for Test' },"
        f"xAxis: {{ categories: [{categories}] }},"
        "yAxis: { title: { text: 'kWh' } },"
        f"series: [{{ name: 'Daily Consumption', data: [{values}] }}]"
        "});</script>"
    )


def test_parse_chart_validates_aligned_values_and_preserves_nulls():
    """Keep valid values paired with their categories and ignore nulls."""

    chart = parse_chart(
        chart_html(
            "'01/Oct/2026','02/Oct/2026',''",
            "{y: 1.25, color: '#99B7DB'},null,0",
        )
    )

    assert chart is not None
    assert chart.rows == [(chart.month, 1.25)]


def test_parse_chart_accepts_empty_categories_and_values():
    """Return an empty chart instead of treating an empty series as malformed."""

    chart = parse_chart(chart_html("", ""))

    assert chart is not None
    assert not chart.rows


def test_parse_chart_rejects_out_of_range_meter_reading():
    """Reject a non-finite meter reading."""

    html = (
        "<script>chart = new Highcharts.Chart({"
        "title: { text: 'Daily Consumption During October 2026 for Test' },"
        "subtitle: { text: 'Reading as of 21 October 2026 is "
        f"{'9' * 400}"
        "' },"
        "xAxis: { categories: [] },"
        "yAxis: { title: { text: 'kWh' } },"
        "series: [{ name: 'Daily Consumption', data: [] }]"
        "});</script>"
    )
    with pytest.raises(ChartParseError, match="Meter reading is out of range"):
        parse_chart(html)


def test_parse_chart_handles_escaped_text_and_comma_reading():
    """Unescape chart text and parse readings with thousands separators."""

    html = (
        "<script>chart = new Highcharts.Chart({"
        "title: { text: 'Daily Consumption During October 2026 for Test\\'s Meter' },"
        "subtitle: { text: 'Reading as of 21 October 2026 is 1,234.50' },"
        "xAxis: { categories: ['01/Oct/2026','02/Oct/2026'] },"
        "yAxis: { title: { text: 'kW\\\\h' } },"
        "series: [{ name: 'Daily Consumption', data: [1.25,2.50] }]"
        "});</script>"
    )

    chart = parse_chart(html)

    assert chart is not None
    assert chart.title.endswith("Test's Meter")
    assert chart.unit_raw == "kW\\h"
    assert chart.reading == 1234.5
    assert chart.reading_date.isoformat() == "2026-10-21"


@pytest.mark.parametrize(
    ("subtitle", "error"),
    [
        ("Reading as of 32 October 2026 is 100", "Invalid reading date"),
        ("Reading as of 21 Smember 2026 is 100", "Invalid reading date"),
    ],
)
def test_parse_chart_rejects_invalid_reading_dates(subtitle, error):
    """Reject invalid reading dates instead of returning a partial chart."""

    html = (
        "<script>chart = new Highcharts.Chart({"
        "title: { text: 'Daily Consumption During October 2026 for Test' },"
        f"subtitle: {{ text: '{subtitle}' }},"
        "xAxis: { categories: ['01/Oct/2026'] },"
        "yAxis: { title: { text: 'kWh' } },"
        "series: [{ name: 'Daily Consumption', data: [1.25] }]"
        "});</script>"
    )

    with pytest.raises(ChartParseError, match=error):
        parse_chart(html)


@pytest.mark.parametrize(
    ("categories", "values", "error"),
    [
        ("'01/Oct/2026','02/Oct/2026'", "1.0", "categories but 1 daily values"),
        ("'01/Oct/2026'", "1.0,2.0", "categories but 2 daily values"),
        ("'not-a-date'", "1.0", "Invalid chart category"),
        ("'01/Oct/2026'", "NaN", "Invalid daily consumption value"),
        ("'01/Oct/2026'", "-1.0", "out of range"),
        ("'01/Oct/2026'", "1.0,broken", "Invalid daily consumption value"),
    ],
)
def test_parse_chart_rejects_malformed_data(categories, values, error):
    """Reject malformed chart categories and readings."""

    with pytest.raises(ChartParseError, match=error):
        parse_chart(chart_html(categories, values))


@pytest.mark.parametrize(
    ("unit_raw", "expected"),
    [
        ("m^3-wtr", UnitInfo("m³", "volume", "water")),
        ("m³", UnitInfo("m³", "volume", "water")),
        ("kWh", UnitInfo("kWh", "energy", "electricity")),
        ("L", UnitInfo("L", "volume", "water")),
        ("litres", UnitInfo("L", "volume", "water")),
    ],
)
def test_unit_info_normalizes_metric_units(unit_raw, expected):
    """Normalize supported metric units without adding imperial units."""

    assert unit_info(unit_raw) == expected
