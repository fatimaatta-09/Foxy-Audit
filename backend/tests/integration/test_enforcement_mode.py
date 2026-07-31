"""P5 §A — `enforcement_mode` gets a real consumer, and grading never notices.

The field was stored, snapshotted and displayed for months while being read by
nothing. §A.2 gives it exactly one job: whether a graded breach EMAILS a human.

Everything else must be untouched, and that is what most of this file asserts.
The verdict, the AuditEvent and the chain entry are written identically in all
three modes; the machine contracts (the outbound webhook queue, and the org's
own notify_webhook_url POST) fire in all three modes; and `flag` — which after
migration 0059 is the whole population that never chose anything — behaves
exactly as the product did the day before this shipped.

Migration 0059 itself gets two guards: it must not touch recorded evidence, and
it must not overwrite an org that deliberately chose `block`.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app import org_notifications as on
from app import user_notifications as un
from app import webhook_delivery as wd
from app import worker
from app.db import SessionLocal, engine
from app.models import OrgPolicy
from app.policy_snapshot import policy_snapshot_hash

_MIGRATION = (Path(__file__).resolve().parents[2] / "migrations" / "versions"
              / "0059_enforcement_mode_opt_in.py")


def _flip_sql() -> str:
    """The UPDATE that 0059 actually ships, loaded from the migration itself.

    Re-typing the SQL here would let the two drift apart silently, which is the
    one failure a migration guard cannot afford."""
    spec = importlib.util.spec_from_file_location("_m0059", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.FLIP_UNCHOSEN_SQL


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _breach_event(tag: str = "p5"):
    """A deterministic breach — pii_signals with no judge configured falls through
    to policy_engine, so every run of this file grades to the same verdict with
    no network and no monkeypatched evaluator."""
    return {
        "event_id": str(uuid.uuid4()),
        "prompt_hash": _h(f"prompt-{tag}"), "response_hash": _h(f"response-{tag}"),
        "token_count": 10, "policy_tag": "chat",
        "pii_signals": ["email"],
    }


def _set_policy(org_id, **fields):
    db = SessionLocal()
    try:
        oid = uuid.UUID(str(org_id))
        row = db.get(OrgPolicy, oid) or OrgPolicy(org_id=oid)
        for key, value in fields.items():
            setattr(row, key, value)
        db.add(row)
        db.commit()
    finally:
        db.close()


def _drain_queues() -> None:
    for q in (on._NOTICE_QUEUE, un._BREACH_QUEUE, wd._DELIVERY_QUEUE):
        while True:
            try:
                q.get_nowait()
            except Exception:       # noqa: BLE001 — Empty, whichever queue module
                break


@pytest.fixture(autouse=True)
def _clean_queues():
    """All three queues are module-level; one test must never feed another."""
    _drain_queues()
    yield
    _drain_queues()


def _grade_and_drain(org, client, monkeypatch, *, tag="p5") -> dict:
    """Ingest one breach, grade it, then drain the two email queues separately.

    `on.email_mod` and `un.email_mod` are the same module object, so a single
    patch captures both senders — the list is cleared between phases so the
    org-level notice and the per-seat fan-out can be told apart.
    """
    assert client.post("/v1/logs/batch", json=[_breach_event(tag)],
                       headers=org["auth"]).status_code == 202
    sent: list = []
    monkeypatch.setattr(on.email_mod, "send_email",
                        lambda **kw: sent.append(kw) or True)
    posts: list = []
    monkeypatch.setattr(on.requests, "post",
                        lambda url, **kw: posts.append((url, kw)) or True)

    db = SessionLocal()
    try:
        rows = worker._claim_batch(db, 10, 300)
        assert rows, "expected a pending row to grade"
        deliveries_before = wd.queue_depth()
        worker._grade_one(db, rows[0])
        queued_deliveries = wd.queue_depth() - deliveries_before
        assert sent == [], "grading must not reach the mail provider inline"
        on.drain_breach_notices(db)
        org_mail, sent[:] = list(sent), []
        un.drain_breach_alerts(db)
        seat_mail = list(sent)
    finally:
        db.close()
    return {"org_mail": org_mail, "seat_mail": seat_mail, "posts": posts,
            "queued_deliveries": queued_deliveries}


def _verdict_rows(org_id) -> list[dict]:
    with engine.begin() as conn:
        return [dict(r) for r in conn.execute(text(
            "SELECT seq, grading_status, gemini_verdict, chain_hash "
            "FROM audit_logs WHERE org_id = :o ORDER BY seq"
        ), {"o": str(org_id)}).mappings().all()]


def _seat(org, add_user, email="seat@corp.test"):
    """A second member, distinct from the org-level destination, so the per-seat
    fan-out has somebody to mail (the org address is deduped out of it)."""
    add_user(org["org_id"], email, "seatpass123", role="member")
    return email


# ══ 1 · `flag` is byte-identical to the day before this shipped ══════════════
# After 0059 this is the entire population that never chose anything, so a
# regression here is a regression for every existing customer at once.

@pytest.mark.parametrize("notify,mails,webhook", [
    ("immediate", True, True),
    ("digest", False, False),      # sends nothing today, and must keep not sending
    ("none", False, False),
])
def test_flag_behaves_exactly_as_before(make_org, client, add_user, monkeypatch,
                                        notify, mails, webhook):
    org = make_org()
    seat = _seat(org, add_user)
    _set_policy(org["org_id"], enforcement_mode="flag", notify_on_breach=notify,
                notify_email="alerts@corp.test",
                notify_webhook_url="https://example.test/hook")
    out = _grade_and_drain(org, client, monkeypatch)
    assert bool(any(m["to"] == "alerts@corp.test" for m in out["org_mail"])) is mails
    assert bool(any(m["to"] == seat for m in out["seat_mail"])) is mails
    assert bool(out["posts"]) is webhook


# ══ 2 · `monitor` suppresses BOTH email paths ═══════════════════════════════

def test_monitor_suppresses_the_org_notice_and_the_per_seat_fan_out(
        make_org, client, add_user, monkeypatch):
    """Both, not one. The org-level notice and the per-seat fan-out are separate
    modules with separate queues, and a tenant who asked for silence getting mail
    from the other one is the same broken promise either way."""
    org = make_org()
    seat = _seat(org, add_user)
    _set_policy(org["org_id"], enforcement_mode="monitor",
                notify_on_breach="immediate", notify_email="alerts@corp.test")
    out = _grade_and_drain(org, client, monkeypatch)
    assert out["org_mail"] == [], "monitor still emailed the org address"
    assert not any(m["to"] == seat for m in out["seat_mail"]), \
        "monitor still emailed a seat"


# ══ 3 · `monitor` suppresses DELIVERY, never RECORDING ══════════════════════

def test_monitor_still_writes_the_verdict_the_event_and_the_chain(
        make_org, client, monkeypatch):
    """The one that matters. Suppression must never touch evidence: monitor means
    "do not email me", not "do not look" — the finding is still in the ledger,
    still in the export, and still in Alerts."""
    org = make_org()
    _set_policy(org["org_id"], enforcement_mode="monitor",
                notify_on_breach="immediate", notify_email="alerts@corp.test")
    _grade_and_drain(org, client, monkeypatch)

    rows = _verdict_rows(org["org_id"])
    assert len(rows) == 1
    assert rows[0]["grading_status"] == "graded"
    assert rows[0]["gemini_verdict"] is not None
    assert rows[0]["gemini_verdict"]["policy_breach"] is True
    assert rows[0]["chain_hash"], "the chain entry was not written"

    with engine.begin() as conn:
        events = conn.execute(text(
            "SELECT count(*) FROM audit_events "
            "WHERE org_id = :o AND event_type = 'verdict'"
        ), {"o": org["org_id"]}).scalar()
    assert events == 1, "the AuditEvent was not written under monitor"
    assert client.get("/v1/verify", headers=org["auth"]).json()["ok"] is True


# ══ 4 · machine contracts fire in EVERY mode ════════════════════════════════

@pytest.mark.parametrize("mode", ["block", "flag", "monitor"])
def test_machine_contracts_fire_in_every_mode(make_org, client, monkeypatch, mode):
    """Webhook subscriptions (`enqueue_grading`, unconditional) and the org's own
    `notify_webhook_url` POST are integration contracts. An integration that
    stops firing is an outage, not a quieter inbox — only humans are modulated."""
    org = make_org()
    _set_policy(org["org_id"], enforcement_mode=mode,
                notify_on_breach="immediate", notify_email="alerts@corp.test",
                notify_webhook_url="https://example.test/hook")
    out = _grade_and_drain(org, client, monkeypatch)
    assert out["queued_deliveries"] == 1, "enqueue_grading did not fire"
    assert out["posts"], f"the org webhook POST was suppressed under {mode}"
    assert out["posts"][0][0] == "https://example.test/hook"
    assert out["posts"][0][1]["json"]["type"] == "policy_breach"
    if mode == "monitor":
        # The split that makes this pass: same call, email gone, POST intact.
        assert out["org_mail"] == []


# ══ 5 · `block` escalates `digest`, and never `none` ════════════════════════

def test_block_escalates_digest_to_a_sent_email(make_org, client, add_user,
                                                monkeypatch):
    """`digest` sends nothing today — both senders gate on != "immediate" and the
    weekly digest runs off a per-user preference — so it is behaviourally
    identical to `none`. That is what an org on the loudest setting escalates."""
    org = make_org()
    seat = _seat(org, add_user)
    _set_policy(org["org_id"], enforcement_mode="block",
                notify_on_breach="digest", notify_email="alerts@corp.test")
    out = _grade_and_drain(org, client, monkeypatch)
    assert any(m["to"] == "alerts@corp.test" for m in out["org_mail"])
    assert any(m["to"] == seat for m in out["seat_mail"])


def test_block_never_overrides_an_explicit_none(make_org, client, add_user,
                                                monkeypatch):
    """`none` is an off switch a human threw. An escalation that overrides one is
    a dark pattern, not a feature."""
    org = make_org()
    seat = _seat(org, add_user)
    _set_policy(org["org_id"], enforcement_mode="block",
                notify_on_breach="none", notify_email="alerts@corp.test")
    out = _grade_and_drain(org, client, monkeypatch)
    assert out["org_mail"] == []
    assert not any(m["to"] == seat for m in out["seat_mail"])


# ══ 6 · the verdict is identical in all three modes ═════════════════════════

def test_the_persisted_verdict_is_identical_across_all_three_modes(
        make_org, client, monkeypatch):
    """Asserted on the STORED verdict, not on a notification count. The verdict is
    evidence: it goes into the hash chain and the export, and a setting that moved
    it would make two tenants' evidence incomparable."""
    verdicts = {}
    for mode in ("block", "flag", "monitor"):
        org = make_org()
        _set_policy(org["org_id"], enforcement_mode=mode,
                    notify_on_breach="immediate", notify_email="alerts@corp.test")
        _grade_and_drain(org, client, monkeypatch, tag="same-input")
        rows = _verdict_rows(org["org_id"])
        assert len(rows) == 1
        verdicts[mode] = json.dumps(rows[0]["gemini_verdict"], sort_keys=True)
    assert verdicts["block"] == verdicts["flag"] == verdicts["monitor"], verdicts


