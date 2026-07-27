"""Render tests for the FoxChart family (D2).

Every type is constructed, given data, painted offscreen, resized, and painted
empty — the four things a page phase will do to it. A chart that raises during
paint takes the whole console page down with it, so "does not crash" is the
floor; on top of that we pin the option-surface behaviours the web contract
guarantees (tone mapping, empty state, legend rules, hit-testing, a11y text).

Painting happens onto a QPixmap so the tests need no display; the CI job sets
QT_QPA_PLATFORM=offscreen.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

from charts import CHART, FoxChart, TONES, tone_color
from foxy_tokens import BAD_RED, OK_GREEN, WARN_AMBER, WEB

ALL_TYPES = ["bar", "hbar", "line", "area", "sparkline", "stacked", "donut", "gauge"]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _paint(widget, w=320, h=None) -> QPixmap:
    """Force a real paint pass and hand back the result."""
    widget.resize(w, h or widget.height() or 140)
    pm = QPixmap(widget.size())
    pm.fill()
    widget.render(pm)
    return pm


def _sample(chart_type: str) -> dict:
    """Representative options per type — the shapes the pages will pass."""
    rows = [{"label": "Mon", "value": 4}, {"label": "Tue", "value": 11, "tone": "bad"},
            {"label": "Wed", "value": 7, "sub": "3 breaches"}]
    if chart_type in ("line", "area", "sparkline", "stacked"):
        return {"labels": ["1", "2", "3", "4"],
                "series": [{"name": "logs", "values": [2, 8, 5, 9]},
                           {"name": "breaches", "tone": "bad", "values": [0, 2, 1, 3]}],
                "legend": True}
    if chart_type == "donut":
        return {"data": [{"label": "graded", "value": 30, "tone": "ok"},
                         {"label": "pending", "value": 6, "tone": "warn"}],
                "center": "36"}
    if chart_type == "gauge":
        return {"value": 62, "max": 100, "label": "62 of 100 credits"}
    return {"data": rows}


# ── construction + paint ────────────────────────────────────────────────────
@pytest.mark.parametrize("chart_type", ALL_TYPES)
def test_every_type_constructs_and_paints(app, chart_type):
    c = FoxChart(chart_type, **_sample(chart_type))
    assert c.type == chart_type
    assert c.height() > 0
    _paint(c)


@pytest.mark.parametrize("chart_type", ALL_TYPES)
def test_every_type_paints_its_empty_state(app, chart_type):
    """No data must mean an honest empty state, never a fabricated curve."""
    c = FoxChart(chart_type, data=[], series=[],
                 empty={"title": "No data yet", "desc": "Run the SDK to capture events."})
    _paint(c)
    if chart_type != "gauge":                 # a gauge always has a value to show
        assert c._has_data() is False
        assert c.accessibleDescription() == "No data yet"


@pytest.mark.parametrize("chart_type", ALL_TYPES)
def test_every_type_survives_resize(app, chart_type):
    c = FoxChart(chart_type, **_sample(chart_type))
    for width in (140, 320, 900, 121):
        _paint(c, w=width)


@pytest.mark.parametrize("chart_type", ALL_TYPES)
def test_sane_size_hint(app, chart_type):
    c = FoxChart(chart_type, **_sample(chart_type))
    assert c.minimumWidth() >= 120
    assert 10 <= c.height() <= 400


def test_unknown_type_falls_back_to_bar(app):
    """The web's draw() does the same rather than rendering nothing."""
    assert FoxChart("pie-chart-3000", data=[{"label": "a", "value": 1}]).type == "bar"


# ── option surface ──────────────────────────────────────────────────────────
def test_tone_names_match_the_web_map():
    assert tone_color("ok") == OK_GREEN
    assert tone_color("bad") == BAD_RED
    assert tone_color("warn") == WARN_AMBER
    assert tone_color("fox") == WEB["fox"] == tone_color("brand")
    assert tone_color("blue") == CHART[1]
    assert tone_color("pink") == CHART[3]
    assert tone_color("mute") == WEB["muted2"]


def test_literal_colours_pass_through_and_unknown_tones_cycle():
    assert tone_color("#123456") == "#123456"
    assert tone_color(None, 0) == CHART[0]
    assert tone_color(None, len(CHART)) == CHART[0]      # wraps
    assert tone_color("not-a-tone", 1) == CHART[1]


