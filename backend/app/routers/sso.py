"""Enterprise SSO (OIDC) for the customer dashboard (P3 · §H).

Admin config:
  GET/PUT/DELETE /v1/auth/sso/connection   the caller org's OIDC connection

Login flow (opt-in per org — inert until configured):
  POST /v1/auth/sso/discover {email}       is this email's domain SSO? → authorize_url
  GET  /v1/auth/sso/callback               IdP redirect → verify → session → /dashboard

JIT provisioning: an SSO user is matched by email within the connection's org, or
created there as a member. The email domain must match the connection's domain, so
one org's SSO can never mint a session in another tenant.
"""

from __future__ import annotations

import secrets
import uuid

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import account_audit, oidc
from ..auth import require_role
from ..config import get_settings
from ..db import get_db
from ..models import Organization, SsoConnection, User
from .auth_human import _establish_session, _record_login_and_alert

router = APIRouter()


def _domain(email: str) -> str:
    return email.strip().lower().rsplit("@", 1)[-1] if "@" in email else ""


def _callback_url(request: Request) -> str:
    return str(request.base_url).rstrip("/") + "/v1/auth/sso/callback"


# ── admin config ─────────────────────────────────────────────────────────────

class SsoConnectionIn(BaseModel):
    email_domain: str = Field(max_length=255)
    issuer: str = Field(max_length=512)
    client_id: str = Field(max_length=512)
    client_secret: str = Field(max_length=512)
    active: bool = True


class SsoConnectionOut(BaseModel):
    configured: bool = False
    email_domain: str | None = None
    issuer: str | None = None
    client_id: str | None = None
    active: bool = False
    has_secret: bool = False       # never return the secret itself


@router.get("/v1/auth/sso/connection", response_model=SsoConnectionOut)
def get_connection(admin: User = Depends(require_role("admin")),
                   db: Session = Depends(get_db)):
    c = db.execute(select(SsoConnection).where(SsoConnection.org_id == admin.org_id)).scalar_one_or_none()
    if c is None:
        return SsoConnectionOut(configured=False)
    return SsoConnectionOut(configured=True, email_domain=c.email_domain, issuer=c.issuer,
                            client_id=c.client_id, active=c.active, has_secret=bool(c.client_secret))


@router.put("/v1/auth/sso/connection", response_model=SsoConnectionOut)
def put_connection(body: SsoConnectionIn,
                   admin: User = Depends(require_role("admin")),
                   db: Session = Depends(get_db)):
    domain = body.email_domain.strip().lower().lstrip("@")
    issuer = body.issuer.strip()
    if "." not in domain or "@" in domain or "/" in domain:
        raise HTTPException(status_code=422, detail="email_domain must be a bare domain, e.g. acme.com")
    if not issuer.startswith("https://"):
        raise HTTPException(status_code=422, detail="issuer must be an https URL")
    c = db.execute(select(SsoConnection).where(SsoConnection.org_id == admin.org_id)).scalar_one_or_none()
    new_secret = body.client_secret.strip()
    if not new_secret:
        if c is None or not c.client_secret:
            raise HTTPException(status_code=422, detail="client_secret is required")
        new_secret = c.client_secret          # blank on edit → keep the stored secret
    if c is None:
        c = SsoConnection(org_id=admin.org_id)
        db.add(c)
    c.email_domain, c.issuer = domain, issuer
    c.client_id, c.client_secret, c.active = body.client_id.strip(), new_secret, body.active
    account_audit.record_account_action(
        db, org_id=admin.org_id, actor_email=admin.email, action="sso.configure",
        target=domain, detail={"issuer": issuer, "active": body.active})
    # A domain routes to exactly one org's SSO — the global unique index on
    # email_domain enforces that even across tenants (RLS hides other orgs' rows
    # from the pre-check, so we rely on the constraint).
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409,
                            detail="that email domain is already claimed by another workspace")
    db.refresh(c)
    return SsoConnectionOut(configured=True, email_domain=c.email_domain, issuer=c.issuer,
                            client_id=c.client_id, active=c.active, has_secret=True)