def test_the_judge_projection_still_drops_the_field(make_org):
    """`judge_policy_config` is the boundary that decides what a judge can act on.
    Adding this field there is what would make it reachable — so it must stay
    dropped even now that it has a consumer somewhere else entirely."""
    from app.policy_snapshot import (POLICY_SNAPSHOT_SCHEMA,
                                     capture_policy_snapshot, judge_policy_config)

    org = make_org()
    _set_policy(org["org_id"], enforcement_mode="monitor")
    db = SessionLocal()
    try:
        snapshot = capture_policy_snapshot(db.get(OrgPolicy, uuid.UUID(org["org_id"])))
    finally:
        db.close()
    assert snapshot["schema"] == POLICY_SNAPSHOT_SCHEMA
    # Still IN the snapshot (foxy-policy-v1 is structurally stable)…
    assert snapshot["enforcement_mode"] == "monitor"
    # …and still OUT of everything a judge sees.
    assert "enforcement_mode" not in judge_policy_config(snapshot)


# ══ 7 · 0059 leaves recorded evidence byte-identical ════════════════════════

def test_0059_never_rewrites_a_recorded_policy_snapshot(make_org, client):
    """Migration 0056 refused to touch this column because its values are already
    inside snapshots handed to auditors. 0059 honors that objection rather than
    overruling it: it moves the LIVE row and nothing else."""
    org = make_org()
    _set_policy(org["org_id"], enforcement_mode="block")
    assert client.post("/v1/logs/batch", json=[_breach_event("evidence")],
                       headers=org["auth"]).status_code == 202

    read = ("SELECT event_metadata::text AS meta, chain_hash FROM audit_logs "
            "WHERE org_id = :o ORDER BY seq")
    with engine.begin() as conn:
        before = [dict(r) for r in conn.execute(text(read),
                                                {"o": org["org_id"]}).mappings()]
    assert before, "nothing was recorded to guard"
    snapshot = json.loads(before[0]["meta"])["policy_snapshot"]
    assert snapshot["enforcement_mode"] == "block", \
        "the fixture did not record the value this test exists to protect"

    with engine.begin() as conn:
        conn.execute(text(_flip_sql()))
        after = [dict(r) for r in conn.execute(text(read),
                                               {"o": org["org_id"]}).mappings()]

    assert after == before, "0059 rewrote recorded evidence"
    metadata = json.loads(after[0]["meta"])
    assert metadata["policy_snapshot"]["enforcement_mode"] == "block"
    assert metadata["policy_snapshot_hash"] == policy_snapshot_hash(
        metadata["policy_snapshot"]), "the snapshot no longer hashes to its digest"
    assert client.get("/v1/verify", headers=org["auth"]).json()["ok"] is True

    # …while the LIVE row did move, which is the whole point of the migration.
    db = SessionLocal()
    try:
        assert db.get(OrgPolicy, uuid.UUID(org["org_id"])).enforcement_mode == "flag"
    finally:
        db.close()


