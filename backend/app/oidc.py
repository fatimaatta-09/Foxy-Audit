"""Minimal OIDC authorization-code helpers for enterprise SSO (P3 · §H).

Uses the confidential-client code flow: the id_token is fetched server-to-server
from the IdP token endpoint over TLS with client authentication, so per OIDC Core
§3.1.3.7 its signature need not be re-verified here — we still validate iss, aud,
exp, and nonce. Keeps SSO dependency-free (urllib + stdlib), inert until an org
configures a connection.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from urllib import parse as urlparse
from urllib import request as urlrequest

log = logging.getLogger("foxy.oidc")

_DISCOVERY_CACHE: dict[str, dict] = {}


def discover(issuer: str) -> dict:
    """Fetch (and cache) the IdP's OpenID configuration document."""
    issuer = issuer.rstrip("/")
    if issuer in _DISCOVERY_CACHE:
        return _DISCOVERY_CACHE[issuer]
    url = issuer + "/.well-known/openid-configuration"
    with urlrequest.urlopen(url, timeout=8) as resp:      # noqa: S310 — https issuer
        doc = json.loads(resp.read().decode("utf-8"))
    _DISCOVERY_CACHE[issuer] = doc
    return doc


def build_authorize_url(*, issuer: str, client_id: str, redirect_uri: str,
                        state: str, nonce: str) -> str:
    endpoint = discover(issuer).get("authorization_endpoint") or (issuer.rstrip("/") + "/authorize")
    query = urlparse.urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
        "scope": "openid email profile", "state": state, "nonce": nonce,
    })
    return f"{endpoint}?{query}"


def exchange_code(*, issuer: str, client_id: str, client_secret: str,
                  code: str, redirect_uri: str) -> dict:
    """Exchange an auth code for tokens at the IdP token endpoint (server-to-server)."""
    endpoint = discover(issuer).get("token_endpoint") or (issuer.rstrip("/") + "/token")
    data = urlparse.urlencode({
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
        "client_id": client_id, "client_secret": client_secret,
    }).encode("utf-8")
    req = urlrequest.Request(endpoint, data=data, method="POST",
                             headers={"Content-Type": "application/x-www-form-urlencoded",
                                      "Accept": "application/json"})
    with urlrequest.urlopen(req, timeout=10) as resp:     # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def decode_id_token(id_token: str) -> dict:
    """Decode the id_token payload (no signature check — code flow is TLS-trusted)."""
    payload_b64 = id_token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))


def valid_claims(claims: dict, *, issuer: str, client_id: str, nonce: str) -> bool:
    """Validate the standard security claims for our flow."""
    aud = claims.get("aud")
    aud_ok = client_id == aud or (isinstance(aud, list) and client_id in aud)
    iss_ok = (claims.get("iss") or "").rstrip("/") == issuer.rstrip("/")
    exp_ok = int(claims.get("exp", 0)) > int(time.time()) - 60
    nonce_ok = claims.get("nonce") == nonce
    if not (aud_ok and iss_ok and exp_ok and nonce_ok):
        log.warning("oidc claim check failed (iss=%s aud=%s exp=%s nonce=%s)",
                    iss_ok, aud_ok, exp_ok, nonce_ok)
    return bool(aud_ok and iss_ok and exp_ok and nonce_ok)
