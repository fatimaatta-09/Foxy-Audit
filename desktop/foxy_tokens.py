"""
Foxy Audit desktop — the ONE design-token / QSS / font / resource source.

Every desktop surface (auditor console, chat popup, settings dialog, menus,
fox pet) pulls its colors, fonts, shadows, icons and asset paths from here.
There is no theme toggle and no skin picker: the desktop look is locked to the
web dashboard's dark "original" skin.

`WEB` is a byte-for-byte port of the web dashboard's design tokens
(foxy-dashboard/foxy-audit-premium.html `:root`, lines 24-94).  The colours
must never drift from the site — if the site's `:root` changes, this dict is
re-ported, not hand-tuned.  The one permitted deviation from the web is
typography: the desktop keeps its bundled Unbounded (display) + Space Mono
(mono) faces instead of the site's Poppins / JetBrains Mono.

The `clay_tokens()` / `glass_tokens()` / `matte_tokens()` builders are the
existing per-window token sets (console / chat / settings), moved here
verbatim from dashboard.py, clay_chat_popup.py and settings_dialog.py so each
exists exactly once.  Later phases collapse them onto `WEB`; for now they are
kept byte-identical so nothing re-renders differently.
"""

from __future__ import annotations

import os
import sys

from PyQt6.QtCore import Qt, QPoint, QPointF, QRectF
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QFontDatabase, QPainter, QPainterPath, QPen, QPolygonF,
)
from PyQt6.QtWidgets import QGraphicsDropShadowEffect