# ══ 8 · 0059 never overwrites a deliberate `block` ═════════════════════════

def test_0059_keeps_a_deliberately_chosen_block(make_org, client, login):
    """`account_actions` is what makes the flip safe at all: every `policy.update`
    records the chosen enforcement_mode in its JSONB detail, so an org that CHOSE
    `block` is distinguishable from one that merely inherited it.

    Both halves are asserted — dropping the NOT EXISTS clause overwrites the
    chooser, dropping the UPDATE strands the inheritor."""
    chose = make_org()
    admin = login(chose["admin_email"], chose["admin_password"])
    body = admin.get("/v1/policies").json()
    body["enforcement_mode"] = "block"
    assert admin.put("/v1/policies", json=body).status_code == 200

    inherited = make_org()
    _set_policy(inherited["org_id"], enforcement_mode="block")

    with engine.begin() as conn:
        recorded = conn.execute(text(
            "SELECT count(*) FROM account_actions WHERE org_id = :o "
            "AND action = 'policy.update' AND detail->>'enforcement_mode' = 'block'"
        ), {"o": chose["org_id"]}).scalar()
        assert recorded == 1, "the PUT did not record the choice 0059 relies on"
        conn.execute(text(_flip_sql()))

    db = SessionLocal()
    try:
        assert db.get(OrgPolicy, uuid.UUID(chose["org_id"])).enforcement_mode == "block", \
            "0059 overwrote a deliberate choice"
        assert db.get(OrgPolicy, uuid.UUID(inherited["org_id"])).enforcement_mode == "flag", \
            "0059 left an inherited default in place"
    finally:
        db.close()
