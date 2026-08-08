"""C0 · register #91 — two chart segments that touch must have a boundary.

THE DEFECT IS ARITHMETIC, NOT BAD LUCK. Every status hue in this file was tuned
to clear 3:1 against the PANEL. Passing that test pins them all into one
luminance band, and two colours in the same band are by definition close to each
other. Measured across both themes, 0 of 15 status pairs clear 3:1 against one
another:

    dark   safe/warn 1.15:1   safe/bad 1.83:1   warn/bad 2.12:1
    light  safe/bad  1.01:1   safe/warn 2.23:1  warn/bad 2.25:1

Line and area charts are unaffected — their series are physically apart and the
legend names them. The exposure is fills that TOUCH, where hue is the only thing
dividing them: `drawStacked` (Breaches over time: high/medium/low in one bar)
and `drawDonut` (grading status). `drawGauge` and `drawHBar` are NOT exposed:
their fill OVERLAYS a track rather than abutting a sibling, so the boundary
there is fill-against-background, a different test with a different answer.

⚠ THIS FILE ALREADY HAD A STROKE AND IT SEPARATED NOTHING.

Both draw calls shipped `stroke:var(--bc)`. `--bc` is rgba(255,250,245,.07) in
dark and rgba(120,105,90,.13) in light — a hairline 7% opaque against the fill
it is painted over. Composited and measured, its separation from its own fill:

    dark   over safe 1.031:1   over warn 1.026:1   over bad 1.063:1
    light  over safe 1.034:1   over warn 1.115:1   over bad 1.062:1

Present in the markup, invisible on screen. THAT is why nothing in here asserts
a stroke merely exists — the A7 lesson, assert it can fire. The guards run the
shipped engine and read the value it emits.

    python -m pytest foxy-dashboard/test_c0_chart_segments.py -q
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

HTML = Path(__file__).with_name("foxy-audit-premium.html")
SRC = HTML.read_text(encoding="utf-8")

# The panel the probe paints BEHIND the chart. The host itself is left
# transparent, so a stroke that comes back as this value also proves panelBg
# walked up the tree instead of reading the node it was handed.
PANEL = "rgb(242, 240, 238)"

_SHIM = """
var PANEL='%s';
function El(bg,parent){ return {nodeType:1,parentElement:parent||null,__bg:bg,
  clientWidth:400, innerHTML:'', __attrs:{},
  classList:{add:function(){},remove:function(){}},
  setAttribute:function(k,v){this.__attrs[k]=v;},
  insertAdjacentHTML:function(p,h){this.innerHTML+=h;},
  querySelectorAll:function(){return [];}}; }
global.getComputedStyle=function(n){ return {backgroundColor:n.__bg}; };
global.document={documentElement:{},getElementById:function(){return null;},
  addEventListener:function(){}};
global.window={addEventListener:function(){}};
global.ResizeObserver=undefined;
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
  .replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function resolve(t){ return t; }