# ── Asset resolution ────────────────────────────────────────────────────────
def resource_path(relative_path: str) -> str:
    """Resolve a bundled asset from source OR a PyInstaller onefile build
    (which extracts data files under sys._MEIPASS).  Import this everywhere —
    never build asset paths from __file__ in another module."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


# ── WEB — byte-for-byte the web dashboard :root (dark "original" skin) ──────
WEB: dict[str, str] = {
    # deep clay base — not cream, not pure black
    "bg":      "#0e0c0a",
    "bg2":     "#161310",
    "surf":    "#1c1815",
    "surf2":   "#221d18",
    "surf3":   "#2a241d",
    "line":    "#322b23",

    "ink":     "#f7f1e8",
    "ink2":    "#cdc2b3",
    "muted":   "#8c8174",
    "muted2":  "#5f564a",

    # fox / paprika
    "fox":     "#ff7a2e",
    "fox2":    "#ff9d52",
    "fox3":    "#d65a16",
    "foxdeep": "#8a3611",

    # status — bg colored, text always pure black or white
    "safe_bg":   "#3ddc84",
    "safe_tx":   "#07210f",
    "breach_bg": "#ff4d4d",
    "breach_tx": "#2a0303",
    "warn_bg":   "#ffc83d",
    "warn_tx":   "#2a1d00",

    # border colour (widths below) + the shown-once secret input paper
    "bc":    "#000",
    "paper": "#fff",
}

# categorical chart palette — web --c-1..--c-6 (c1=fox, c3=safe-bg, c5=warn-bg)
CHART: tuple[str, ...] = (
    WEB["fox"], "#5b8cff", WEB["safe_bg"], "#ff6aa8", WEB["warn_bg"], "#9b8cff",
)

# 8-pt spacing scale — web --s-1..--s-8
S1, S2, S3, S4, S5, S6, S7, S8 = 4, 8, 12, 16, 24, 32, 48, 64

# radii — web --r-xl/--r-lg clamp with the viewport; desktop windows sit at the
# large end, so the clamp maxima are the desktop values
RADIUS: dict[str, int] = {"xl": 34, "lg": 26, "md": 16, "sm": 11}

# border widths (px) over WEB["bc"] — web --border-xs/-sm/default/-lg
BORDER_XS, BORDER_SM, BORDER_MD, BORDER_LG = 1.5, 2, 2.5, 3

# clay elevation — hard offset shadows, (dx, dy, black-alpha 0-1):
# web --clay / --clay-lg / --clay-sm / --clay-press
CLAY_SHADOW       = (9, 9, 0.55)
CLAY_SHADOW_LG    = (12, 12, 0.55)
CLAY_SHADOW_SM    = (5, 5, 0.5)
CLAY_SHADOW_PRESS = (3, 3, 0.5)

# Status palette — fixed & theme-independent so danger always reads as danger.
# Same values as WEB safe/warn/breach + chart c-2; the names are the ones the
# console has always used.
OK_GREEN   = WEB["safe_bg"]     # #3ddc84
WARN_AMBER = WEB["warn_bg"]     # #ffc83d
BAD_RED    = WEB["breach_bg"]   # #ff4d4d
INFO_BLUE  = CHART[1]           # #5b8cff
DARK_TX    = "#160d08"          # near-black ink used on the coloured pills

# The console's base palette — 1:1 with the website :root (subset of WEB,
# keyed the way dashboard.py has always consumed it).
CLAY: dict[str, str] = {
    "bg": WEB["bg"], "bg2": WEB["bg2"], "surf": WEB["surf"], "surf2": WEB["surf2"],
    "surf3": WEB["surf3"], "line": WEB["line"],
    "ink": WEB["ink"], "ink2": WEB["ink2"], "muted": WEB["muted"], "muted2": WEB["muted2"],
    "fox": WEB["fox"], "fox2": WEB["fox2"], "fox3": WEB["fox3"], "foxdeep": WEB["foxdeep"],
}

# near-black frosted-glass backdrop for the chat panel — a calm hint of
# transparency, readable
PANEL_WASH = ("qlineargradient(x1:0, y1:0, x2:1, y2:1,"
              " stop:0 rgba(11,11,13,240), stop:0.5 rgba(15,15,18,240),"
              " stop:1 rgba(8,8,10,240))")


# ── Fonts (bundled Unbounded / Space Mono) ──────────────────────────────────
_FONT_CACHE: dict[str, str] = {}
_FONTS_REGISTERED = False


def register_bundled_fonts():
    """Load the bundled Unbounded / Space Mono TTFs so every window uses the
    brand faces instead of a generic system fallback.  Looks in the packaged
    location (desktop/fonts inside a PyInstaller bundle) first, then the
    repo-root fonts/ dir when running from a source checkout."""
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    _FONTS_REGISTERED = True
    here = os.path.dirname(os.path.abspath(__file__))
    for font_dir in (resource_path("fonts"), os.path.join(here, os.pardir, "fonts")):
        try:
            for fn in os.listdir(font_dir):
                if fn.lower().endswith((".ttf", ".otf")):
                    QFontDatabase.addApplicationFont(os.path.join(font_dir, fn))
            return
        except OSError:
            continue


def pick_font(kind: str) -> str:
    """Brand fonts (bundled Unbounded / Space Mono) with sane fallbacks.
    kind: "disp" for display text, anything else for monospace."""
    if kind in _FONT_CACHE:
        return _FONT_CACHE[kind]
    register_bundled_fonts()
    try:
        fams = set(QFontDatabase.families())
    except Exception:
        fams = set()
    if kind == "disp":
        cands = ["Unbounded", "Nunito", "Poppins", "Montserrat",
                 "Segoe UI Variable Display", "Segoe UI Semibold", "Segoe UI"]
        default = "Segoe UI"
    else:
        cands = ["Space Mono", "Cascadia Mono", "JetBrains Mono",
                 "Consolas", "Courier New"]
        default = "Consolas"
    pick = next((c for c in cands if c in fams), default)
    _FONT_CACHE[kind] = pick
    return pick


# ── Motion ──────────────────────────────────────────────────────────────────
def reduced_motion() -> bool:
    """True when the OS asks for reduced motion.

    Qt exposes no cross-platform hint, so read the real setting where we can:
    on Windows SPI_GETCLIENTAREAANIMATION (0x1042) is what the "Show animations
    in Windows" toggle writes. Elsewhere assume motion is fine rather than
    guess. It lives here so every surface answers the question identically."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        enabled = ctypes.c_int()
        if ctypes.windll.user32.SystemParametersInfoW(
                0x1042, 0, ctypes.byref(enabled), 0):
            return not bool(enabled.value)
    except Exception:
        pass
    return False


