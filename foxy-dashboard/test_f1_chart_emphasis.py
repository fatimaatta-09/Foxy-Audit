"""F1 · the focal mark, the segment floor, and three fills that were not marks.

Three separate things share this file because they share one engine and one
harness. Each guard DRIVES the shipped chart helper under node and reads the SVG
it emits — C0's lesson, restated: a static grep over the source executes nothing,
and a deleted forEach once left 369 guards green.

    #112  a stacked segment had no floor, so one High-risk day in a thousand
          drew at 0.1px on the chart whose whole job is finding that day.
    lit   the proposal's "de-emphasise the bars nobody is looking at". Spent on
          type rather than on hue or opacity — see focusIndex()'s note for why
          both of the obvious channels were unavailable.
    #113  --fox and --muted2 were brand/state tokens doing duty as chart marks
          and failed 3:1 in light. --warn-bg is the third and it did NOT move;
          the guard below records the measurement that stopped it.

    python -m pytest foxy-dashboard/test_f1_chart_emphasis.py -q
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from test_c0_chart_segments import _SHIM, _chart_source
from test_p1_contrast import HTML, css, ratio, themes  # noqa: F401 — fixtures

SRC = HTML.read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def _render(opts: dict) -> str:
    """The SVG the SHIPPED engine emits for these options."""
    probe = (_SHIM + _chart_source()
             + "\nvar parent=El('rgb(242, 240, 238)',null), "
               "host=El('rgba(0, 0, 0, 0)', parent);\n"
             + "window.foxChart(host, " + json.dumps(opts) + ");\n"
             + "console.log(JSON.stringify(host.innerHTML));\n")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "chart.js"
        path.write_text(probe, encoding="utf-8")
        # encoding="utf-8": text=True alone decodes cp1252 on this machine and
        # turns a correct render into a mystery failure. (C1's lesson.)
        proc = subprocess.run([shutil.which("node"), str(path)],
                              capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


_BAR = re.compile(r'<rect\b[^>]*class="cx-bar"[^>]*>')


def _bars(svg: str) -> list[dict]:
    out = []
    for tag in _BAR.findall(svg):
        def attr(name, cast=str):
            hit = re.search(r'\b%s="([^"]*)"' % name, tag)
            return cast(hit.group(1)) if hit else None
        fill = re.search(r"fill:([^;\"]+)", attr("style") or "")
        out.append({"height": attr("height", float), "y": attr("y", float),
                    "fill": fill.group(1).strip() if fill else None,
                    "style": attr("style") or ""})
    assert out, "the engine emitted no bars at all"
    return out


def _texts(svg: str, cls: str) -> list[str]:
    return re.findall(r'<text\b[^>]*class="%s"[^>]*>([^<]*)</text>' % cls, svg)


# ── #112 · a rare event must not render as nothing ──────────────────────────
_ONE_IN_A_THOUSAND = {
    "type": "stacked", "height": 150, "labels": ["a", "b"],
    "series": [{"name": "high", "tone": "bad", "values": [1, 1]},
               {"name": "low", "tone": "ok", "values": [1000, 1000]}]}


def test_a_one_in_a_thousand_segment_is_still_drawable() -> None:
    """THE DEFECT. seg was (v/max)*(H-pb-pt) with no floor, so the single
    High-risk day against a thousand Low ones came out 0.1px tall — in the
    markup, absent from the screen, on "Breaches over time". C0 measured exactly
    this case and recorded it as out of its own scope; the admin engine has
    floored at Math.max(1, ...) since it was written.

    One physical device pixel is the smallest thing a screen can show, so that
    is the floor. This asserts the SHIPPED number, not that a floor exists.
    """
    thin = [b for b in _bars(_render(_ONE_IN_A_THOUSAND)) if b["height"] < 5]
    assert thin, "the 1-in-1000 case drew no small segment at all"
    for b in thin:
        assert b["height"] >= 1.0, (
            "a real High-risk day rendered %spx tall — invisible, on the chart "
            "whose entire job is finding it" % b["height"])


def test_the_floor_only_ever_lifts_a_value_that_is_already_there() -> None:
    """A floor that ran before the `v>0` test would invent a mark for a day
    with no breaches, which is worse than the defect it fixes: a fabricated
    High-risk day on a tamper-evidence product. Two bands, one of them empty on
    the second day — the second column must carry exactly one segment."""
    svg = _render({"type": "stacked", "height": 150, "labels": ["a", "b"],
                   "series": [{"name": "high", "tone": "bad", "values": [3, 0]},
                              {"name": "low", "tone": "ok", "values": [5, 5]}]})
    assert len(_bars(svg)) == 3, (
        "expected 3 segments (2 + 1); a zero drew a mark: %s" % len(_bars(svg)))


def test_the_floor_does_not_overflow_the_plot() -> None:
    """Three floored bands add at most 3px to a column. Asserted rather than
    reasoned about, because an unbounded floor would push the tallest column up
    through the chart's top padding and out of the viewBox."""
    svg = _render({"type": "stacked", "height": 150, "labels": ["a"],
                   "series": [{"name": "h", "tone": "bad", "values": [1]},
                              {"name": "m", "tone": "warn", "values": [1]},
                              {"name": "l", "tone": "ok", "values": [1000]}]})
    tops = [b["y"] for b in _bars(svg)]
    assert min(tops) >= 0, "a floored stack pushed a segment above the viewBox"


