"""Foxy Audit desktop — the chart family (D2).

QPainter reimplementation of the web dashboard's `foxChart`
(foxy-dashboard/foxy-audit-premium.html:2029-2152). Same option surface, same
eight types, same geometry constants and tone names, so the page phases
(D4-D10) can port a web chart call almost verbatim:

    web      foxChart('#usageChart', {type:'area', labels:…, series:[…]})
    desktop  FoxChart('area', labels=…, series=[…])

Types: bar · hbar · line · area · sparkline · stacked · donut · gauge

Options (mirroring the web's `o`):
    type      one of the above (unknown falls back to bar, as the web does)
    data      [{label, value, tone, sub}]              — single-series shape
    series    [{name, tone, values[]}] + labels[]      — multi-series shape
    height    px; per-type defaults match the web
    tone      named tone or literal colour for a single series
    legend    show the legend (line/stacked: only when >1 series;
              donut: shown unless legend=False — again matching the web)
    center    donut centre text
    value/max/unlimited/tip/label   gauge
    limit/rowH                      hbar (8 rows, 32 px — the web's defaults)
    empty     {title, desc} for the honest empty state
    aria      accessible name; a data summary is generated for the description

Deliberate desktop deviations from the web, all additive:
* A draw-in animation (220 ms, skipped under OS reduced-motion) — the web has
  none, but a native widget that pops fully-formed feels broken next to one
  that doesn't.
* Tooltips are QToolTip on hover-hit-test rather than a floating div.
* Every chart carries an accessible name AND a generated description, because
  a screen reader gets nothing from a painted widget (the web at least has
  role="img" + aria-label).
* Series are distinguished by BOTH colour and legend text; the line chart also
  varies dash patterns after the first series, so the reading never depends on
  colour alone.

No fabricated data, ever: a chart with nothing to show paints the empty state.
"""

from __future__ import annotations

import sys

from PyQt6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath,
    QPen, QPolygonF,
)
from PyQt6.QtWidgets import QSizePolicy, QToolTip, QWidget

from foxy_tokens import (
    BAD_RED, CHART, OK_GREEN, WARN_AMBER, WEB, pick_font, qcolor, reduced_motion,
)

# The web's TONE map (foxy-audit-premium.html:2031), value-for-value.
TONES: dict[str, str] = {
    "ok": OK_GREEN,          # --safe-bg
    "bad": BAD_RED,          # --breach-bg
    "warn": WARN_AMBER,      # --warn-bg
    "fox": WEB["fox"],
    "brand": WEB["fox"],
    "blue": CHART[1],        # --c-2
    "pink": CHART[3],        # --c-4
    "mute": WEB["muted2"],
}

# Per-type default heights, matching the web's `o.height||…` fallbacks.
_DEFAULT_HEIGHT = {
    "bar": 140, "hbar": 0, "line": 150, "area": 150,
    "sparkline": 42, "stacked": 150, "donut": 150, "gauge": 14,
}

_ANIM_MS = 220          # inside the 150-300 ms band
_LEGEND_H = 22
_TICK_GAP = 6           # minimum clear space between two axis labels, px


def _text_bounds(rect: QRectF, flag, advance: float) -> tuple[float, float]:
    """Where the glyphs of a label actually land inside its layout rect.

    Axis labels are drawn into fixed 60px rects that the text rarely fills, so
    the rect edges are not the text edges: a right-aligned label hugs the right
    edge, a left-aligned one the left, and a CENTRED one sits inset by half the
    leftover width on both sides. Collision detection has to compare glyphs.
    """
    if flag == Qt.AlignmentFlag.AlignRight:
        return rect.right() - advance, rect.right()
    if flag == Qt.AlignmentFlag.AlignHCenter:
        inset = max(0.0, (rect.width() - advance) / 2.0)
        return rect.left() + inset, rect.right() - inset
    return rect.left(), rect.left() + advance


def tone_color(tone: str | None, index: int = 0) -> str:
    """Named tone → colour, else a literal colour, else the categorical cycle.
    Mirrors the web's toneColor()."""
    if tone:
        if tone in TONES:
            return TONES[tone]
        if tone.startswith("#") or tone.startswith("rgb") or tone.startswith("hsl"):
            return tone
    return CHART[(index or 0) % len(CHART)]