# ── Colour helpers ──────────────────────────────────────────────────────────
def is_dark(color_str: str, threshold: int = 128) -> bool:
    """Crude luminance check — True for colours darker than the threshold.
    The settings dialog historically used threshold=140, the console 128;
    pass the threshold explicitly so each keeps its exact contrast picks."""
    s = color_str.strip().lstrip("#")
    if len(s) == 6:
        try:
            r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
            return (r * 299 + g * 587 + b * 114) / 1000 < threshold
        except ValueError:
            pass
    return False


def with_alpha(color: str, alpha: int) -> str:
    s = color.strip()
    if s.startswith("#") and len(s) == 7:
        r, g, b = int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16)
        return f"rgba({r},{g},{b},{alpha})"
    return s


def qcolor(color: str, fallback=(136, 136, 136)) -> QColor:
    s = color.strip()
    if s.startswith("#") and len(s) == 7:
        return QColor(int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16))
    c = QColor(s)
    return c if c.isValid() else QColor(*fallback)


def mix(a: str, b: str, t: float) -> str:
    """Linear blend of two #RRGGBB colours, t in [0,1]."""
    ca, cb = qcolor(a), qcolor(b)
    r = int(ca.red() * (1 - t) + cb.red() * t)
    g = int(ca.green() * (1 - t) + cb.green() * t)
    bl = int(ca.blue() * (1 - t) + cb.blue() * t)
    return f"#{r:02X}{g:02X}{bl:02X}"


def hairline(tokens: dict, alpha: int = 38) -> str:
    return with_alpha(tokens.get("text_muted", "#888"), alpha)


# ── Shadow helpers ──────────────────────────────────────────────────────────
def glass_shadow(widget, dy: int = 9, blur: int = 26, alpha: int = 90):
    """Soft ambient shadow that lifts a frosted-glass panel off the backdrop."""
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setOffset(0, dy)
    eff.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(eff)


def make_shadow(tokens: dict) -> QGraphicsDropShadowEffect:
    """Build a QGraphicsDropShadowEffect from token shadow_* keys."""
    eff = QGraphicsDropShadowEffect()
    eff.setBlurRadius(tokens.get("shadow_blur", 20))
    eff.setOffset(tokens.get("shadow_dx", 0), tokens.get("shadow_dy", 8))
    r, g, b = tokens.get("shadow_color", (60, 40, 20))
    eff.setColor(QColor(r, g, b, tokens.get("shadow_alpha", 80)))
    return eff


# ── Per-window token sets (moved here verbatim — one copy each) ─────────────
def clay_tokens() -> dict:
    """The auditor console's design tokens.  The base palette (bg / accent /
    status) is the original clay/paprika scheme — unchanged — but the surfaces
    are *glassmorphic*: translucent white frosting (rgba whites), bright 1px
    rims and soft shadows are layered over that palette.  Qt can't do CSS
    backdrop-filter, so 'glass' here = translucency + rim-light + soft shadow
    over the dark base.

    Alpha values are 0-255 (Qt stylesheet convention)."""
    return {
        "bg": CLAY["bg"], "bg2": CLAY["bg2"],
        "panel": CLAY["surf"], "panel2": CLAY["surf2"], "panel3": CLAY["surf3"],
        "line": CLAY["line"],
        "text": CLAY["ink"], "text2": CLAY["ink2"],
        "text_muted": CLAY["muted"], "text_muted2": CLAY["muted2"],
        "accent": CLAY["fox"], "accent2": CLAY["fox2"], "accent3": CLAY["fox3"],
        # ── glass layer (frosted-white over the dark palette) ──
        "glass_hi":  "rgba(255,255,255,34)",   # top sheen of a panel
        "glass_lo":  "rgba(255,255,255,10)",   # bottom of a panel
        "glass_brd": "rgba(255,255,255,50)",   # bright 1px rim
        "glass_brd_soft": "rgba(255,255,255,28)",
        "glass_str_hi": "rgba(255,255,255,60)",  # raised glass (nav active / button)
        "glass_str_lo": "rgba(255,255,255,24)",
        "glass_fill": "rgba(255,255,255,16)",   # flat translucent fill
        "font": pick_font("disp"), "font_mono": pick_font("mono"),
    }