def test_series_and_data_shapes_normalise_alike(app):
    from_data = FoxChart("bar", data=[{"label": "a", "value": 3},
                                      {"label": "b", "value": 5}])
    from_series = FoxChart("line", labels=["a", "b"],
                           series=[{"values": [3, 5]}])
    assert from_data._norm()[1][0]["values"] == [3.0, 5.0]
    assert from_series._norm()[1][0]["values"] == [3.0, 5.0]
    assert from_data._norm()[0] == from_series._norm()[0] == ["a", "b"]


def test_bad_values_become_zero_not_crashes(app):
    c = FoxChart("bar", data=[{"label": "a", "value": None},
                              {"label": "b", "value": "oops"},
                              {"label": "c", "value": "7"}])
    assert c._norm()[1][0]["values"] == [0.0, 0.0, 7.0]
    _paint(c)


def test_hbar_honours_limit(app):
    rows = [{"label": f"p{i}", "value": i} for i in range(20)]
    c = FoxChart("hbar", data=rows, limit=5, rowH=30)
    _paint(c)
    assert len(c._hit) == 5
    assert c.height() == 5 * 30 + 4


def test_gauge_clamps_and_handles_unlimited(app):
    over = FoxChart("gauge", value=500, max=100)
    _paint(over)                                   # must not overflow its track
    unlimited = FoxChart("gauge", value=9, unlimited=True)
    _paint(unlimited)
    assert "unlimited" in unlimited.accessibleDescription()


def test_donut_ignores_non_positive_slices(app):
    c = FoxChart("donut", data=[{"label": "a", "value": 5},
                                {"label": "zero", "value": 0},
                                {"label": "neg", "value": -3}])
    _paint(c)
    assert len(c._hit) == 1


def test_legend_rules_match_the_web(app):
    """line/stacked: only with legend=True AND >1 series. donut: unless False."""
    one = FoxChart("line", labels=["a"], series=[{"values": [1]}], legend=True)
    two = FoxChart("line", labels=["a"], legend=True,
                   series=[{"values": [1]}, {"values": [2]}])
    off = FoxChart("line", labels=["a"], series=[{"values": [1]}, {"values": [2]}])
    assert one._wants_legend() is False
    assert two._wants_legend() is True
    assert off._wants_legend() is False

    donut = FoxChart("donut", data=[{"label": "a", "value": 1}])
    donut_off = FoxChart("donut", data=[{"label": "a", "value": 1}], legend=False)
    assert donut._wants_legend() is True
    assert donut_off._wants_legend() is False


def test_hover_regions_are_built_for_tooltips(app):
    c = FoxChart("bar", **_sample("bar"))
    _paint(c)
    assert len(c._hit) == 3
    label, text = c._hit[2][0], c._hit[2][1]
    assert "Wed" in text and "7" in text
    assert "3 breaches" in text                   # the `sub` field reaches the tip


def test_stacked_tooltip_reports_each_series_and_the_total(app):
    c = FoxChart("stacked", **_sample("stacked"))
    _paint(c)
    tip = c._hit[1][1]
    assert "logs" in tip and "breaches" in tip and "total" in tip


# ── accessibility ───────────────────────────────────────────────────────────
def test_charts_describe_their_data_for_screen_readers(app):
    c = FoxChart("bar", data=[{"label": "Mon", "value": 4},
                              {"label": "Tue", "value": 11}], aria="events per day")
    assert c.accessibleName() == "events per day"
    desc = c.accessibleDescription()
    assert "Mon 4" in desc and "Tue 11" in desc


def test_multi_series_description_names_each_series(app):
    c = FoxChart("area", **_sample("area"))
    desc = c.accessibleDescription()
    assert "logs:" in desc and "breaches:" in desc


def test_default_accessible_name_states_the_type(app):
    assert FoxChart("donut", **_sample("donut")).accessibleName() == "donut chart"


# ── updating ────────────────────────────────────────────────────────────────
def test_set_options_swaps_the_data_and_repaints(app):
    c = FoxChart("bar", data=[{"label": "a", "value": 1}])
    _paint(c)
    c.set_options(data=[{"label": "x", "value": 9}, {"label": "y", "value": 2}])
    _paint(c)
    assert len(c._hit) == 2
    assert "x" in c._hit[0][1]


def test_going_from_data_to_empty_shows_the_empty_state(app):
    c = FoxChart("bar", data=[{"label": "a", "value": 1}])
    _paint(c)
    c.set_options(data=[], empty={"title": "Nothing left"})
    _paint(c)
    assert c._has_data() is False
    assert c.accessibleDescription() == "Nothing left"
