"""Tests for validating CARMA chart categories and readings."""

import pytest

from custom_components.carma_smart_metering.parser import ChartParseError, parse_chart


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