@router.delete("/v1/auth/sso/connection")
def delete_connection(admin: User = Depends(require_role("admin")),
                      db: Session = Depends(get_db)):
    c = db.execute(select(SsoConnection).where(SsoConnection.org_id == admin.org_id)).scalar_one_or_none()
    if c is not None:
        db.delete(c)
        account_audit.record_account_action(
            db, org_id=admin.org_id, actor_email=admin.email, action="sso.remove", target=c.email_domain)
        db.commit()
    return {"status": "removed"}


# ── login flow ───────────────────────────────────────────────────────────────

class DiscoverRequest(BaseModel):
    email: str


@router.post("/v1/auth/sso/discover")
def discover_sso(payload: DiscoverRequest, request: Request, db: Session = Depends(get_db)):
    """Does this email's domain use SSO? If so, stash CSRF state+nonce and return
    the IdP authorize URL for the browser to redirect to."""
    domain = _domain(payload.email)
    if not domain:
        return {"sso": False}
    conn = db.execute(select(SsoConnection).where(
        SsoConnection.email_domain == domain, SsoConnection.active.is_(True))).scalar_one_or_none()
    if conn is None:
        return {"sso": False}
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    request.session["sso_state"] = state
    request.session["sso_nonce"] = nonce
    request.session["sso_domain"] = domain
    try:
        url = oidc.build_authorize_url(issuer=conn.issuer, client_id=conn.client_id,
                                       redirect_uri=_callback_url(request), state=state, nonce=nonce)
    except Exception:                        # noqa: BLE001 — discovery/network failure
        raise HTTPException(status_code=502, detail="could not reach the SSO provider")
    return {"sso": True, "authorize_url": url}


@router.get("/v1/auth/sso/callback")
def sso_callback(request: Request, code: str = "", state: str = "",
                 db: Session = Depends(get_db)):
    """IdP redirect target: validate state, exchange the code, verify the id_token,
    match/provision the user in the connection's org, and start a dashboard session."""
    sess_state = request.session.pop("sso_state", None)
    nonce = request.session.pop("sso_nonce", None)
    domain = request.session.pop("sso_domain", None)
    if not code or not state or state != sess_state or not domain:
        raise HTTPException(status_code=400, detail="invalid SSO state")
    conn = db.execute(select(SsoConnection).where(
        SsoConnection.email_domain == domain, SsoConnection.active.is_(True))).scalar_one_or_none()
    if conn is None:
        raise HTTPException(status_code=400, detail="SSO is not configured for this domain")
    try:
        tokens = oidc.exchange_code(issuer=conn.issuer, client_id=conn.client_id,
                                    client_secret=conn.client_secret, code=code,
                                    redirect_uri=_callback_url(request))
        claims = oidc.decode_id_token(tokens["id_token"])
    except Exception:                        # noqa: BLE001
        raise HTTPException(status_code=401, detail="SSO token exchange failed")
    if not oidc.valid_claims(claims, issuer=conn.issuer, client_id=conn.client_id, nonce=nonce or ""):
        raise HTTPException(status_code=401, detail="SSO token verification failed")

    email = (claims.get("email") or "").strip().lower()
    if not email or _domain(email) != domain:
        raise HTTPException(status_code=401, detail="SSO email domain mismatch")

    user = db.execute(select(User).where(
        User.email == email, User.org_id == conn.org_id)).scalar_one_or_none()
    if user is None:                         # JIT provision as a member of the SSO org
        placeholder = bcrypt.hashpw(secrets.token_urlsafe(32).encode(), bcrypt.gensalt()).decode()
        user = User(org_id=conn.org_id, email=email, password_hash=placeholder, role="member")
        db.add(user)
        db.flush()
    if user.disabled:
        raise HTTPException(status_code=403, detail="This account is disabled")
    org = db.get(Organization, conn.org_id)
    if org is not None and org.deleted_at is not None:
        raise HTTPException(status_code=403, detail="This workspace has been deleted")
    db.commit()
    # SSO sign-ins were not being recorded in login history at all, so they were
    # invisible to the admin history view AND could never be recognised as a known
    # device. Record them like every other successful sign-in (P3 §3).
    sid = _establish_session(request, user, db)
    _record_login_and_alert(request, user, db, user.email, sid)
    return RedirectResponse(url=get_settings().dashboard_url or "/dashboard", status_code=303)
