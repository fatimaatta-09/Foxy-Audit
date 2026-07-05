"""Worker circuit-breaker + backoff on repeated Gemini failures (Phase 5 · 5E.2).

Pure logic (no DB): a Gemini outage makes whole grading batches fail; the breaker
trips OPEN so the poller stops hammering the API / burning attempts, backs off
with capped exponential delay, then half-opens for a trial.
"""

from __future__ import annotations

from app.worker import CircuitBreaker, backoff_delay


def test_backoff_is_capped_exponential():
    assert backoff_delay(0, 2.0, 60.0) == 2.0     # no failures → base poll interval
    assert backoff_delay(1, 2.0, 60.0) == 2.0
    assert backoff_delay(2, 2.0, 60.0) == 4.0
    assert backoff_delay(3, 2.0, 60.0) == 8.0
    assert backoff_delay(4, 2.0, 60.0) == 16.0
    assert backoff_delay(10, 2.0, 60.0) == 60.0   # capped


def test_breaker_opens_after_threshold():
    b = CircuitBreaker(threshold=3, cooldown=60.0)
    assert b.allow(now=0) is True
    b.on_failure(now=0)
    b.on_failure(now=1)
    assert b.allow(now=2) is True                 # 2 < threshold → still closed
    b.on_failure(now=3)                           # 3rd → opens
    assert b.allow(now=4) is False                # within cooldown → blocked
    assert b.allow(now=30) is False
    assert b.allow(now=63) is True                # cooldown elapsed → half-open trial


def test_breaker_success_closes_and_resets():
    b = CircuitBreaker(threshold=2, cooldown=60.0)
    b.on_failure(now=0)
    b.on_failure(now=1)                           # opens at 1
    assert b.allow(now=2) is False
    assert b.allow(now=61) is True                # half-open
    b.on_success()                                # trial passed → closed + reset
    assert b.allow(now=62) is True
    assert b.failures == 0


def test_breaker_reopens_when_trial_fails():
    b = CircuitBreaker(threshold=2, cooldown=60.0)
    b.on_failure(now=0)
    b.on_failure(now=1)                           # opens at 1
    assert b.allow(now=61) is True                # half-open trial
    b.on_failure(now=62)                          # trial failed → slides window to 62
    assert b.allow(now=63) is False
    assert b.allow(now=122) is True               # after another cooldown
