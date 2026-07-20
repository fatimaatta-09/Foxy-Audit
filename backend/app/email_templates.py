"""Branded transactional email templates (dashboard P17).

One shared render helper produces the (html, text) pair for all transactional emails — a dark
fox-orange header band → white body → footer. Table-based, inline-styled, ~600px, Gmail/Outlook-safe
(bulletproof VML button), with a hidden preheader and a MATCHING plaintext part.

Every component ESCAPES its text at the boundary, so injected names / bodies / lead content can never
break the HTML. There is intentionally no raw-HTML escape hatch — our own copy is plain strings too.
"""

from __future__ import annotations

import html as _html

FOX = "#ff7a2e"
HEADER_BG = "#1a1410"
INK = "#241d16"
MUTED = "#8c8174"
PAGE_BG = "#f4f1ec"
CARD_BORDER = "#e7ddd0"
FOOTER_BG = "#faf7f2"
LOGO_URL = "https://foxyaudit.tech/logo.png"
FONT = "'Helvetica Neue',Helvetica,Arial,sans-serif"


def _esc(s) -> str:
    return _html.escape(str(s if s is not None else ""))


# ── components — each returns {"h": html_fragment (a <tr>), "t": plaintext} ──
def heading(text) -> dict:
    return {"h": f'<tr><td style="padding:0 0 8px;font-family:{FONT};font-size:20px;font-weight:700;'
                 f'color:{INK};line-height:1.25;">{_esc(text)}</td></tr>',
            "t": str(text)}


def paragraph(text) -> dict:
    return {"h": f'<tr><td style="padding:6px 0;font-family:{FONT};font-size:14px;color:{INK};'
                 f'line-height:1.6;">{_esc(text)}</td></tr>',
            "t": str(text)}


def muted(text) -> dict:
    return {"h": f'<tr><td style="padding:6px 0;font-family:{FONT};font-size:12px;color:{MUTED};'
                 f'line-height:1.55;">{_esc(text)}</td></tr>',
            "t": str(text)}


def code_block(code) -> dict:
    return {"h": f'<tr><td style="padding:14px 0;"><table role="presentation" width="100%" '
                 f'cellpadding="0" cellspacing="0"><tr><td align="center" style="background:#f4efe8;'
                 f'border:1px solid {CARD_BORDER};border-radius:12px;padding:18px;'
                 f'font-family:\'Courier New\',monospace;font-size:30px;font-weight:700;'
                 f'letter-spacing:6px;color:{INK};">{_esc(code)}</td></tr></table></td></tr>',
            "t": f"    {code}"}


def button(label, url) -> dict:
    u, l = _esc(url), _esc(label)
    h = (f'<tr><td style="padding:16px 0 8px;">'
         f'<table role="presentation" cellpadding="0" cellspacing="0"><tr>'
         f'<td align="center" bgcolor="{FOX}" style="border-radius:10px;">'
         f'<!--[if mso]><v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" '
         f'xmlns:w="urn:schemas-microsoft-com:office:word" href="{u}" '
         f'style="height:44px;v-text-anchor:middle;width:260px;" arcsize="22%" fillcolor="{FOX}" '
         f'stroke="f"><w:anchorlock/><center style="color:#1a0900;font-family:{FONT};font-size:14px;'
         f'font-weight:700;">{l}</center></v:roundrect><![endif]-->'
         f'<a href="{u}" style="display:inline-block;padding:12px 26px;font-family:{FONT};'
         f'font-size:14px;font-weight:700;color:#1a0900;text-decoration:none;border-radius:10px;'
         f'mso-hide:all;">{l}</a></td></tr></table></td></tr>')
    return {"h": h, "t": f"{label}: {url}"}


def callout(text, tone="info") -> dict:
    palette = {"info": ("#f4efe8", MUTED), "warn": ("#fff6e6", "#7a5a10"),
               "bad": ("#fdecec", "#8a1f1f"), "ok": ("#e9f8ef", "#0f5c2e")}
    bg, fg = palette.get(tone, palette["info"])
    return {"h": f'<tr><td style="padding:12px 0;"><table role="presentation" width="100%"><tr>'
                 f'<td style="background:{bg};border-radius:10px;padding:12px 14px;font-family:{FONT};'
                 f'font-size:13px;color:{fg};line-height:1.55;">{_esc(text)}</td></tr></table></td></tr>',
            "t": f"[{tone}] {text}"}


