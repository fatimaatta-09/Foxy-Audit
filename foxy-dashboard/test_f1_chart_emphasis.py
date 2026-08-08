"""#112 · a stacked segment had no floor.

One High-risk day in a thousand drew at 0.1px on "Breaches over time" — the
chart whose entire job is finding that day. Each guard DRIVES the shipped chart
helper under node and reads the SVG it emits, because a static grep over the
source executes nothing (C0: a deleted forEach once left 369 guards green).

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
