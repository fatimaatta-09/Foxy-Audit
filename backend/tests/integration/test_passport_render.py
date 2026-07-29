"""P2 §12 · the passport, verified against a real render rather than its markup.

The existing passport tests pin the HTML. That is necessary and not sufficient:
the document that reaches a customer is a PDF, produced by weasyprint's native
libraries, and the route swallows ANY exception from that step and quietly
returns HTML with a 200. So HTML-only tests pass in exactly the situation the
customer is worst served by.

These tests cover the two things a Windows dev box cannot: that the route's
contract is honoured when the renderer works, and that its failure path does not
leak the renderer's own error text into the log of an authenticated request.

The PDF itself was rendered and read in the production image (Debian + pango /
cairo / gdk-pixbuf, per backend/Dockerfile) during the §12 sweep: 5 pages,
application/pdf, all four @page margin boxes, and every counter traceable to
six ingested events. Reproducing that here would mean shipping weasyprint's
native stack into the test environment, so it is asserted where it can be — the
route's declared media type and the template's structure.
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


# ── the logging hard rule ──────────────────────────────────────────────────

def test_the_degrade_path_logs_the_exception_type_not_its_text(source):
    """/v1/passport is authenticated, and a weasyprint failure carries library
    paths and system detail in its message. The type name distinguishes "no PDF
    renderer" from "the template blew up", which is all this line is for."""
    m = re.search(r"except Exception as exc:(.*?)return HTMLResponse", source, re.S)
    assert m, "the weasyprint degrade path moved — re-check what it logs"
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