# ── the focal mark ──────────────────────────────────────────────────────────
_WEEK = {"type": "bar", "height": 120, "data": [
    {"label": "Su", "value": 4, "tone": "ok"},
    {"label": "Mo", "value": 9, "tone": "bad"},
    {"label": "Tu", "value": 6, "tone": "ok"},
    {"label": "We", "value": 2, "tone": "ok"},
    {"label": "Th", "value": 7, "tone": "bad"},
    {"label": "Fr", "value": 5, "tone": "ok"},
    {"label": "Sa", "value": 8, "tone": "ok"}]}


def test_the_focal_bar_is_named_once_and_carries_its_own_value() -> None:
    """"Spend the emphasis on the one that matters." On ana7d that is today,
    the last bar. One promoted axis label and one direct value — never a number
    on every mark, which is the thing that makes a bar chart unreadable."""
    svg = _render({**_WEEK, "focus": "last"})
    assert _texts(svg, "cx-focus") == ["Sa"], _texts(svg, "cx-focus")
    assert _texts(svg, "cx-val") == ["8"], _texts(svg, "cx-val")
    assert len(_texts(svg, "cx-axis")) == 6, (
        "the other six labels did not stay recessive: %s"
        % _texts(svg, "cx-axis"))


def test_emphasis_never_touches_a_fill() -> None:
    """THE ONE THIS PHASE EXISTS FOR. ana7d already sets tone per bar from
    breaches>0, so hue is spoken for; and de-emphasising the rest by lowering
    opacity or mixing them toward the panel composites a status fill toward the
    background and drops it under 3:1 — register #131, and the same defect #113
    is fixing four lines down. So the fills must come out byte-identical with
    and without a focus, and no bar may carry an opacity at all."""
    plain, lit = _bars(_render(_WEEK)), _bars(_render({**_WEEK, "focus": "last"}))
    assert [b["fill"] for b in plain] == [b["fill"] for b in lit], (
        "the focus repainted the bars")
    for b in lit:
        assert "opacity" not in b["style"], (
            "a bar was de-emphasised by opacity: %r" % b["style"])
        assert "color-mix" not in b["style"], (
            "a bar was mixed toward something; measure it before shipping it")


def test_a_chart_with_no_focus_renders_exactly_as_it_did() -> None:
    """Six of the seven bar charts get no focus, so the no-focus path is the
    common one and must be untouched: same padding, same heights, no stray
    empty label. Compared against an explicitly out-of-range focus, which the
    resolver rejects, so this also proves the rejection reaches the geometry."""
    assert _render(_WEEK) == _render({**_WEEK, "focus": 99}), (
        "an out-of-range focus changed the render")
    assert _render(_WEEK) == _render({**_WEEK, "focus": "today"}), (
        "a focus the resolver cannot read still changed the render")
    assert not _texts(_render(_WEEK), "cx-focus")
    assert not _texts(_render(_WEEK), "cx-val")


def test_a_stacked_focal_label_is_the_column_total() -> None:
    """A stack's bands are the tooltip's job and are already there. The number
    over the column is what the column is worth — anything else would be one
    band's value wearing the whole column's position."""
    svg = _render({"type": "stacked", "height": 180, "labels": ["a", "b"],
                   "series": [{"name": "high", "tone": "bad", "values": [1, 2]},
                              {"name": "low", "tone": "ok", "values": [4, 3]}],
                   "focus": "last"})
    assert _texts(svg, "cx-val") == ["5"], _texts(svg, "cx-val")
    assert _texts(svg, "cx-focus") == ["b"], _texts(svg, "cx-focus")


def test_a_focal_label_is_dropped_rather_than_drawn_over_its_neighbours() -> None:
    """The threats page ranges to 90 days. At that count a bar is ~2px wide and
    a two-digit number over it would sit across three of its neighbours. Below
    the width threshold the promoted axis label carries the emphasis alone."""
    wide = {"type": "bar", "height": 120, "focus": "last",
            "data": [{"label": str(i), "value": i + 1} for i in range(90)]}
    svg = _render(wide)
    assert not _texts(svg, "cx-val"), "a value label was drawn over a 2px bar"
    assert _texts(svg, "cx-focus") == ["89"], "the emphasis vanished entirely"


