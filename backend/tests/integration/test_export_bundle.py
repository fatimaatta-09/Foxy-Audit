"""GET /v1/logs/export?format=bundle — the evidence AND the tool that checks it.

Register #27: the Compliance Passport promises "routes that actually work for a
third party" and tells the reader to run the standalone verifier, while the
product had no way to hand that verifier over — no route served it, the SDK wheel
does not carry it, and no public repository URL exists.

Most of what follows proves plumbing. The one that proves the issue is closed is
`test_a_stranger_can_verify_from_a_bare_folder`: it unzips the archive into a
temp directory that is NOT this repo and runs the shipped script as a subprocess.
"""

from __future__ import annotations

import hashlib
import io
import os
import pathlib
import subprocess
import sys
import zipfile

import pytest

from app import export_bundle

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
REAL_VERIFIER = REPO_ROOT / "verifier" / "foxy_verify.py"


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _ingest(client, org, n: int) -> None:
    rows = [{"prompt_hash": _h(f"{org['org_id']}p{i}"),
             "response_hash": _h(f"{org['org_id']}r{i}"),
             "token_count": 10 * i + 5, "policy_tag": "chat"} for i in range(n)]
    if n > 1:
        rows[1]["agent"] = "gpt-4o"          # an agent-bearing row hashes differently
    assert client.post("/v1/logs/batch", json=rows,
                       headers=org["auth"]).status_code == 202


def _bundle(client, org, query: str = "") -> zipfile.ZipFile:
    r = client.get(f"/v1/logs/export?format=bundle{query}", headers=org["auth"])
    assert r.status_code == 200, r.text
    return zipfile.ZipFile(io.BytesIO(r.content))


def _day(offset: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(days=offset)).strftime("%Y-%m-%d")


# ── the archive itself ───────────────────────────────────────────────────────

def test_the_bundle_carries_all_three_members(make_org, client):
    org = make_org()
    _ingest(client, org, 3)
    z = _bundle(client, org)
    assert sorted(z.namelist()) == sorted(
        ["foxy-audit-logs.json", "foxy_verify.py", "VERIFY.txt"])


def test_the_response_is_a_named_zip_attachment(make_org, client):
    org = make_org()
    _ingest(client, org, 1)
    r = client.get("/v1/logs/export?format=bundle", headers=org["auth"])
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert 'filename="foxy-audit-export.zip"' in r.headers["content-disposition"]
    # Really a ZIP, not JSON with a hopeful header.
    assert r.content[:2] == b"PK"


def test_the_json_member_is_byte_identical_to_format_json(make_org, client):
    """Not "equivalent" — identical. The bundle must not become a second,
    slightly different export that the verifier then has to tolerate."""
    org = make_org()
    _ingest(client, org, 4)
    plain = client.get("/v1/logs/export?format=json", headers=org["auth"])
    assert plain.status_code == 200
    zipped = _bundle(client, org).read("foxy-audit-logs.json")
    assert zipped == plain.content


def test_the_verify_instructions_name_the_command(make_org, client):
    org = make_org()
    _ingest(client, org, 1)
    txt = _bundle(client, org).read("VERIFY.txt").decode("utf-8")
    assert "python foxy_verify.py foxy-audit-logs.json" in txt
    assert "standard library only" in txt      # why no install step is listed


# ── the drift guard (the whole reason vendoring was acceptable) ──────────────

def test_the_vendored_verifier_has_not_drifted():
    """`backend/Dockerfile` does `COPY . .` from the backend/ context, so
    `verifier/` at the repo root never reaches the image and the verifier had to
    be vendored into `app/bundled/`. This test is the price of that copy: edit
    one, and CI fails until you edit the other."""
    assert REAL_VERIFIER.exists(), f"the real verifier moved: {REAL_VERIFIER}"
    assert export_bundle.VERIFIER_PATH.read_bytes() == REAL_VERIFIER.read_bytes(), (
        "app/bundled/foxy_verify.py has drifted from verifier/foxy_verify.py — "
        "the bundle would ship a stale verifier to auditors. Re-copy the file.")


def test_the_bundle_ships_that_exact_file(make_org, client):
    """And the drift guard is only worth anything if the archive carries the
    file it guards, rather than some other copy."""
    org = make_org()
    _ingest(client, org, 1)
    assert _bundle(client, org).read("foxy_verify.py") == REAL_VERIFIER.read_bytes()


# ── failing loudly beats shipping quietly ────────────────────────────────────