// wireTips is DEFINED INSIDE the slice, so a stub here is hoisted over and
// never used. Its handlers close over showTip/hideTip from the enclosing IIFE,
// which the slice does not carry -- stub those instead. They are only reached
// once querySelectorAll returns marks, so an El stub that returns [] hides the
// need for them; supplying them keeps the probe from depending on that.
function showTip(){} function hideTip(){}
""" % PANEL

# A data mark, matched WITHOUT assuming where `class` sits in the attribute
# list. S() emits attributes in object-key order, so class lands after x/y/
# width/height — an expression anchored to the tag opening silently matches
# nothing and reads as "the chart drew no segments". Anchor to the name.
_MARK = re.compile(r'<(?:rect|path)\b[^>]*class="cx-(?:bar|arc)"[^>]*>')


def _chart_source() -> str:
    """The shipped chart helper, from its banner to the end of foxChart."""
    start = SRC.index("/* ───────── inline-SVG chart helper")
    end = SRC.index("\n", SRC.index("window.foxChart=function", start))
    return SRC[start:end]


def _draw(opts: dict) -> list[dict]:
    """Run the SHIPPED foxChart over a stubbed DOM; return one dict per mark."""
    probe = (_SHIM + _chart_source()
             + "\nvar parent=El(PANEL,null), host=El('rgba(0, 0, 0, 0)', parent);\n"
             + "window.foxChart(host, " + json.dumps(opts) + ");\n"
             + "console.log(JSON.stringify(host.innerHTML));\n")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "chart.js"
        path.write_text(probe, encoding="utf-8")
        proc = subprocess.run([shutil.which("node"), str(path)],
                              capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    html = json.loads(proc.stdout.strip().splitlines()[-1])
    marks = []
    for tag in _MARK.findall(html):
        style = re.search(r'style="([^"]*)"', tag)
        style = style.group(1) if style else ""
        stroke = re.search(r"stroke:([^;\"]+)", style)
        height = re.search(r'height="([\d.]+)"', tag)
        marks.append({"tag": tag, "style": style,
                      "stroke": stroke.group(1).strip() if stroke else None,
                      "height": float(height.group(1)) if height else None})
    assert marks, "the engine emitted no data marks at all"
    return marks


_STACKED = {"type": "stacked", "height": 150, "labels": ["a", "b", "c"],
            "series": [{"name": "high", "tone": "bad", "values": [4, 6, 3]},
                       {"name": "medium", "tone": "warn", "values": [7, 5, 8]},
                       {"name": "low", "tone": "ok", "values": [9, 4, 6]}]}
_DONUT = {"type": "donut", "height": 150,
          "data": [{"label": "graded", "value": 40, "tone": "ok"},
                   {"label": "failed", "value": 30, "tone": "bad"},
                   {"label": "pending", "value": 30, "tone": "warn"}]}

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def test_touching_chart_segments_are_drawn_with_a_boundary() -> None:
    """"Breaches over time" stacks high/medium/low in one bar, and bad/warn
    measure 2.12:1 against each other in dark and 2.25:1 in light."""
    for label, opts in (("stacked", _STACKED), ("donut", _DONUT)):
        bare = [m["tag"][:90] for m in _draw(opts) if not m["stroke"]]
        assert not bare, "%s: segments touch with no boundary: %s" % (label, bare)


def test_the_boundary_is_opaque_and_is_the_panel_behind_the_chart() -> None:
    """THE ONE THAT WOULD HAVE CAUGHT THE OLD CODE. `stroke:var(--bc)` was
    already there and measured 1.03:1 against its own fill, because --bc is 7%
    alpha. A guard that accepted any stroke would have passed on the broken
    file, so this asserts the emitted VALUE: the opaque panel colour, not --bc,
    not a var() that resolves to a translucent border token.

    The probe paints the PARENT and leaves the host transparent, so passing
    also proves panelBg walks up rather than reading the node it was given.
    """
    for label, opts in (("stacked", _STACKED), ("donut", _DONUT)):
        for m in _draw(opts):
            assert m["stroke"] == PANEL, (
                "%s: the boundary is %r, not the opaque panel behind the chart"
                % (label, m["stroke"]))
            assert "--bc" not in m["style"], (
                "%s: the boundary is back on --bc, which is 7%% alpha and "
                "measures 1.03:1 against the fill it is drawn over" % label)


def test_a_segment_too_thin_for_a_stroke_is_not_closed_over_by_one() -> None:
    """THE ONE THAT PROTECTS THE DATA. An SVG stroke is CENTRED on the edge, so
    a 1.5px stroke eats 0.75px of fill on each side and closes over any segment
    thinner than itself. One High-risk day against a thousand Low ones is such
    a segment, and it vanishing would state something false about the ledger —
    the same class of defect as the false empty state drawStacked already
    carries a comment about. Below the threshold the stroke is dropped.

    ⚠ THIS ALONE DOES NOT MAKE THE SLIVER LEGIBLE, and it never claimed to.
    When this was written drawStacked had no minimum segment height at all —
    seg was (v/max)*(H-pb-pt) with no floor, so the 1-in-1000 case measured here
    rendered 0.1px tall, and that was recorded as pre-existing and out of C0's
    scope. F1 (#112) closed it: the floor is MIN_SEG and the guard for it lives
    in test_f1_chart_emphasis.py. What is still asserted HERE is only the other
    half — that the hairline boundary does not eat the sliver.
    """
    marks = _draw({"type": "stacked", "height": 150, "labels": ["a", "b"],
                   "series": [{"name": "high", "tone": "bad", "values": [1, 1]},
                              {"name": "low", "tone": "ok", "values": [1000, 1000]}]})
    thin = [m for m in marks if not m["stroke"]]
    assert thin, ("every segment was stroked, so the 1-in-1000 sliver carries a "
                  "stroke many times its own height and is gone entirely")
    for m in thin:
        assert m["height"] and m["height"] > 0, "a real value was drawn with no height"
    assert [m for m in marks if m["stroke"]], (
        "the thin case switched the boundary off for the whole chart")


def test_a_segment_that_touches_nothing_gets_no_boundary() -> None:
    """A one-series stack has nothing above or below it and a one-slice donut
    has no neighbour. Stroking those outlines a lone mark in the panel colour
    for no reason, which on a donut reads as a gap that is not there."""
    for opts in ({"type": "stacked", "height": 150, "labels": ["a"],
                  "series": [{"name": "only", "tone": "ok", "values": [5]}]},
                 {"type": "donut", "height": 150,
                  "data": [{"label": "all", "value": 9, "tone": "ok"}]}):
        assert not any(m["stroke"] for m in _draw(opts)), (
            "a mark with no neighbour was given a boundary anyway")


def test_the_overlay_charts_are_left_alone() -> None:
    """drawGauge and drawHBar draw a fill ON TOP of a track, so no two fills
    abut and #91 does not reach them. Their `--bc` stroke is a border on a
    track, which is what --bc is for, and it stays.

    Guarded because the cheap way to "fix" #91 is a search-and-replace of
    var(--bc) across the file, which would repaint two components that were
    never broken.
    """
    src = _chart_source()
    gauge = src[src.index("function drawGauge"):]
    assert "cx-gauge-track" in gauge, "the gauge lost its track"
    css = re.search(r"\.chart-host \.cx-gauge-track\{([^}]*)\}", SRC)
    assert css, "the gauge track's rule is gone"
    assert "stroke:var(--bc)" in css.group(1), (
        "the gauge track's border was swept up in the #91 change; it is a "
        "border on a track, not a divider between two saturated fills"
    )