def test_a_ranking_chart_is_never_given_a_focus() -> None:
    """On a sorted chart the top bar is already first, so lighting it states
    what position has said. drawHBar — the engine behind "top agents" here and
    both admin rankings — takes no focus at all, and asserting that is cheaper
    than asking every future caller to remember."""
    svg = _render({"type": "hbar", "height": 120, "focus": "last", "data": [
        {"label": "a", "value": 9}, {"label": "b", "value": 4}]})
    assert not _texts(svg, "cx-focus") and not _texts(svg, "cx-val"), (
        "the ranking engine grew a focal mark")


def test_the_two_charts_that_were_given_the_treatment_actually_ask_for_it() -> None:
    """GUARD THE USE, not the definition (A6). An engine option nobody passes
    is the whole feature missing while every other guard here stays green."""
    src = SRC
    ana = src[src.index("window.foxChart('ana7d'"):][:400]
    assert "focus:'last'" in ana, "ana7d lost its focal mark: %s" % ana[:200]
    timeline = src[src.index("window.foxChart('threatTimeline',{type:'stacked',height:180,labels:"):][:600]
    assert "focus:'last'" in timeline, "the threat timeline lost its focal mark"


# ── #113 · a chart mark is not a brand token ────────────────────────────────
# every panel a chart is hosted on, not just the deepest one: --muted2
# cleared 3:1 on dark --bg and failed on dark --surf, which is where the
# threat timeline actually sits.
_PANELS = ("bg", "surf", "surf2")


@pytest.mark.parametrize("token", ["fox-series", "mute-series"])
def test_the_chart_mark_steps_clear_three_to_one_on_every_panel(themes, token):
    """--fox was 2.81:1 against --bg and --muted2 2.61:1, and both were data
    marks — TONE.fox / TONE.brand / --c-1, and TONE.mute. A mark carries no ink,
    so the panel behind it is the only thing it can be measured against.

    Both themes, because _token() defaults to dark and a light-mode failure
    ships green otherwise."""
    for theme, tokens in themes.items():
        for panel in _PANELS:
            got = ratio(tokens[token], tokens[panel])
            assert got >= 3.0, (
                "%s %s on --%s is %.2f:1" % (theme, token, panel, got))


def test_the_brand_and_disabled_tokens_were_left_where_they_were(themes):
    """The fix had to be local. --fox has sixty consumers and --muted2 is this
    stylesheet's declared disabled-only step ("2.61:1, disabled-only, on
    purpose", in the light block's own words) — moving either to satisfy a
    chart would repaint the product to fix six marks. Asserted so a later phase
    does not "simplify" the split back out."""
    assert themes["light"]["fox"] == "#f05700", themes["light"]["fox"]
    assert themes["light"]["muted2"] == "#988e83", themes["light"]["muted2"]
    assert themes["dark"]["fox-series"] == themes["dark"]["fox"], (
        "dark --fox is 7.14:1 on --bg and was supposed to alias through unchanged")
    # --mute-series does NOT alias in dark, and that is the finding: --muted2 is
    # 3.09:1 on dark --bg but 2.90:1 on dark --surf, so the Low band was failing
    # in dark as well. The brief called #113 a light-mode defect; it was not.
    assert themes["dark"]["mute-series"] != themes["dark"]["muted2"]


def test_the_engine_reads_the_series_steps_and_not_the_brand_ones() -> None:
    """GUARD THE BEHAVIOUR. A token nothing resolves to is C4's defect: a guard
    on the definition passes while the mark still paints from --fox. Driven,
    not grepped — the fill is read out of the emitted SVG."""
    fills = {b["fill"] for b in _bars(_render(
        {"type": "bar", "height": 120,
         "data": [{"label": "a", "value": 3, "tone": "fox"},
                  {"label": "b", "value": 2, "tone": "mute"}]}))}
    assert fills == {"var(--fox-series)", "var(--mute-series)"}, fills
    # --c-1 is the first series step, so an unlabelled series lands there too
    default = _bars(_render({"type": "bar", "height": 120,
                             "data": [{"label": "a", "value": 3}]}))
    assert default[0]["fill"] == "var(--c-1)", default[0]["fill"]


def test_the_gauge_no_longer_starts_from_a_fill_that_fails(css) -> None:
    """One element, two verdicts: the gauge is a --fox -> --fox2 gradient, and
    in light its right end cleared 4.73:1 while its left end failed at 2.81:1.
    The stop moves to the series step; --fox2 was already passing and stays."""
    grad = re.search(r'<linearGradient[^>]*>(.*?)</linearGradient>',
                     SRC[SRC.index("function drawGauge"):], re.S)
    assert grad and "--fox-series" in grad.group(1), grad.group(1) if grad else None
    assert "stop-color:var(--fox)\"" not in grad.group(1)