def glass_tokens() -> dict:
    """Fixed frosted-glass skin for the chat popup — mirrors the console, so
    any chat theme passed in is ignored (the copilot always reads as branded)."""
    return {
        "bg": "#11101a", "panel": "rgba(176,174,255,20)",
        "radius": 22, "border": "1px solid rgba(190,186,255,16)",
        "accent": "#c96a2f", "accent_dark": "#a8551f",
        "text": WEB["ink"], "text_muted": WEB["muted"],
        "font": pick_font("disp"), "font_mono": pick_font("mono"),
        "bubble_user": "#c96a2f", "bubble_fox": "rgba(176,174,255,22)",
        "header_bg": "transparent", "header_text": WEB["ink"],
        "input_border": "1px solid rgba(190,186,255,16)", "outline_focus": "#c96a2f",
        "shadow_blur": 30, "shadow_dx": 0, "shadow_dy": 12,
        "shadow_color": (0, 0, 0), "shadow_alpha": 110,
    }


def matte_tokens() -> dict:
    """Fixed matte skin for the settings dialog — mirrors the auditor console
    and chat popup so the whole app reads as one branded surface.  There is no
    theme switching; the look is constant."""
    return {
        "bg": "#0c0c0e", "radius": 16,
        "accent": "#c96a2f", "accent_dark": "#a8551f",
        "panel": "rgba(255,255,255,16)",
        "text": "#f4efe8", "text_muted": "#9b9aae",
        "font": pick_font("disp"), "font_disp": pick_font("disp"),
        "header_bg": "rgba(255,255,255,10)", "header_text": "#f4efe8",
        "border": "2px solid rgba(255,255,255,55)",
        "input_border": "1px solid rgba(255,255,255,45)",
        "outline_focus": "#c96a2f",
        "shadow_blur": 34, "shadow_dx": 0, "shadow_dy": 14,
        "shadow_color": (0, 0, 0), "shadow_alpha": 150,
    }


# ── QSS builders (per-window stylesheet blobs, one home) ────────────────────
def matte_menu_qss() -> str:
    """Stylesheet for the tray + right-click menus, in the app's fixed matte
    skin: dark base, lavender frost rim, matte-orange selection, and the
    Unbounded brand display font (same wordmark used on the dashboard/chat)."""
    disp = pick_font("disp")
    return f"""
        QMenu {{
            background-color: #17131f;
            color: #f7f1e8;
            border: 1px solid rgba(190,186,255,30);
            border-radius: 16px;
            padding: 7px;
            font-family: '{disp}';
            font-size: 14px;
            font-weight: 500;
        }}
        QMenu::item {{
            padding: 11px 40px 11px 20px;
            border-radius: 11px;
            margin: 3px 5px;
        }}
        QMenu::item:selected {{
            background-color: #c96a2f;
            color: #ffffff;
        }}
        QMenu::item:disabled {{ color: #6b6675; }}
        QMenu::separator {{
            height: 1px;
            background: rgba(190,186,255,22);
            margin: 6px 12px;
        }}
        QMenu::right-arrow {{ width: 12px; height: 12px; margin-right: 8px; }}
    """


