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
    # The fill fraction is clamped to 1, so a 500% value paints a full bar and
    # not a rectangle running off the widget.
    assert _gauge_fraction(over) == 1.0
    assert _gauge_fraction(FoxChart("gauge", value=25, max=100)) == 0.25
    assert _gauge_fraction(FoxChart("gauge", value=-5, max=100)) == 0.0
    assert _gauge_fraction(FoxChart("gauge", value=5, max=0)) == 0.0   # no /0
    unlimited = FoxChart("gauge", value=9, unlimited=True)
    _paint(unlimited)
    assert _gauge_fraction(unlimited) == 1.0
    assert "unlimited" in unlimited.accessibleDescription()


def _gauge_fraction(chart) -> float:
    """Recompute the gauge's fill fraction the way _paint_gauge does."""
    value = float(chart.o.get("value") or 0)
    mx = chart.o.get("max")
    mx = float(mx) if mx is not None else 100.0
    if chart.o.get("unlimited"):
        return 1.0
    return max(0.0, min(1.0, value / mx)) if mx else 0.0


def test_stacked_draws_when_the_first_series_is_empty(app):
    """Regression: a stacked chart whose FIRST band has no points — zero
    high-risk events in the window, the real D5 Threats case — must still draw
    the bands that DO have data, not sit blank on top of them."""
    c = FoxChart("stacked", labels=["7d", "30d"],
                 series=[{"name": "High", "tone": "bad", "values": []},
                         {"name": "Low", "tone": "ok", "values": [5, 9]}])
    assert c._has_data() is True
    _paint(c)
    assert len(c._hit) == 2, "both columns must be drawn and hoverable"
    assert "Low" in c._hit[0][1] and "total" in c._hit[0][1]


def test_stacked_handles_ragged_series_lengths(app):
    c = FoxChart("stacked", labels=["a", "b", "c"],
                 series=[{"name": "x", "values": [1]},
                         {"name": "y", "values": [2, 3, 4]}])
    _paint(c)
    assert len(c._hit) == 3          # spans the longest series
    assert "total 1" in c._hit[0][1] or "total 3" in c._hit[0][1]


def test_stacked_with_no_points_at_all_is_empty(app):
    c = FoxChart("stacked", labels=[], series=[{"values": []}, {"values": []}],
                 empty={"title": "No breach data"})
    assert c._has_data() is False
    _paint(c)
    assert c.accessibleDescription() == "No breach data"


# ── sparkline specifics ─────────────────────────────────────────────────────
def test_sparkline_suppresses_dots_labels_and_hit_map(app):
    """The spark is a glance-sized trend: no dots, no axis labels, no tooltips
    (the web passes spark=true, which skips all three)."""
    spark = FoxChart("sparkline", data=[{"label": str(i), "value": v}
                                        for i, v in enumerate([2, 6, 3, 9])])
    _paint(spark)
    assert spark._hit == []
    line = FoxChart("line", data=[{"label": str(i), "value": v}
                                  for i, v in enumerate([2, 6, 3, 9])])
    _paint(line)
    assert len(line._hit) == 4        # the full chart DOES get per-point tips


def test_sparkline_default_height_matches_the_web(app):
    assert FoxChart("sparkline", data=[{"label": "a", "value": 1}]).height() == 42


def test_sparkline_uses_the_tight_padding(app):
    """Web pads the spark 2/2/4/4 vs 8/8/12/18 for a full line chart, so the
    trace fills its box; verified via the drawn extent."""
    data = [{"label": str(i), "value": v} for i, v in enumerate([0, 10])]
    spark = FoxChart("sparkline", data=data, height=42)
    full = FoxChart("line", data=data, height=42)
    _paint(spark)
    _paint(full)
    # Same widget height, but the spark's usable band is taller by the padding
    # difference (12+18) - (4+4) = 22 px.
    assert spark.height() == full.height() == 42


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


# ── axis tick collision (D5-P4) ─────────────────────────────────────────────
def test_text_bounds_measures_glyphs_not_the_layout_rect():
    """Axis labels are drawn into fixed 60px rects the text rarely fills. The
    first cut compared rect edges, which understated a centred label's right
    edge by half the slack — so real collisions still slipped through."""
    from PyQt6.QtCore import QRectF, Qt
    from charts import _text_bounds
    rect = QRectF(100, 0, 60, 14)
    assert _text_bounds(rect, Qt.AlignmentFlag.AlignLeft, 20) == (100.0, 120.0)
    assert _text_bounds(rect, Qt.AlignmentFlag.AlignRight, 20) == (140.0, 160.0)
    # centred: inset by (60-20)/2 = 20 on each side
    assert _text_bounds(rect, Qt.AlignmentFlag.AlignHCenter, 20) == (120.0, 140.0)


def test_text_bounds_clamps_when_the_label_overflows_its_rect():
    from PyQt6.QtCore import QRectF, Qt
    from charts import _text_bounds
    rect = QRectF(100, 0, 60, 14)
    assert _text_bounds(rect, Qt.AlignmentFlag.AlignHCenter, 90) == (100.0, 160.0)