def test_a_missing_verifier_is_a_503_not_a_short_bundle(make_org, client, monkeypatch,
                                                        tmp_path):
    """An archive that silently omits the tool it exists to deliver fails at the
    auditor's desk instead of here. Refuse instead."""
    org = make_org()
    _ingest(client, org, 2)
    monkeypatch.setattr(export_bundle, "VERIFIER_PATH", tmp_path / "gone.py")

    r = client.get("/v1/logs/export?format=bundle", headers=org["auth"])
    assert r.status_code == 503
    assert "verifier" in r.json()["detail"].lower()
    assert r.content[:2] != b"PK", "a bundle was emitted without the verifier"

    # …and the plain JSON export is untouched by the failure.
    assert client.get("/v1/logs/export", headers=org["auth"]).status_code == 200


def test_an_empty_verifier_is_also_refused(make_org, client, monkeypatch, tmp_path):
    """A zero-byte file is readable. It is not a verifier."""
    org = make_org()
    _ingest(client, org, 1)
    empty = tmp_path / "foxy_verify.py"
    empty.write_bytes(b"   \n")
    monkeypatch.setattr(export_bundle, "VERIFIER_PATH", empty)
    assert client.get("/v1/logs/export?format=bundle",
                      headers=org["auth"]).status_code == 503


# ── the date range behaves exactly as it does for json ───────────────────────

def test_the_date_range_applies_to_the_bundle(make_org, client):
    org = make_org()
    _ingest(client, org, 3)
    import json as _json

    inside = _json.loads(_bundle(client, org, f"&date_to={_day(0)}")
                         .read("foxy-audit-logs.json"))
    assert inside["count"] == 3, "date_to=today dropped today's rows"

    outside = _json.loads(_bundle(client, org, f"&date_to={_day(-1)}")
                          .read("foxy-audit-logs.json"))
    assert outside["count"] == 0, "date_to=yesterday returned today's rows"

    later = _json.loads(_bundle(client, org, f"&date_from={_day(1)}")
                        .read("foxy-audit-logs.json"))
    assert later["count"] == 0, "date_from=tomorrow returned rows created today"


def test_a_ranged_bundle_still_matches_the_same_ranged_json(make_org, client):
    org = make_org()
    _ingest(client, org, 3)
    q = f"date_from={_day(-1)}&date_to={_day(0)}"
    plain = client.get(f"/v1/logs/export?format=json&{q}", headers=org["auth"])
    zipped = _bundle(client, org, f"&{q}").read("foxy-audit-logs.json")
    assert zipped == plain.content


def test_the_bundle_rejects_a_date_it_cannot_parse(make_org, client):
    org = make_org()
    assert client.get("/v1/logs/export?format=bundle&date_from=not-a-date",
                      headers=org["auth"]).status_code == 422


def test_the_bundle_is_tenant_scoped(make_org, client):
    import json as _json
    a, b = make_org(), make_org()
    _ingest(client, a, 3)
    _ingest(client, b, 2)
    data = _json.loads(_bundle(client, a).read("foxy-audit-logs.json"))
    assert data["count"] == 3 and data["org_id"] == a["org_id"]


def test_the_bundle_requires_auth(client):
    assert client.get("/v1/logs/export?format=bundle").status_code == 401


def test_an_unknown_format_is_still_rejected(make_org, client):
    org = make_org()
    assert client.get("/v1/logs/export?format=tarball",
                      headers=org["auth"]).status_code == 422


# ── the export-history type ──────────────────────────────────────────────────

def test_a_bundle_download_can_be_recorded_in_export_history(make_org, login, client):
    """ExportJob.type is String(32), so "logs_bundle" needed no migration."""
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    r = c.post("/v1/exports", json={"type": "logs_bundle"})
    assert r.status_code == 200 and r.json()["type"] == "logs_bundle"
    assert any(j["type"] == "logs_bundle" for j in c.get("/v1/exports").json()["items"])


# ── THE ONE THAT CLOSES #27 ──────────────────────────────────────────────────

@pytest.mark.skipif(not REAL_VERIFIER.exists(), reason="verifier/ not checked out")
def test_a_stranger_can_verify_from_a_bare_folder(make_org, client, tmp_path):
    """Unzip somewhere that is NOT this repo, run the script it shipped with, and
    have it verify. No repo checkout, no pip install, no network, no Foxy.

    Everything above this line proves plumbing; this proves the claim the
    Compliance Passport makes in writing on every copy.
    """
    org = make_org()
    _ingest(client, org, 5)

    r = client.get("/v1/logs/export?format=bundle", headers=org["auth"])
    assert r.status_code == 200
    workdir = tmp_path / "auditor-desk"
    workdir.mkdir()
    zipfile.ZipFile(io.BytesIO(r.content)).extractall(workdir)

    # PYTHONIOENCODING is about the auditor's console, not the proof: the script
    # prints "✓" and a piped stdout on a cp1252 Windows box would otherwise die
    # in the child on an encode, failing for a reason that has nothing to do
    # with the chain.
    proc = subprocess.run(
        [sys.executable, "foxy_verify.py", "foxy-audit-logs.json"],
        cwd=workdir, capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=120)
    out = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")

    assert proc.returncode == 0, f"the shipped verifier refused its own bundle:\n{out}"
    assert "chain intact" in out, out
    assert "5 rows verified from genesis" in out, out


