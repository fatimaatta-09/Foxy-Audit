"""P2 §1.5 — /v1/notifications paginates, because the page has to reach it all.

The dashboard's notifications PAGE (as opposed to the top-bar panel) shows the
whole history. `limit` is capped at 100, so before this a local slice could
never have reached row 101 — an audit surface that silently stops is the same
defect whether it stops at 30 or at 100.

`page` mirrors /v1/logs: 1-indexed, paired with `limit`. `total` is additive, so
the panel's existing `?limit=30` call is untouched and is simply page 1.
"""

from __future__ import annotations

import uuid

from app.db import SessionLocal
from app.models import Notification


def _seed(org_id, count: int) -> None:
    """Notifications are not in the fixture's TRUNCATE list, so each test seeds
    its own org id and only ever counts its own rows."""
    with SessionLocal() as db:
        for i in range(count):
            db.add(Notification(
                id=uuid.uuid4(), org_id=org_id, kind="breach",
                title=f"Breach {i:03d}", body="seeded", level="warn"))
        db.commit()


def test_page_and_limit_walk_the_whole_set(make_org, login, client):
    org = make_org()
    _seed(uuid.UUID(org["org_id"]), 25)
    c = login(org["admin_email"], org["admin_password"])

    first = c.get("/v1/notifications?limit=10&page=1").json()
    assert first["total"] >= 25
    assert len(first["items"]) == 10

    second = c.get("/v1/notifications?limit=10&page=2").json()
    assert len(second["items"]) == 10

    ids_1 = {i["id"] for i in first["items"]}
    ids_2 = {i["id"] for i in second["items"]}
    assert not (ids_1 & ids_2), "page 2 repeated rows from page 1"


def test_a_page_past_the_end_is_empty_not_an_error(make_org, login, client):
    org = make_org()
    _seed(uuid.UUID(org["org_id"]), 3)
    c = login(org["admin_email"], org["admin_password"])
    r = c.get("/v1/notifications?limit=10&page=99")
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_the_panels_existing_call_is_unchanged(make_org, login, client):
    """The top bar calls this with limit only. That must still be page 1, and
    the response must still carry `unread` and `items`."""
    org = make_org()
    _seed(uuid.UUID(org["org_id"]), 5)
    c = login(org["admin_email"], org["admin_password"])

    plain = c.get("/v1/notifications?limit=30").json()
    explicit = c.get("/v1/notifications?limit=30&page=1").json()
    assert "unread" in plain and "items" in plain
    assert [i["id"] for i in plain["items"]] == [i["id"] for i in explicit["items"]]


def test_total_counts_the_filtered_set_not_the_whole_table(make_org, login, client):
    """`unread_only` narrows the rows, so `total` has to narrow with them —
    otherwise the pager renders pages that cannot be reached."""
    org = make_org()
    org_id = uuid.UUID(org["org_id"])
    _seed(org_id, 4)
    c = login(org["admin_email"], org["admin_password"])

    everything = c.get("/v1/notifications?limit=100").json()
    c.post(f"/v1/notifications/{everything['items'][0]['id']}/read")

    unread = c.get("/v1/notifications?limit=100&unread_only=true").json()
    assert unread["total"] == len(unread["items"])
    assert unread["total"] < everything["total"], "marking one read did not narrow the filtered total"