def info_rows(rows) -> dict:
    trs = "".join(
        f'<tr><td style="padding:5px 0;font-family:{FONT};font-size:12px;color:{MUTED};">{_esc(k)}</td>'
        f'<td align="right" style="padding:5px 0;font-family:{FONT};font-size:12px;color:{INK};'
        f'font-weight:600;">{_esc(v)}</td></tr>' for k, v in rows)
    return {"h": f'<tr><td style="padding:8px 0;"><table role="presentation" width="100%">{trs}'
                 f'</table></td></tr>',
            "t": "\n".join(f"{k}: {v}" for k, v in rows)}


def divider() -> dict:
    return {"h": f'<tr><td style="padding:10px 0;"><div style="border-top:1px solid {CARD_BORDER};'
                 f'font-size:0;line-height:0;">&nbsp;</div></td></tr>',
            "t": "----"}


def layout(*, title: str, preheader: str, blocks: list, cta: dict | None = None,
           surface: str = "customer", variant: str = "full") -> tuple[str, str]:
    """Assemble components into the branded shell. Returns (html, text).

    blocks: list of component dicts. cta: optional {"label", "url"} rendered as a button after blocks.
    surface: "customer" | "staff" (footer tagline). variant: "full" | "compact" (body padding)."""
    blocks = list(blocks)
    if cta:
        blocks.append(button(cta["label"], cta["url"]))
    body_h = "".join(b["h"] for b in blocks)
    body_t = "\n\n".join(b["t"] for b in blocks if b.get("t"))
    tagline = "Internal ops" if surface == "staff" else "Compliance & audit"
    pad = "20px 26px" if variant == "compact" else "30px 28px"
    html = (
        f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="x-apple-disable-message-reformatting"><title>{_esc(title)}</title></head>'
        f'<body style="margin:0;padding:0;background:{PAGE_BG};">'
        f'<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;opacity:0;">{_esc(preheader)}</div>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{PAGE_BG};">'
        f'<tr><td align="center" style="padding:26px 12px;">'
        f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        f'style="width:100%;max-width:600px;background:#ffffff;border:1px solid {CARD_BORDER};'
        f'border-radius:14px;overflow:hidden;">'
        f'<tr><td style="background:{HEADER_BG};padding:20px 26px;">'
        f'<table role="presentation" width="100%"><tr>'
        f'<td style="font-family:{FONT};"><img src="{LOGO_URL}" width="26" height="26" alt="Foxy Audit" '
        f'style="vertical-align:middle;border:0;border-radius:7px;">&nbsp;'
        f'<span style="color:#ffffff;font-weight:700;font-size:17px;vertical-align:middle;">'
        f'Foxy&nbsp;<span style="color:{FOX};">Audit</span></span></td>'
        f'<td align="right" style="font-family:{FONT};color:#7c7267;font-size:11px;'
        f'text-transform:uppercase;letter-spacing:1px;">{_esc(tagline)}</td></tr></table></td></tr>'
        f'<tr><td style="padding:{pad};"><table role="presentation" width="100%">{body_h}</table></td></tr>'
        f'<tr><td style="background:{FOOTER_BG};border-top:1px solid {CARD_BORDER};padding:18px 26px;'
        f'font-family:{FONT};color:{MUTED};font-size:11px;line-height:1.6;">'
        f'Foxy Audit — tamper-evident audit trails for AI.<br>'
        f'<a href="https://foxyaudit.tech" style="color:{FOX};text-decoration:none;">foxyaudit.tech</a>'
        f'&nbsp;·&nbsp; This is a transactional message about your account.'
        f'</td></tr></table></td></tr></table></body></html>'
    )
    text = (f"{title}\n\n{body_t}\n\n—\nFoxy Audit · foxyaudit.tech\n"
            f"This is a transactional message about your account.")
    return html, text