@pytest.mark.skipif(not REAL_VERIFIER.exists(), reason="verifier/ not checked out")
def test_the_shipped_verifier_catches_a_tampered_bundle(make_org, client, tmp_path):
    """And it is not a script that says yes to everything: edit one field in the
    unzipped ledger and the same command must fail, naming the row."""
    import json as _json

    org = make_org()
    _ingest(client, org, 4)
    r = client.get("/v1/logs/export?format=bundle", headers=org["auth"])
    workdir = tmp_path / "tampered"
    workdir.mkdir()
    zipfile.ZipFile(io.BytesIO(r.content)).extractall(workdir)

    ledger = workdir / "foxy-audit-logs.json"
    data = _json.loads(ledger.read_text(encoding="utf-8"))
    data["logs"][1]["token_count"] = 999            # the agent-bearing row
    ledger.write_text(_json.dumps(data, indent=2), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "foxy_verify.py", "foxy-audit-logs.json", "--json"],
        cwd=workdir, capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=120)
    assert proc.returncode == 1, "tampering went unreported"
    report = _json.loads(proc.stdout.decode("utf-8"))
    assert report["chain"]["ok"] is False
    assert report["chain"]["first_broken_seq"] == 2


# ── V4: the same proof, for the verdict (H1) ─────────────────────────────────

def _unzip(client, org, tmp_path, name: str) -> pathlib.Path:
    r = client.get("/v1/logs/export?format=bundle", headers=org["auth"])
    assert r.status_code == 200, r.text
    workdir = tmp_path / name
    workdir.mkdir()
    zipfile.ZipFile(io.BytesIO(r.content)).extractall(workdir)
    return workdir


def _run_verifier(workdir: pathlib.Path):
    return subprocess.run(
        [sys.executable, "foxy_verify.py", "foxy-audit-logs.json", "--json"],
        cwd=workdir, capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=120)


def test_the_bundle_carries_what_a_v4_row_needs_to_be_verified(make_org, client, tmp_path):
    """From chain_version 4 the verdict hash IS hashed material. If the export
    omitted it, a stranger could not recompute the row at all — so this asserts
    the columns are really in the archive, not just in the database."""
    import json as _json

    org = make_org()
    _ingest(client, org, 3)
    data = _json.loads((_unzip(client, org, tmp_path, "v4") / "foxy-audit-logs.json")
                       .read_text(encoding="utf-8"))
    for row in data["logs"]:
        assert row["chain_version"] == 4
        assert len(row["verdict_hash"]) == 64
        assert row["local_verdict"]["decision"] == "clean"
        # The AI grade is a separate, unbound field and is still ungraded here.
        assert row["gemini_verdict"] is None


@pytest.mark.skipif(not REAL_VERIFIER.exists(), reason="verifier/ not checked out")
def test_a_stranger_can_verify_a_v4_ledger_from_a_bare_folder(make_org, client, tmp_path):
    """The #27 proof, re-run now that ingest writes V4. A row whose verdict is
    chained but which no longer verifies from its own bundle would have broken
    the product's central claim, and no unit test would have said so."""
    import json as _json

    org = make_org()
    _ingest(client, org, 5)
    proc = _run_verifier(_unzip(client, org, tmp_path, "v4-desk"))
    out = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
    assert proc.returncode == 0, f"the shipped verifier refused a V4 bundle:\n{out}"
    assert _json.loads(proc.stdout.decode("utf-8"))["chain"]["count"] == 5


@pytest.mark.skipif(not REAL_VERIFIER.exists(), reason="verifier/ not checked out")
def test_the_shipped_verifier_catches_an_edited_verdict(make_org, client, tmp_path):
    """The point of H1, end to end: flip a verdict in the evidence and the tool an
    auditor was handed says so. Before V4 this edit verified clean."""
    import json as _json

    org = make_org()
    _ingest(client, org, 4)
    workdir = _unzip(client, org, tmp_path, "verdict-tamper")
    ledger = workdir / "foxy-audit-logs.json"
    data = _json.loads(ledger.read_text(encoding="utf-8"))
    data["logs"][2]["local_verdict"]["decision"] = "clean-ish"
    ledger.write_text(_json.dumps(data, indent=2), encoding="utf-8")

    proc = _run_verifier(workdir)
    assert proc.returncode == 1, "an edited verdict went unreported"
    report = _json.loads(proc.stdout.decode("utf-8"))
    assert report["chain"]["ok"] is False
    assert report["chain"]["first_broken_seq"] == 3
    assert "verdict" in report["chain"]["detail"]
