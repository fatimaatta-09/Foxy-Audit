"""Admin-app IP allow-list posture — secure by default in prod (empty list denies).

Pure-function tests of the access decision; no DB or app needed.
"""
from __future__ import annotations

from app.middleware.admin_guard import _is_allowed


def test_empty_list_denies_in_prod():
    # An unset ADMIN_IP_ALLOWLIST must NOT silently expose the staff console.
    assert _is_allowed(True, [], "203.0.113.7") is False


def test_empty_list_allows_in_dev():
    assert _is_allowed(False, [], "203.0.113.7") is True


def test_listed_ip_allowed_in_prod():
    assert _is_allowed(True, ["203.0.113.7"], "203.0.113.7") is True


def test_unlisted_ip_denied_in_prod():
    assert _is_allowed(True, ["203.0.113.7"], "198.51.100.9") is False


def test_cidr_range_allowed_in_prod():
    assert _is_allowed(True, ["10.0.0.0/24"], "10.0.0.42") is True


def test_multiple_entries_any_match_allowed_in_prod():
    # ADMIN_IP_ALLOWLIST is comma-separated: any single match (exact IP OR CIDR) allows.
    allow = ["203.0.113.7", "198.51.100.9", "10.0.0.0/24", "2001:db8::/32"]
    assert _is_allowed(True, allow, "203.0.113.7") is True    # 1st exact IP
    assert _is_allowed(True, allow, "198.51.100.9") is True   # 2nd exact IP
    assert _is_allowed(True, allow, "10.0.0.42") is True      # inside the CIDR
    assert _is_allowed(True, allow, "2001:db8::5") is True    # inside the IPv6 CIDR
    assert _is_allowed(True, allow, "192.0.2.1") is False     # matches none


def test_allow_all_cidr_opens_it():
    assert _is_allowed(True, ["0.0.0.0/0"], "203.0.113.7") is True