def console_shell_qss(t: dict, acrylic: bool = False) -> str:
    """The auditor console's window-level stylesheet (shell, sidebar, topbar,
    page typography, hero/verification cards, buttons).  Moved verbatim from
    DashboardWindow.apply_theme — the rendered output is unchanged."""
    font = t.get("font", "Segoe UI")
    mono = t.get("font_mono", "Consolas")
    # When real acrylic blur is active the shell is a translucent veil so the
    # frosted blur of the backdrop shows through; otherwise it's an opaque
    # paprika backdrop (so the window is never see-through to the raw desktop).
    if acrylic:
        shell_bg = ("qradialgradient(cx:0.12, cy:0.0, radius:1.25, fx:0.12, fy:0.0,"
                    " stop:0 rgba(42,26,17,150), stop:0.5 rgba(16,13,10,125),"
                    " stop:1 rgba(11,10,9,125))")
    else:
        # colourful wash so the frosted panels have real colour to frost over
        shell_bg = ("qlineargradient(x1:0, y1:0, x2:1, y2:1,"
                    " stop:0 #6a3514, stop:0.30 #15100c, stop:0.52 #122a5e,"
                    " stop:0.74 #1c0e26, stop:1 #461630)")
    return f"""
            QFrame#shell {{
                background: {shell_bg};
                border: 1px solid rgba(255,255,255,22);
                border-radius: 22px; }}
            QFrame#sidebar {{
                background: rgba(190,186,255,11);
                border: none;
                border-right: 1px solid rgba(200,196,255,16);
                border-top-left-radius: 22px; border-bottom-left-radius: 22px; }}
            QFrame#topbar {{
                background: transparent;
                border-bottom: 1px solid rgba(255,255,255,12); }}
            QLabel#brandName {{ color: {t['text']}; font-size: 15px; font-weight: 800;
                background: transparent; }}
            QLabel#brandSub {{ color: {t['text_muted']}; font-family: '{mono}';
                font-size: 8px; font-weight: 700; letter-spacing: 1.4px;
                background: transparent; }}
            QLabel#logo {{ background: transparent; }}
            QLabel#navMeta {{ color: {t['text_muted']}; font-family: '{mono}';
                font-size: 8px; font-weight: 700; letter-spacing: 1.2px;
                background: transparent; }}
            QLabel#navMetaVal {{ color: {t['text']}; font-size: 12px; font-weight: 700;
                font-family: '{mono}'; background: transparent; }}
            QPushButton#navAuthBtn {{
                background: rgba(255,255,255,16); color: {t['text']};
                border: 1px solid rgba(255,255,255,40); border-radius: 10px;
                padding: 7px 10px; font-size: 11.5px; font-weight: 700;
                margin-top: 8px; }}
            QPushButton#navAuthBtn:hover {{ background: rgba(255,255,255,32); }}
            QPushButton#navAuthBtn:focus {{ border: 1px solid {t['accent']}; }}
            QLabel#pageTitle {{ color: {t['text']}; font-size: 18px; font-weight: 800;
                letter-spacing: -0.5px; background: transparent; }}
            QLabel#emptyState {{ color: {t['text_muted']}; font-size: 12px;
                padding: 16px 4px; background: transparent; }}
            QLabel#sectionCap {{ color: {t['text_muted']}; font-family: '{mono}';
                font-size: 9px; font-weight: 700; letter-spacing: 1.2px;
                background: transparent; }}
            QFrame#verifCard {{
                background: qlineargradient(x1:0, y1:0, x2:0.55, y2:1,
                    stop:0 #c96a2f, stop:0.6 #a4521d, stop:1 #76360f);
                border: 1px solid rgba(255,255,255,16);
                border-radius: 20px; }}
            QLabel#verifFox {{ background: transparent; border: none; }}
            QLabel#verifHint {{ color: #ffffff; background: rgba(0,0,0,170);
                font-family: '{mono}'; font-size: 9px; font-weight: 700;
                border-radius: 11px; padding: 4px 11px; }}
            QLabel#verifEye {{ color: rgba(26,9,0,200); font-family: '{mono}';
                font-size: 10px; font-weight: 700; letter-spacing: 1px;
                background: transparent; }}
            QLabel#verifNum {{ color: #1a0900; font-size: 27px; font-weight: 800;
                letter-spacing: -1px; background: transparent; }}
            QLabel#verifBottom {{ color: rgba(26,9,0,205); font-family: '{mono}';
                font-size: 10px; font-weight: 700; letter-spacing: 1px;
                background: transparent; }}
            QLabel#chainState {{ color: {OK_GREEN}; font-size: 16px; font-weight: 800;
                letter-spacing: 0.5px; background: transparent; }}
            QLabel#chainMeta {{ color: {t['text2']}; font-size: 12px; background: transparent; }}
            QLabel#chainHash {{ color: {t['text_muted']}; font-size: 10px;
                font-family: '{mono}'; background: transparent; }}
            QLabel#tableCap {{ color: {t['text']}; font-size: 12px; font-weight: 800;
                letter-spacing: 1px; background: transparent; }}
            QLabel#tableCount {{ color: {t['text_muted']}; font-family: '{mono}';
                font-size: 10px; background: transparent; }}
            QLabel#connState {{ color: {t['text']}; font-size: 14px; font-weight: 700;
                background: transparent; }}
            QLabel#connUrl {{ color: {t['text_muted']}; font-size: 11px;
                font-family: '{mono}'; background: transparent; }}
            QPushButton#verifyBtn {{
                background: #c96a2f;
                color: #ffffff;
                border: 1px solid rgba(255,255,255,14);
                border-radius: 11px; padding: 9px 0; font-size: 12px; font-weight: 800; }}
            QPushButton#verifyBtn:hover {{ background: #d6743a; }}
            QPushButton#verifyBtn:pressed {{ background: #a8551f; }}
            QLabel#eyebrow {{ color: {t['accent2']}; font-family: '{mono}';
                font-size: 9px; font-weight: 800; letter-spacing: 1.6px;
                background: transparent; }}
            QLabel#h1Head {{ color: {t['text']}; font-size: 26px; font-weight: 800;
                letter-spacing: -1px; background: transparent; }}
            QLabel#subHead {{ color: {t['text_muted']}; font-size: 12px;
                background: transparent; }}
            QPushButton#ctaBtn {{ background: #c96a2f; color: #ffffff;
                border: 1px solid rgba(255,255,255,14); border-radius: 12px; padding: 9px 16px;
                font-size: 12px; font-weight: 800; }}
            QPushButton#ctaBtn:hover {{ background: #d6743a; }}
            QFrame#hero {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #c96a2f, stop:0.62 #a4521d, stop:1 #6e3411);
                border: 1px solid rgba(255,255,255,16);
                border-radius: 24px; }}
            QLabel#heroTag {{ color: rgba(26,9,0,210); font-family: '{mono}';
                font-size: 10px; font-weight: 800; letter-spacing: 1px;
                background: transparent; }}
            QLabel#liveBadge {{ color: #ffffff; background: rgba(0,0,0,160);
                font-family: '{mono}'; font-size: 9px; font-weight: 800;
                border-radius: 10px; padding: 3px 10px; }}
            QLabel#heroNum {{ color: #1a0900; font-size: 46px; font-weight: 800;
                letter-spacing: -2px; background: transparent; }}
            QLabel#heroFoot {{ color: rgba(26,9,0,200); font-family: '{mono}';
                font-size: 11px; font-weight: 700; background: transparent; }}
            QPushButton#tileBlue, QPushButton#tilePink {{
                color: #ffffff; border: 1px solid rgba(255,255,255,15);
                border-radius: 20px; padding: 18px; text-align: left;
                font-size: 15px; font-weight: 800; }}
            QPushButton#tileBlue {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #4d6cba, stop:1 #39508f); }}
            QPushButton#tilePink {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #c25c88, stop:1 #9c3c64); }}
            QPushButton#tileBlue:hover, QPushButton#tilePink:hover {{
                border: 1px solid rgba(255,255,255,40); }}
            QWidget {{ font-family: '{font}'; }}
        """


