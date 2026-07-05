"""PII signal detection — the expanded regex layer (Phase 5 · 5J).

Presidio is an optional extra; these cover the always-on, dependency-free layer.
Only SIGNAL labels are asserted — raw values never leave the client.
"""

from __future__ import annotations

from foxy_audit.pii import detect_pii


def test_email_and_ssn():
    assert "email" in detect_pii("reach me at jane.doe@acme.co", "")
    assert "ssn_pattern" in detect_pii("", "ssn 123-45-6789")


def test_phone():
    assert "phone" in detect_pii("call (415) 555-2671 today", "")
    assert "phone" in detect_pii("", "+1 415-555-2671")


def test_credit_card_luhn_gated():
    assert "credit_card" in detect_pii("card 4242 4242 4242 4242", "")     # valid Luhn
    assert "credit_card" not in detect_pii("ref 4242 4242 4242 4241", "")  # fails Luhn


def test_ip_address():
    assert "ip_address" in detect_pii("connected from 192.168.1.100", "")


def test_clean_text_has_no_signals():
    assert detect_pii("what is the capital of France?", "Paris.") == []


def test_signals_are_deduped_and_sorted():
    sig = detect_pii("a@b.com and c@d.com", "")
    assert sig == sorted(set(sig)) and sig.count("email") == 1