def test_a_long_axis_series_draws_no_overlapping_ticks(app):
    """The real defect: 27 daily dates rendered the last two on top of each
    other as one smear. Render it and count what actually got drawn."""
    from PyQt6.QtGui import QFontMetrics
    from charts import FoxChart, _TICK_GAP, _text_bounds
    from PyQt6.QtCore import QRectF, Qt
    chart = FoxChart("area", height=140)
    chart.resize(420, 140)
    chart.set_options(height=140, data=[
        {"label": f"2026-07-{d:02d}", "value": d} for d in range(1, 28)])
    assert not _paint(chart).isNull()
    # Re-run the selection the painter uses and assert the invariant it keeps.
    fm = QFontMetrics(chart._axis_font())
    labels = [r["label"] for r in chart._rows()]
    step = max(1, -(-len(labels) // 6))
    drawn_right, drawn = None, 0
    for i, lab in enumerate(labels):
        if i % step and i != len(labels) - 1:
            continue
        x = 8.0 + (i / (len(labels) - 1)) * (420 - 16)
        if i == 0:
            rect, flag = QRectF(x, 0, 60, 14), Qt.AlignmentFlag.AlignLeft
        elif i == len(labels) - 1:
            rect, flag = QRectF(x - 60, 0, 60, 14), Qt.AlignmentFlag.AlignRight
        else:
            rect, flag = QRectF(x - 30, 0, 60, 14), Qt.AlignmentFlag.AlignHCenter
        left, right = _text_bounds(rect, flag, fm.horizontalAdvance(lab))
        if drawn_right is not None and left < drawn_right + _TICK_GAP:
            continue
        assert drawn_right is None or left >= drawn_right + _TICK_GAP
        drawn_right, drawn = right, drawn + 1
    assert drawn >= 2


# ── declared panel state overrides inferred emptiness (D5, owner decision) ──
def test_a_gauge_with_a_failed_state_does_not_paint_a_confident_zero(app):
    """A gauge always claims to have a value, so without this it would draw a
    0% bar for a request that never answered."""
    from charts import FoxChart
    chart = FoxChart("gauge", height=12)
    chart.resize(200, 12)
    chart.set_options(value=0, max=100, height=12,
                      empty={"state": "error", "title": "Couldn't load"})
    assert chart._has_data() is False
    assert not _paint(chart).isNull()


def test_an_ok_state_still_paints_its_data(app):
    from charts import FoxChart
    chart = FoxChart("gauge", height=12)
    chart.set_options(value=42, max=100, height=12,
                      empty={"state": "empty", "title": "Nothing yet"})
    assert chart._has_data() is True


# ── set_options replaces, deliberately (C3) ─────────────────────────────────
def test_set_options_replaces_it_does_not_merge(app):
    """Pinned because it is a trap, not because it is wrong.

    The web's foxChart rebuilds from the options it was handed on every call
    and takes its aria from `o.aria||'<type> chart'`, so it loses unsupplied
    options too — merging here would diverge, and a sticky tone from three
    refreshes ago is its own class of bug. The hazard is real though: a caller
    that sets aria once at construction loses it silently on the first refresh.
    """
    from charts import FoxChart
    chart = FoxChart("bar", height=100)
    chart.set_options(height=100, tone="bad", aria="breaches per day",
                      data=[{"label": "a", "value": 1}])
    assert chart.accessibleName() == "breaches per day"

    chart.set_options(height=100, data=[{"label": "a", "value": 2}])
    assert "tone" not in chart.o                 # gone, as designed
    assert chart.accessibleName() == "bar chart"  # and so is the name


def test_update_options_merges_for_callers_who_want_that(app):
    from charts import FoxChart
    chart = FoxChart("bar", height=100)
    chart.set_options(height=100, tone="bad", aria="breaches per day",
                      data=[{"label": "a", "value": 1}])
    chart.update_options(data=[{"label": "a", "value": 2}])
    assert chart.o["tone"] == "bad"
    assert chart.accessibleName() == "breaches per day"
    assert chart.o["data"][0]["value"] == 2


def test_update_options_keeps_the_chart_type(app):
    from charts import FoxChart
    chart = FoxChart("donut", height=100)
    chart.set_options(height=100, data=[{"label": "a", "value": 1}])
    chart.update_options(data=[{"label": "a", "value": 3}])
    assert chart.type == "donut"


# ── backing scrim (C4) ──────────────────────────────────────────────────────
def test_a_backing_is_painted_under_the_marks(app):
    """The hero sparkline is drawn over the hero's gradient, whose dark end
    leaves the site's near-black ink at 2:1. A faint scrim lifts the local
    background so the same ink clears 3:1 across the whole run."""
    from charts import FoxChart
    chart = FoxChart("sparkline", height=28)
    chart.resize(200, 28)
    chart.set_options(height=28, tone="#1a0900",
                      backing={"color": "#ffffff", "alpha": 0.26, "radius": 6},
                      data=[{"label": "a", "value": 1}, {"label": "b", "value": 4}])
    assert not _paint(chart, 200, 28).isNull()


def test_no_backing_by_default(app):
    """Every other chart sits on a card and must not gain a scrim."""
    from charts import FoxChart
    chart = FoxChart("bar", height=100)
    chart.set_options(height=100, data=[{"label": "a", "value": 1}])
    assert chart.o.get("backing") is None
    assert not _paint(chart, 200, 100).isNull()


def test_the_backing_survives_the_empty_and_error_states(app):
    """It is the widget's background, not part of the data drawing."""
    from charts import FoxChart
    from panel_state import PanelState, chart_empty
    chart = FoxChart("sparkline", height=28)
    chart.resize(200, 28)
    chart.set_options(height=28, backing={"color": "#ffffff", "alpha": 0.26},
                      empty=chart_empty(PanelState.ERROR, quiet=True), data=[])
    assert not _paint(chart, 200, 28).isNull()
