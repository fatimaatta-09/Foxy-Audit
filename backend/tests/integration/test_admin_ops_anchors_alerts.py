"""P0 admin anchor monitor and alert-center coverage."""

from __future__ import annotations

import uuid

from app.db import SessionLocal
from app.models import AdminAction, AuditLog, ChainAnchor


def _audit_row(org_id: str, seq: int = 1) -> str:
    db = SessionLocal()
    try:
        row = AuditLog(
            org_id=uuid.UUID(org_id), seq=seq,
            prompt_hash="c" * 64, response_hash="d" * 64,
            token_count=8, policy_tag="default",
            prev_hash="0" * 64, chain_hash=f"{seq + 100:064x}",
            grading_status="pending", grading_attempts=0,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return str(row.id)
    finally:
        db.close()


def test_anchor_monitor_scopes_and_operator_reanchor_is_audited(
    client, make_org, make_staff, staff_login
):
    org_a = make_org(name="Anchor A")
    org_b = make_org(name="Anchor B")
    _audit_row(org_a["org_id"])
    _audit_row(org_b["org_id"])

    viewer = make_staff(role="viewer")
    viewer_console = staff_login(viewer["email"], viewer["password"])
    all_rows = viewer_console.get("/admin/v1/anchors")
    assert all_rows.status_code == 200
    assert all_rows.json()["summary"]["organizations"] == 2
    scoped = viewer_console.get(
        "/admin/v1/anchors", params={"org_id": org_a["org_id"]}
    )
    assert scoped.status_code == 200
    assert [row["org_id"] for row in scoped.json()["organizations"]] == [org_a["org_id"]]
    assert scoped.json()["organizations"][0]["latest"] is None
    assert viewer_console.post(
        f"/admin/v1/anchors/{org_a['org_id']}/anchor"
    ).status_code == 403

    operator = make_staff(role="operator")
    operator_console = staff_login(operator["email"], operator["password"])
    response = operator_console.post(f"/admin/v1/anchors/{org_a['org_id']}/anchor")
    assert response.status_code == 200
    assert response.json()["anchor"]["status"] == "confirmed"
    assert response.json()["anchor"]["chain"] == "stub"

    db = SessionLocal()
    try:
        anchor = db.query(ChainAnchor).filter(
            ChainAnchor.org_id == uuid.UUID(org_a["org_id"])
        ).one()
        action = db.query(AdminAction).filter(
            AdminAction.action == "anchor.reanchor",
            AdminAction.target_org_id == uuid.UUID(org_a["org_id"]),
        ).one()
        assert anchor.status == "confirmed"
        assert action.detail["force"] is True
    finally:
        db.close()


def test_alert_center_derives_deadletter_and_acknowledges_with_audit(
    client, make_org, make_staff, staff_login
):
    org = make_org(name="Alert Org")
    _failed_id = _seed_failed(org["org_id"])
    viewer = make_staff(role="viewer")
    viewer_console = staff_login(viewer["email"], viewer["password"])

    alerts = viewer_console.get("/admin/v1/alerts")
    assert alerts.status_code == 200
    ids = {item["id"] for item in alerts.json()["items"]}
    assert "health.worker" in ids
    assert "grading.deadletter" in ids
    assert viewer_console.post("/admin/v1/alerts/grading.deadletter/ack").status_code == 403

    operator = make_staff(role="operator")
    operator_console = staff_login(operator["email"], operator["password"])
    response = operator_console.post("/admin/v1/alerts/grading.deadletter/ack")
    assert response.status_code == 200
    current = operator_console.get("/admin/v1/alerts").json()
    deadletter = next(item for item in current["items"] if item["id"] == "grading.deadletter")
    assert deadletter["acknowledged"] is True

    db = SessionLocal()
    try:
        action = db.query(AdminAction).filter(
            AdminAction.action == "alert.ack",
            AdminAction.target_id == "grading.deadletter",
        ).one()
        assert action.target_org_id is None
    finally:
        db.close()


def _seed_failed(org_id: str) -> str:
    db = SessionLocal()
    try:
        row = AuditLog(
            org_id=uuid.UUID(org_id), seq=1,
            prompt_hash="e" * 64, response_hash="f" * 64,
            token_count=4, policy_tag="default",
            prev_hash="0" * 64, chain_hash="1" * 64,
            grading_status="failed", grading_attempts=5,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return str(row.id)
    finally:
        db.close()