# ── Minimal line-icon painter (replaces emoji throughout) ───────────────────
def paint_icon(p: QPainter, rect: QRectF, name: str, color: QColor,
               weight: float = 1.8):
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color, weight)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()

    if name == "overview":      # 2x2 grid
        s = w * 0.36
        gap = w * 0.10
        for cx in (x + w * 0.16, x + w * 0.16 + s + gap):
            for cy in (y + h * 0.16, y + h * 0.16 + s + gap):
                p.drawRoundedRect(QRectF(cx, cy, s, s), 2, 2)
    elif name == "analytics":
        p.drawLine(int(x + w * 0.25), int(y + h * 0.85), int(x + w * 0.25), int(y + h * 0.4))
        p.drawLine(int(x + w * 0.5), int(y + h * 0.85), int(x + w * 0.5), int(y + h * 0.2))
        p.drawLine(int(x + w * 0.75), int(y + h * 0.85), int(x + w * 0.75), int(y + h * 0.55))
        p.drawLine(int(x + w * 0.1), int(y + h * 0.85), int(x + w * 0.9), int(y + h * 0.85))
    elif name == "log":         # list rows
        for i in range(3):
            ly = y + h * (0.30 + i * 0.20)
            p.setBrush(QBrush(color))
            p.drawEllipse(QRectF(x + w * 0.14, ly - w * 0.035, w * 0.07, w * 0.07))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawLine(int(x + w * 0.32), int(ly), int(x + w * 0.84), int(ly))
    elif name == "system":      # sliders
        for i, knob in zip(range(3), (0.7, 0.35, 0.6)):
            ly = y + h * (0.30 + i * 0.20)
            p.drawLine(int(x + w * 0.14), int(ly), int(x + w * 0.84), int(ly))
            p.setBrush(QBrush(color))
            p.drawEllipse(QRectF(x + w * (0.14 + knob * 0.70) - w * 0.05,
                                 ly - w * 0.05, w * 0.10, w * 0.10))
            p.setBrush(Qt.BrushStyle.NoBrush)
    elif name == "shield":
        path = QPainterPath()
        path.moveTo(x + w * 0.5, y + h * 0.12)
        path.lineTo(x + w * 0.84, y + h * 0.26)
        path.lineTo(x + w * 0.84, y + h * 0.55)
        path.cubicTo(x + w * 0.84, y + h * 0.78, x + w * 0.66, y + h * 0.86,
                     x + w * 0.5, y + h * 0.92)
        path.cubicTo(x + w * 0.34, y + h * 0.86, x + w * 0.16, y + h * 0.78,
                     x + w * 0.16, y + h * 0.55)
        path.lineTo(x + w * 0.16, y + h * 0.26)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "link":        # chain links
        p.drawRoundedRect(QRectF(x + w * 0.12, y + h * 0.36, w * 0.44, h * 0.28),
                          h * 0.14, h * 0.14)
        p.drawRoundedRect(QRectF(x + w * 0.44, y + h * 0.36, w * 0.44, h * 0.28),
                          h * 0.14, h * 0.14)
    elif name == "refresh":
        p.drawArc(QRectF(x + w * 0.22, y + h * 0.22, w * 0.56, h * 0.56),
                  55 * 16, 250 * 16)
        p.setBrush(QBrush(color))
        ax, ay = x + w * 0.74, y + h * 0.26
        p.drawPolygon(QPoint(int(ax), int(ay - h * 0.05)),
                      QPoint(int(ax + w * 0.06), int(ay + h * 0.12)),
                      QPoint(int(ax - w * 0.12), int(ay + h * 0.06)))
    elif name == "close":
        p.drawLine(int(x + w * 0.3), int(y + h * 0.3),
                   int(x + w * 0.7), int(y + h * 0.7))
        p.drawLine(int(x + w * 0.7), int(y + h * 0.3),
                   int(x + w * 0.3), int(y + h * 0.7))
    elif name == "min":
        p.drawLine(int(x + w * 0.3), int(y + h * 0.6),
                   int(x + w * 0.7), int(y + h * 0.6))
    elif name == "key":         # sign-in / credentials (the web gate's 🔑, as vector)
        r = w * 0.19
        cy = y + h * 0.5
        p.drawEllipse(QRectF(x + w * 0.10, cy - r, r * 2, r * 2))
        p.drawLine(int(x + w * 0.10 + r * 2), int(cy), int(x + w * 0.88), int(cy))
        p.drawLine(int(x + w * 0.70), int(cy), int(x + w * 0.70), int(cy + h * 0.15))
        p.drawLine(int(x + w * 0.82), int(cy), int(x + w * 0.82), int(cy + h * 0.10))
    elif name == "mail":        # "check your email" step
        rect = QRectF(x + w * 0.12, y + h * 0.26, w * 0.76, h * 0.48)
        p.drawRoundedRect(rect, h * 0.07, h * 0.07)
        p.drawPolyline(QPolygonF([
            QPointF(rect.left() + w * 0.04, rect.top() + h * 0.04),
            QPointF(rect.center().x(), rect.center().y() + h * 0.07),
            QPointF(rect.right() - w * 0.04, rect.top() + h * 0.04)]))
    p.restore()