def test_the_threat_legend_swatch_matches_the_band_it_names() -> None:
    """The legend is hand-written markup next to a generated chart, so the two
    drift silently. Low is TONE.mute; the swatch has to be the same step or the
    key lies about the picture."""
    legend = SRC[SRC.index('Low (&lt;40)') - 400:SRC.index('Low (&lt;40)')]
    assert "var(--mute-series)" in legend, legend[-200:]


# ── #113 · the amber that could not move ────────────────────────────────────
_WARN_PLATES = (".lockchip", ".vstatus.warn .vstatus-ic", ".ltag.warn",
                ".pill.warn", ".sev.med", ".vres.unknown", ".badge.warn")


def test_the_amber_plate_has_an_edge_that_can_be_seen(css, themes) -> None:
    """R2's finding, one console over: a chip whose text cleared 4.5:1 while the
    pill itself sat near 1:1 against the card and dissolved. Here the plate is
    1.74:1 on --bg. The seven all already carried a 1px border and it was --bc,
    the 1.03:1 hairline C0 condemned; --warn-ink replaces it."""
    rule = re.search(r"^%s\{([^}]*)\}" % re.escape(",".join(_WARN_PLATES)),
                     css, re.M)
    assert rule, "the seven amber plates no longer share an edge rule"
    assert "border:1px solid var(--warn-ink)" in rule.group(1), rule.group(1)
    for theme, tokens in themes.items():
        # dark --warn-ink IS --warn-bg, so the ring is a deliberate no-op there
        if theme == "dark":
            assert tokens["warn-ink"] == tokens["warn-bg"]
            continue
        got = ratio(tokens["warn-ink"], tokens["bg"])
        assert got >= 3.0, "light --warn-ink on --bg is %.2f:1" % got


def _deutan(hexcolour: str) -> tuple[float, float, float]:
    """Viénot-Brettel-Mollon deuteranope simulation, sRGB in and out."""
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    h = hexcolour.lstrip("#")
    r, g, b = (lin(int(h[i:i + 2], 16)) for i in (0, 2, 4))
    l = 17.8824 * r + 43.5161 * g + 4.11935 * b
    m = 3.45565 * r + 27.1554 * g + 3.86714 * b
    s = 0.0299566 * r + 0.184309 * g + 1.46709 * b
    m = 0.494207 * l + 1.24827 * s                       # the collapsed axis
    return (0.080944 * l - 0.130504 * m + 0.116721 * s,
            -0.0102485 * l + 0.0540194 * m - 0.113615 * s,
            -0.000365294 * l - 0.00412163 * m + 0.693513 * s)


def _oklab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = (max(0.0, min(1.0, c)) for c in rgb)
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def _cvd_gap(a: str, b: str) -> float:
    pa, pb = _oklab(_deutan(a)), _oklab(_deutan(b))
    return 100 * sum((x - y) ** 2 for x, y in zip(pa, pb)) ** 0.5


def test_the_medium_band_still_separates_from_the_high_one(themes) -> None:
    """WHY --warn-bg DID NOT MOVE, and the guard that keeps it that way.

    The obvious fix was R2's: the admin had the identical #F59E0B, measured it
    as a fill and deepened it to #B45309. That value cannot be lifted across,
    because here --warn-bg is also the Medium band of "Breaches over time",
    stacked directly against --breach-bg. Deepening collapses their deuteranope
    separation from dE 19 to 3, on the one chart where the two physically touch
    and the admin has no equivalent. A sweep of 63 (red, amber) pairs found none
    that clears 3:1 against this page AND still separates from the other — the
    two constraints are mutually exclusive in this hue region, so the fill keeps
    its hue and the plate got an edge instead.

    Below 8 is dataviz's floor and it is not reachable with any amber that also
    passes contrast, so failing this means somebody traded the wrong one away.
    """
    for theme, tokens in themes.items():
        gap = _cvd_gap(tokens["warn-bg"], tokens["breach-bg"])
        assert gap >= 8.0, (
            "%s: Medium and High are dE %.1f apart to a deuteranope — they are "
            "stacked segments that touch, and the legend cannot undo that"
            % (theme, gap))


def test_the_two_bands_that_touch_are_the_ones_being_measured() -> None:
    """The pair above is only worth guarding while those two tones really do
    stack. Read from the shipped call, so a re-tone of the timeline re-aims this
    file rather than leaving it measuring a pair nobody draws."""
    call = SRC[SRC.index("window.foxChart('threatTimeline',{type:'stacked'"):][:600]
    assert "tone:'bad'" in call and "tone:'warn'" in call, call[:300]