def _num(v) -> float:
    """The web's `+v||0` — anything unparseable is zero, never a crash."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if f != f else f      # NaN → 0


def _fmt(v: float) -> str:
    """Integers print without a trailing .0, matching the web's String(v)."""
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


class FoxChart(QWidget):
    """One widget, eight chart types — the desktop twin of `foxChart`."""

    TYPES = ("bar", "hbar", "line", "area", "sparkline", "stacked", "donut", "gauge")

    def __init__(self, chart_type: str = "bar", parent=None, **options):
        super().__init__(parent)
        self.type = chart_type if chart_type in self.TYPES else "bar"
        self.o: dict = {}
        self._hit: list[tuple[QRectF, str]] = []     # tooltip hit-test regions
        self._progress = 1.0
        self._animated_once = False
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(_ANIM_MS)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(120)
        self.set_options(**options)

    # ── options / data ──────────────────────────────────────────────────
    def set_options(self, **options):
        """REPLACE the option bag and repaint — this does not merge.

        Every option not passed here is GONE, including ones that feel like
        properties of the widget rather than of the data: `aria`, `tone`,
        `height`, `empty`. A caller that sets `aria=` once at construction and
        then refreshes with `set_options(data=…)` silently loses its accessible
        name, and nothing fails — the chart just stops announcing itself. Pass
        the full bag every time, or use `update_options()`.

        Replace is deliberate, not an oversight: the web's `foxChart(host, o)`
        rebuilds the host's innerHTML from the options it was handed and takes
        its aria from `o.aria||'<type> chart'` on every call, so it loses
        unsupplied options too. Merging here would be a divergence, and a
        sticky `tone` or `empty` from three refreshes ago is its own class of
        bug. `test_set_options_replaces_it_does_not_merge` pins this.
        """
        if "type" in options:
            t = options.pop("type")
            self.type = t if t in self.TYPES else "bar"
        self.o = dict(options)
        self._apply_height()
        self._describe()
        self._replay()

    def update_options(self, **options):
        """Merge into the current bag — for callers who want to change one
        thing without restating the rest. `set_options` is the parity path;
        this is the safe one."""
        self.set_options(**{**self.o, "type": self.type, **options})

    def set_data(self, data, **options):
        """Convenience for the single-series shape."""
        self.set_options(data=data, **{**self.o_without("data"), **options})

    def o_without(self, key: str) -> dict:
        return {k: v for k, v in self.o.items() if k != key}

    def _apply_height(self):
        h = self.o.get("height")
        if self.type == "hbar":
            # Height follows the rows we actually PAINT — the web sizes from the
            # already-sliced list, so an over-long input must not reserve dead
            # space for rows that never get drawn.
            rows = min(len(self._rows()), int(self.o.get("limit") or 8)) or 1
            h = h or rows * int(self.o.get("rowH") or 32) + 4
        else:
            h = h or _DEFAULT_HEIGHT[self.type]
        if self._wants_legend():
            h += _LEGEND_H
        self.setFixedHeight(int(h))

    def _replay(self):
        """Animate the draw-in ONCE, when data first arrives.

        The console re-polls every 15 s; re-animating on each refresh would
        make every chart on the page twitch in unison for no informational
        gain — motion should mean something. So: first data draws in, later
        updates just repaint."""
        if (reduced_motion() or not self._has_data() or self._animated_once
                or not self.isVisible()):
            # Off-screen is the fourth case: a chart on a stack page the user
            # is not looking at has no draw-in to show, and animating it only
            # keeps Qt's animation timer ticking on a widget that may be torn
            # down underneath it.
            self._progress = 1.0
            self.update()
            return
        self._animated_once = True
        self._progress = 0.0
        self._anim.stop()
        self._anim.start()

    def _on_anim(self, value):
        self._progress = float(value)
        self.update()

    def stop_animation(self):
        """Settle the draw-in immediately and release the animation timer.

        Hiding a parent does not deliver a hide event to its children, so a
        window that closes mid-animation leaves its charts ticking against Qt's
        animation timer while they are being torn down. Owners call this in
        their own teardown; the chart also does it for itself on hide."""
        self._anim.stop()
        self._progress = 1.0

    def hideEvent(self, e):
        self.stop_animation()
        super().hideEvent(e)

    # ── normalisation (the web's norm()) ────────────────────────────────
    def _rows(self) -> list[dict]:
        return list(self.o.get("data") or [])

    def _norm(self) -> tuple[list[str], list[dict]]:
        """→ (labels, [{name, tone, values}]) for both input shapes."""
        if self.o.get("series"):
            labels = [str(x) for x in (self.o.get("labels") or [])]
            series = [{"name": s.get("name"), "tone": s.get("tone"),
                       "values": [_num(v) for v in (s.get("values") or [])]}
                      for s in self.o["series"]]
            return labels, series
        rows = self._rows()
        return ([("" if r.get("label") is None else str(r.get("label"))) for r in rows],
                [{"name": self.o.get("name"), "tone": self.o.get("tone"),
                  "values": [_num(r.get("value")) for r in rows]}])

    def _state(self) -> str | None:
        """The panel state its owner declared, if any (see panel_state.py).

        Charts that predate the state pattern pass no state and keep inferring
        emptiness from their data, so D2's callers are unaffected."""
        return (self.o.get("empty") or {}).get("state")

    def _has_data(self) -> bool:
        # A declared "loading" or "error" outranks whatever data is present: a
        # gauge in particular always claims to have a value, and would
        # otherwise paint a confident 0 over a request that never answered.
        if self._state() in ("loading", "error"):
            return False
        if self.type == "gauge":
            return True                       # a gauge always has a value
        if self.type == "donut":
            return any(_num(r.get("value")) > 0 for r in self._rows())
        if self.type in ("line", "area", "sparkline", "stacked"):
            _, series = self._norm()
            return any(s["values"] for s in series)
        return bool(self._rows())

    def _wants_legend(self) -> bool:
        if self.type == "donut":
            return self.o.get("legend") is not False and self._has_data()
        if self.type in ("line", "area", "stacked"):
            _, series = self._norm()
            return bool(self.o.get("legend")) and len(series) > 1
        return False

    # ── accessibility ───────────────────────────────────────────────────
    def _describe(self):
        """A painted widget tells assistive tech nothing, so state the data."""
        name = self.o.get("aria") or f"{self.type} chart"
        self.setAccessibleName(name)
        if not self._has_data():
            empty = self.o.get("empty") or {}
            self.setAccessibleDescription(empty.get("title") or "No data yet")
            return
        if self.type == "gauge":
            value, mx = _num(self.o.get("value")), self.o.get("max")
            self.setAccessibleDescription(
                f"{_fmt(value)} of unlimited" if self.o.get("unlimited")
                else f"{_fmt(value)} of {_fmt(_num(mx if mx is not None else 100))}")
            return
        labels, series = self._norm()
        parts = []
        for s in series:
            head = f"{s['name']}: " if s.get("name") else ""
            pairs = ", ".join(
                f"{labels[i] if i < len(labels) else i} {_fmt(v)}"
                for i, v in enumerate(s["values"][:12]))
            more = "…" if len(s["values"]) > 12 else ""
            parts.append(head + pairs + more)
        self.setAccessibleDescription("; ".join(parts))

    # ── painting ────────────────────────────────────────────────────────
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._hit = []
        w, h = float(self.width()), float(self.height())
        self._paint_backing(p, QRectF(0, 0, w, float(self.height())))
        if self._wants_legend():
            h -= _LEGEND_H
        if not self._has_data():
            self._paint_empty(p, QRectF(0, 0, w, float(self.height())))
            p.end()
            return
        area = QRectF(0, 0, w, h)
        {"bar": self._paint_bar, "hbar": self._paint_hbar,
         "line": lambda pp, r: self._paint_line(pp, r, fill=False),
         "area": lambda pp, r: self._paint_line(pp, r, fill=True),
         "sparkline": lambda pp, r: self._paint_line(pp, r, fill=True, spark=True),
         "stacked": self._paint_stacked, "donut": self._paint_donut,
         "gauge": self._paint_gauge}[self.type](p, area)
        if self._wants_legend():
            self._paint_legend(p, QRectF(0, h, w, _LEGEND_H))
        p.end()

    def _paint_backing(self, p: QPainter, r: QRectF):
        """Optional scrim behind the marks, for a chart drawn over artwork.

        The hero sparkline sits on the hero's warm gradient, which runs from
        #c96a2f down to #6e3411. The site strokes it in near-black, which is
        legible at the light end and 2:1 at the dark end — under the 3:1 floor
        for meaningful graphics. Rather than changing the ink away from the
        site's, a faint light scrim raises the local background so one ink
        clears the bar across the whole run.

        Options: `backing={"color": "#ffffff", "alpha": 0.26, "radius": 6}`.
        A stylesheet background cannot do this job — this class overrides
        paintEvent and never calls super(), so QSS backgrounds are not drawn.
        """
        spec = self.o.get("backing")
        if not spec:
            return
        colour = qcolor(spec.get("color") or "#ffffff")
        colour.setAlphaF(float(spec.get("alpha", 0.26)))
        radius = float(spec.get("radius", 6))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(colour))
        p.drawRoundedRect(r, radius, radius)

    def _paint_empty(self, p: QPainter, r: QRectF):
        """Honest empty state — never a placeholder curve."""
        empty = self.o.get("empty") or {}
        # An inline strip (the hero sparkline) has no room for a titled empty
        # state — it would paint a card-sized message inside 28px. The card
        # around it already says there is nothing yet, so draw nothing.
        if empty.get("quiet"):
            return
        title = empty.get("title") or "No data yet"
        desc = empty.get("desc")
        cx, cy = r.center().x(), r.center().y()

        p.setPen(QPen(qcolor(WEB["muted"]), 1.8))
        p.setBrush(Qt.BrushStyle.NoBrush)
        s = 15.0                                  # a small layers/box mark
        top = QPolygonF([QPointF(cx, cy - 26), QPointF(cx + s, cy - 26 + s * 0.55),
                         QPointF(cx, cy - 26 + s * 1.1),
                         QPointF(cx - s, cy - 26 + s * 0.55)])
        p.drawPolygon(top)
        p.drawPolyline(QPolygonF([QPointF(cx - s, cy - 26 + s * 1.15),
                                  QPointF(cx, cy - 26 + s * 1.7),
                                  QPointF(cx + s, cy - 26 + s * 1.15)]))

        f = QFont(pick_font("disp"), 9)
        f.setBold(True)
        p.setFont(f)
        # "Couldn't load" is not the same news as "nothing yet" and must not
        # read like it — an error states itself in the failure colour.
        p.setPen(qcolor(BAD_RED if self._state() == "error" else WEB["ink2"]))
        # A narrow card (the grading donut beside a wide trend chart) is not
        # wide enough for the title, and drawText would spill it past both
        # edges — "Nothing graded yet" rendering as "othing graded ye".
        title = QFontMetrics(f).elidedText(
            title, Qt.TextElideMode.ElideRight, int(r.width()) - 8)
        p.drawText(QRectF(r.left(), cy + 6, r.width(), 18),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, title)
        if desc:
            fd = QFont(pick_font("mono"), 7)
            p.setFont(fd)
            p.setPen(qcolor(WEB["muted"]))
            p.drawText(QRectF(r.left() + 8, cy + 24, r.width() - 16, 30),
                       int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
                           | Qt.TextFlag.TextWordWrap), desc)

    def _axis_font(self) -> QFont:
        f = QFont(pick_font("mono"), 7)
        f.setBold(True)
        return f

    def _paint_bar(self, p: QPainter, r: QRectF):
        rows = self._rows()
        W, H = r.width(), r.height()
        pb, pt = 20.0, 12.0
        vals = [_num(x.get("value")) for x in rows]
        mx = max(1.0, max(vals) if vals else 1.0)
        n = len(rows)
        gap = max(4.0, W * 0.03)
        bw = (W - gap * (n - 1)) / n if n else W
        p.setFont(self._axis_font())
        for i, row in enumerate(rows):
            v = vals[i]
            full = max(1.0, (v / mx) * (H - pb - pt))
            bh = full * self._progress
            bx = i * (bw + gap)
            by = H - pb - bh
            rect = QRectF(bx, by, bw, bh)
            p.setPen(QPen(qcolor(WEB["bc"]), 2))
            p.setBrush(QBrush(qcolor(tone_color(row.get("tone"), i))))
            p.drawRoundedRect(rect, min(6.0, bw / 3), min(6.0, bw / 3))
            label = "" if row.get("label") is None else str(row.get("label"))
            p.setPen(qcolor(WEB["muted"]))
            p.drawText(QRectF(bx, H - 18, bw, 16),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                       label)
            sub = f" · {row['sub']}" if row.get("sub") else ""
            self._hit.append((QRectF(bx, r.top(), bw, H),
                              f"{label} · {_fmt(v)}{sub}"))

    def _paint_hbar(self, p: QPainter, r: QRectF):
        rows = self._rows()[: int(self.o.get("limit") or 8)]
        W = r.width()
        row_h = float(self.o.get("rowH") or 32)
        mx = max(1.0, max((_num(x.get("value")) for x in rows), default=1.0))
        name_f = QFont(pick_font("disp"), 8)
        name_f.setBold(True)
        for i, row in enumerate(rows):
            v = _num(row.get("value"))
            y = i * row_h
            label = "—" if row.get("label") is None else str(row.get("label"))
            p.setFont(name_f)
            p.setPen(qcolor(WEB["ink"]))
            p.drawText(QRectF(0, y, W * 0.7, 14),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       label)
            p.setFont(self._axis_font())
            p.setPen(qcolor(WEB["muted"]))
            p.drawText(QRectF(W * 0.7, y, W * 0.3, 14),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       _fmt(v))
            track = QRectF(0, y + 16, W, 9)
            p.setPen(QPen(qcolor(WEB["bc"]), 2))          # .cx-gauge-track
            p.setBrush(QBrush(qcolor(WEB["bg"])))
            p.drawRoundedRect(track, 4.5, 4.5)
            fw = max(4.0, (v / mx) * W) * self._progress
            p.setBrush(QBrush(qcolor(tone_color(row.get("tone"), i))))
            p.drawRoundedRect(QRectF(0, y + 16, fw, 9), 4.5, 4.5)
            self._hit.append((QRectF(0, y, W, row_h), f"{label} · {_fmt(v)}"))

    def _paint_line(self, p: QPainter, r: QRectF, *, fill: bool, spark: bool = False):
        labels, series = self._norm()
        all_values = [v for s in series for v in s["values"]]
        W, H = r.width(), r.height()
        pl = pr = 2.0 if spark else 8.0
        pt = 4.0 if spark else 12.0
        pb = 4.0 if spark else 18.0
        mx = max(all_values)
        mn = min(0.0, min(all_values))
        if mx == mn:
            mx = mn + 1
        iw, ih = W - pl - pr, H - pt - pb
        n = max(len(series[0]["values"]), len(labels)) if series else 0

        def X(i):
            return pl + (iw / 2 if n <= 1 else (i / (n - 1)) * iw)

        def Y(v):
            return pt + ih - ((v - mn) / (mx - mn)) * ih

        if not spark:                                  # 3 grid lines, as the web
            p.setPen(QPen(qcolor(WEB["line"]), 1))
            for g in range(3):
                gy = pt + (ih / 2) * g
                p.drawLine(QPointF(pl, gy), QPointF(W - pr, gy))

        for si, s in enumerate(series):
            col = qcolor(tone_color(s.get("tone"), si))
            pts = [QPointF(X(i), Y(v)) for i, v in enumerate(s["values"])]
            if not pts:
                continue
            shown = max(2, int(len(pts) * self._progress)) if self._progress < 1 else len(pts)
            vis = pts[:shown]
            if fill and len(vis) > 1:
                path = QPainterPath(vis[0])
                for pt_ in vis[1:]:
                    path.lineTo(pt_)
                path.lineTo(QPointF(vis[-1].x(), pt + ih))
                path.lineTo(QPointF(vis[0].x(), pt + ih))
                path.closeSubpath()
                fill_col = QColor(col)
                fill_col.setAlphaF(0.16)               # the web's opacity:.16
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(fill_col))
                p.drawPath(path)
            pen = QPen(col, 2.0 if spark else 2.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            if si:                       # never rely on colour alone (a11y)
                pen.setStyle((Qt.PenStyle.DashLine, Qt.PenStyle.DotLine,
                              Qt.PenStyle.DashDotLine)[(si - 1) % 3])
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            if len(vis) > 1:
                p.drawPolyline(QPolygonF(vis))
            if not spark:
                p.setPen(QPen(qcolor(WEB["bc"]), 1.5))
                p.setBrush(QBrush(col))
                for i, pt_ in enumerate(pts):
                    if i < len(vis):
                        p.drawEllipse(pt_, 2.6, 2.6)   # only the revealed dots
                    # …but EVERY point is hoverable: the draw-in is a visual
                    # reveal, not a reason to withhold a tooltip.
                    lab = labels[i] if i < len(labels) else str(i)
                    name = f" · {s['name']}" if s.get("name") else ""
                    self._hit.append((QRectF(pt_.x() - 7, pt_.y() - 7, 14, 14),
                                      f"{lab}{name} · {_fmt(s['values'][i])}"))

        if not spark and labels:
            p.setFont(self._axis_font())
            p.setPen(qcolor(WEB["muted"]))
            step = max(1, -(-len(labels) // 6))         # ceil(len/6), as the web
            fm = QFontMetrics(self._axis_font())
            drawn_right = None
            for i, lab in enumerate(labels):
                if i % step and i != len(labels) - 1:
                    continue
                x = X(i)
                if i == 0:
                    rect, flag = QRectF(x, H - 16, 60, 14), Qt.AlignmentFlag.AlignLeft
                elif i == len(labels) - 1:
                    rect, flag = QRectF(x - 60, H - 16, 60, 14), Qt.AlignmentFlag.AlignRight
                else:
                    rect, flag = QRectF(x - 30, H - 16, 60, 14), Qt.AlignmentFlag.AlignHCenter
                # The final tick is always drawn, so on a series whose length is
                # just past a step boundary it lands on top of its neighbour and
                # the two dates render as one unreadable smear. Skip it instead.
                #
                # The glyphs, not the layout rect, are what collide: every rect
                # here is 60px wide but a label rarely fills it, and a CENTRED
                # label sits inset by half the slack on each side. Measuring the
                # rect edges (as the first cut did) overstates the box on the
                # left and understates it on the right, so a genuine overlap
                # could still slip through.
                left, right = _text_bounds(rect, flag, fm.horizontalAdvance(str(lab)))
                if drawn_right is not None and left < drawn_right + _TICK_GAP:
                    continue
                p.drawText(rect, flag | Qt.AlignmentFlag.AlignVCenter, str(lab))
                drawn_right = right

    def _paint_stacked(self, p: QPainter, r: QRectF):
        labels, series = self._norm()
        # Span the LONGEST series, not series[0]. Keying off the first series
        # (as the web does) means a stacked chart whose first band is empty —
        # zero High-risk events in the window, exactly the D5 Threats case —
        # draws nothing at all while the any-series _has_data() check keeps the
        # empty state suppressed: a blank chart sitting on top of real data.
        # Missing points count as 0, so shorter series simply contribute
        # nothing at those indices. The web routes this case to "No data yet",
        # which would be a FALSE empty state — worse, given the honesty rule.
        n = max((len(s["values"]) for s in series), default=0)
        totals = [sum(_num(s["values"][i]) if i < len(s["values"]) else 0.0
                      for s in series) for i in range(n)]
        mx = max(1.0, max(totals) if totals else 1.0)
        W, H = r.width(), r.height()
        pb, pt = 20.0, 12.0
        gap = max(4.0, W * 0.03)
        bw = (W - gap * (n - 1)) / n if n else W
        p.setFont(self._axis_font())
        for i in range(n):
            yb = H - pb
            bx = i * (bw + gap)
            parts = []
            for si, s in enumerate(series):
                v = _num(s["values"][i]) if i < len(s["values"]) else 0.0
                if v <= 0:
                    continue
                seg = (v / mx) * (H - pb - pt) * self._progress
                yb -= seg
                p.setPen(QPen(qcolor(WEB["bc"]), 1.5))
                p.setBrush(QBrush(qcolor(tone_color(s.get("tone"), si))))
                p.drawRect(QRectF(bx, yb, bw, seg))
                parts.append(f"{s.get('name') or f'S{si + 1}'}: {_fmt(v)}")
            label = str(labels[i]) if i < len(labels) else ""
            p.setPen(qcolor(WEB["muted"]))
            p.drawText(QRectF(bx, H - 18, bw, 16),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                       label)
            tip = label + ("\n" + "\n".join(parts) if parts else "")
            self._hit.append((QRectF(bx, r.top(), bw, H),
                              f"{tip}\ntotal {_fmt(totals[i])}"))

    def _paint_donut(self, p: QPainter, r: QRectF):
        rows = [x for x in self._rows() if _num(x.get("value")) > 0]
        total = sum(_num(x.get("value")) for x in rows)
        # The web draws a fixed o.height||150 square and lets the legend
        # wrap beside it; the size must NOT shrink with the container.
        size = float(self.o.get("height") or _DEFAULT_HEIGHT["donut"])
        cx, cy = r.center().x(), r.top() + size / 2
        rad = size / 2 - 6
        rin = rad * 0.58
        start = 90.0 * 16                       # 12 o'clock, like the web's -PI/2
        for i, row in enumerate(rows):
            frac = _num(row.get("value")) / total if total else 0.0
            span = -frac * 360 * 16 * self._progress
            path = QPainterPath()
            outer = QRectF(cx - rad, cy - rad, rad * 2, rad * 2)
            inner = QRectF(cx - rin, cy - rin, rin * 2, rin * 2)
            path.arcMoveTo(outer, start / 16)
            path.arcTo(outer, start / 16, span / 16)
            path.arcTo(inner, (start + span) / 16, -span / 16)
            path.closeSubpath()
            p.setPen(QPen(qcolor(WEB["bc"]), 2))
            p.setBrush(QBrush(qcolor(tone_color(row.get("tone"), i))))
            p.drawPath(path)
            pct = round(frac * 100)
            self._hit.append((outer,
                              f"{row.get('label')} · {_fmt(_num(row.get('value')))} ({pct}%)"))
            start += span
        center = self.o.get("center")
        if center is not None:
            f = QFont(pick_font("disp"), max(8, int(size * 0.10)))
            f.setBold(True)
            p.setFont(f)
            p.setPen(qcolor(WEB["ink"]))
            p.drawText(QRectF(cx - rin, cy - rin, rin * 2, rin * 2),
                       Qt.AlignmentFlag.AlignCenter, str(center))

    def _paint_gauge(self, p: QPainter, r: QRectF):
        value = _num(self.o.get("value"))
        mx = self.o.get("max")
        mx = _num(mx) if mx is not None else 100.0
        frac = 1.0 if self.o.get("unlimited") else (
            max(0.0, min(1.0, value / mx)) if mx else 0.0)
        W = r.width()
        H = min(r.height(), float(self.o.get("height") or _DEFAULT_HEIGHT["gauge"]))
        rad = H / 2
        p.setPen(QPen(qcolor(WEB["bc"]), 2))              # .cx-gauge-track
        p.setBrush(QBrush(qcolor(WEB["bg"])))
        p.drawRoundedRect(QRectF(1, r.top() + 1, W - 2, H - 2), rad, rad)
        fw = frac * (W - 2) * self._progress
        if fw > 0:
            p.setPen(Qt.PenStyle.NoPen)
            tone = self.o.get("tone")
            if tone:
                p.setBrush(QBrush(qcolor(tone_color(tone))))
            else:                                   # the web's fox→fox2 gradient
                grad = QLinearGradient(1, 0, W - 1, 0)
                grad.setColorAt(0.0, qcolor(WEB["fox"]))
                grad.setColorAt(1.0, qcolor(WEB["fox2"]))
                p.setBrush(QBrush(grad))
            p.drawRoundedRect(QRectF(1, r.top() + 1, fw, H - 2), rad, rad)
        label = self.o.get("tip") or self.o.get("label")
        if label:
            self._hit.append((QRectF(0, r.top(), W, H), str(label)))

    def _paint_legend(self, p: QPainter, r: QRectF):
        if self.type == "donut":
            items = [(str(x.get("label")), tone_color(x.get("tone"), i),
                      _fmt(_num(x.get("value"))))
                     for i, x in enumerate(self._rows())
                     if _num(x.get("value")) > 0]
        else:
            _, series = self._norm()
            items = [(s.get("name") or f"Series {i + 1}",
                      tone_color(s.get("tone"), i), None)
                     for i, s in enumerate(series)]
        f = QFont(pick_font("mono"), 7)
        f.setBold(True)
        p.setFont(f)
        fm = QFontMetrics(f)
        x = 0.0
        for name, colour, value in items:
            text = f"{name} · {value}" if value is not None else name
            tw = fm.horizontalAdvance(text)
            if x + tw + 18 > r.width() and x > 0:
                break                       # one row; the rest stay in the a11y text
            p.setPen(QPen(qcolor(WEB["bc"]), 1.5))        # .cx-legend i border
            p.setBrush(QBrush(qcolor(colour)))
            p.drawRoundedRect(QRectF(x, r.top() + 7, 8, 8), 2, 2)
            p.setPen(qcolor(WEB["muted"]))
            p.drawText(QRectF(x + 12, r.top(), tw + 4, r.height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            x += tw + 26

    # ── interaction ─────────────────────────────────────────────────────
    def mouseMoveEvent(self, event):
        pos = event.position()
        for rect, text in self._hit:
            if rect.contains(pos):
                QToolTip.showText(event.globalPosition().toPoint(), text, self)
                return
        QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        QToolTip.hideText()
        super().leaveEvent(event)

    def resizeEvent(self, event):
        # Geometry is derived from the widget width at paint time, so a resize
        # only needs a repaint — but the hit map must be rebuilt with it.
        self._hit = []
        super().resizeEvent(event)


# ── standalone preview ──────────────────────────────────────────────────────
if __name__ == "__main__":
    from PyQt6.QtWidgets import QApplication, QGridLayout, QLabel, QWidget as QW

    app = QApplication(sys.argv)
    win = QW()
    win.setStyleSheet(f"background: {WEB['bg']}; color: {WEB['ink']};")
    grid = QGridLayout(win)

    demos = [
        ("bar", FoxChart("bar", data=[{"label": "Mon", "value": 12},
                                      {"label": "Tue", "value": 31, "tone": "bad"},
                                      {"label": "Wed", "value": 22}])),
        ("hbar", FoxChart("hbar", data=[{"label": "hipaa_basic", "value": 9},
                                        {"label": "soc2", "value": 4}])),
        ("area", FoxChart("area", labels=["1", "2", "3", "4", "5"],
                          series=[{"name": "logs", "values": [3, 9, 5, 12, 8]}])),
        ("stacked", FoxChart("stacked", legend=True, labels=["7d", "30d"],
                             series=[{"name": "High", "tone": "bad", "values": [3, 6]},
                                     {"name": "Low", "tone": "warn", "values": [5, 9]}])),
        ("donut", FoxChart("donut", center="41",
                           data=[{"label": "graded", "value": 33, "tone": "ok"},
                                 {"label": "pending", "value": 8, "tone": "warn"}])),
        ("gauge", FoxChart("gauge", value=72, max=100)),
        ("sparkline", FoxChart("sparkline", data=[{"label": str(i), "value": v}
                                                  for i, v in enumerate([2, 6, 3, 9, 7])])),
        ("empty", FoxChart("bar", data=[],
                           empty={"title": "No data yet",
                                  "desc": "Run the SDK to capture events."})),
    ]
    for i, (name, chart) in enumerate(demos):
        grid.addWidget(QLabel(name), (i // 2) * 2, i % 2)
        grid.addWidget(chart, (i // 2) * 2 + 1, i % 2)
    win.resize(720, 900)
    win.show()
    sys.exit(app.exec())
