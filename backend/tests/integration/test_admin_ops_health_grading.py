"""P0 admin health and grading recovery coverage."""

from __future__ import annotations

import uuid

from app.db import SessionLocal
from app.models import AdminAction, AuditLog


def _failed_row(org_id: str, seq: int = 1) -> str:
    db = SessionLocal()
    try:
        row = AuditLog(
            org_id=uuid.UUID(org_id), seq=seq,
            prompt_hash="a" * 64, response_hash="b" * 64,
            token_count=12, policy_tag="default",
            prev_hash="0" * 64, chain_hash=f"{seq:064x}",
            grading_status="failed", grading_attempts=3,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return str(row.id)
    finally:
        db.close()


def test_health_requires_staff_and_reports_honest_worker_state(client, make_staff, staff_login):
    assert client.get("/admin/v1/health").status_code == 401
    staff = make_staff(role="viewer")
    console = staff_login(staff["email"], staff["password"])

    response = console.get("/admin/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["database"]["status"] == "ok"
    assert body["worker"]["status"] == "missing"
    assert body["circuit_breaker"]["state"] == "unavailable"
    assert "process-local" in body["circuit_breaker"]["detail"]


def test_deadletter_is_viewable_but_mutation_requires_operator(
    client, make_org, make_staff, staff_login
):
    org_a = make_org(name="Dead Letter A")
    org_b = make_org(name="Dead Letter B")
    row_a = _failed_row(org_a["org_id"])
    _failed_row(org_b["org_id"])

    viewer = make_staff(role="viewer")
    viewer_console = staff_login(viewer["email"], viewer["password"])
    response = viewer_console.get("/admin/v1/grading/deadletter")
    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert {item["org_name"] for item in response.json()["items"]} == {
        "Dead Letter A", "Dead Letter B"
    }
    scoped = viewer_console.get(
        "/admin/v1/grading/deadletter", params={"org_id": org_a["org_id"]}
    )
    assert scoped.status_code == 200
    assert scoped.json()["total"] == 1
    assert scoped.json()["items"][0]["id"] == row_a

    assert viewer_console.post(
        f"/admin/v1/grading/deadletter/{row_a}/requeue"
    ).status_code == 403


def test_operator_requeue_changes_status_and_writes_admin_action(
    client, make_org, make_staff, staff_login
):
    org = make_org(name="Requeue Org")
    row_id = _failed_row(org["org_id"])
    operator = make_staff(role="operator")
    console = staff_login(operator["email"], operator["password"])

    response = console.post(f"/admin/v1/grading/deadletter/{row_id}/requeue")

    assert response.status_code == 200
    assert response.json() == {"status": "pending", "id": row_id}
    db = SessionLocal()
    try:
        row = db.get(AuditLog, uuid.UUID(row_id))
        action = db.query(AdminAction).filter(
            AdminAction.action == "grading.requeue",
            AdminAction.target_id == row_id,
        ).one()
        assert row.grading_status == "pending"
        assert row.grading_started_at is None
        assert action.target_org_id == uuid.UUID(org["org_id"])
        assert action.detail["grading_attempts"] == 3
    finally:
        db.close()
