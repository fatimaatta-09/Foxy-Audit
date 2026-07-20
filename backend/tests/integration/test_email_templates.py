"""Branded transactional email templates (dashboard P17) — pure render unit tests.

No DB / client needed; these exercise app.email_templates directly. The invariants that matter for
a transactional email: a real HTML doc + a matching non-empty plaintext part, codes/links present in
BOTH parts (so text-only clients still work), and EVERY caller-supplied value escaped at the boundary
so a hostile name / message / lead body can never break out into markup.
"""

from __future__ import annotations

from app import email_templates as et


def test_layout_returns_html_and_matching_text():
    html, text = et.layout(
        title="Hello", preheader="a short preheader",
        blocks=[et.paragraph("Body copy here.")])
    assert html.startswith("<!DOCTYPE html>") and "</html>" in html
    assert "Hello" in html and text.startswith("Hello")
    assert "Body copy here." in html and "Body copy here." in text
    assert "a short preheader" in html          # hidden preheader div is rendered
    assert "foxyaudit.tech" in text             # footer carried into plaintext


def test_code_block_shows_code_in_both_parts():
    html, text = et.layout(title="Code", preheader="p", blocks=[et.code_block("123456")])
    assert "123456" in html
    assert "123456" in text


def test_button_url_in_both_parts():
    url = "https://foxyaudit.tech/dash?reset_token=abc-123_XYZ"
    html, text = et.layout(title="Go", preheader="p", blocks=[et.button("Choose a new password", url)])
    assert url in html and url in text
    assert "Choose a new password" in html


def test_cta_kwarg_appends_a_button():
    url = "https://x.test/y"
    html, text = et.layout(title="t", preheader="p", blocks=[et.paragraph("x")],
                           cta={"label": "Do it", "url": url})
    assert "Do it" in html and url in html and url in text


def test_user_content_is_escaped_in_html_but_literal_in_text():
    evil = "<script>alert('x')</script>"
    html, text = et.layout(title="t", preheader="p", blocks=[et.paragraph(evil)])
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert evil in text     # plaintext is not markup, so the raw string is fine (and expected)


def test_every_component_escapes_its_input():
    evil = "<b>x</b>&'\""
    comps = [et.heading(evil), et.paragraph(evil), et.muted(evil), et.code_block(evil),
             et.callout(evil, "bad"), et.button(evil, "http://e/<script>"),
             et.info_rows([(evil, evil)])]
    for c in comps:
        assert "<b>x</b>" not in c["h"], c["h"]
        assert "&lt;b&gt;" in c["h"], c["h"]
    # the button URL is escaped too — no raw <script> smuggled through href
    assert "<script>" not in et.button(evil, "http://e/<script>")["h"]


def test_plaintext_is_never_empty():
    _, text = et.layout(title="Subject Line", preheader="p",
                        blocks=[et.paragraph("a"), et.divider(), et.muted("b")])
    assert text.strip()
    assert text.startswith("Subject Line")


def test_staff_vs_customer_tagline():
    hs, _ = et.layout(title="t", preheader="p", blocks=[et.paragraph("x")], surface="staff")
    hc, _ = et.layout(title="t", preheader="p", blocks=[et.paragraph("x")], surface="customer")
    assert "Internal ops" in hs
    assert "Compliance &amp; audit" in hc and "Internal ops" not in hc


def test_callout_all_tones_render():
    for tone in ("info", "warn", "bad", "ok"):
        c = et.callout("the message", tone)
        assert "the message" in c["h"] and c["t"].startswith(f"[{tone}]")
    # an unknown tone falls back to the info palette rather than raising
    assert "the message" in et.callout("the message", "nonsense")["h"]


def test_compact_variant_still_valid():
    html, text = et.layout(title="Ops", preheader="p", blocks=[et.paragraph("x")],
                           surface="staff", variant="compact")
    assert html.startswith("<!DOCTYPE html>") and "Ops" in html and text.strip()


def test_info_rows_pairs_render_in_both_parts():
    html, text = et.layout(title="t", preheader="p",
                           blocks=[et.info_rows([("Failed", "3 org(s)"), ("Stale", "1 org(s)")])])
    assert "Failed" in html and "3 org(s)" in html
    assert "Failed: 3 org(s)" in text and "Stale: 1 org(s)" in text
