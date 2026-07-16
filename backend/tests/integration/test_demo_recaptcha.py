"""The public demo form (source=demo) is reCAPTCHA-gated when the secret is set."""
import app.routers.leads as leads


def test_demo_lead_rejected_when_recaptcha_fails(client, monkeypatch):
    monkeypatch.setattr(leads, "_verify_recaptcha", lambda t: False)
    r = client.post("/v1/leads", json={"email": "bot@corp.com", "source": "demo",
                                        "message": "hi", "recaptcha_token": "bad"})
    assert r.status_code == 400


def test_demo_lead_ok_when_recaptcha_passes(client, monkeypatch):
    monkeypatch.setattr(leads, "_verify_recaptcha", lambda t: True)
    r = client.post("/v1/leads", json={"email": "human@corp.com", "source": "demo",
                                        "message": "hi", "recaptcha_token": "good"})
    assert r.status_code == 200


def test_non_demo_source_is_not_gated(client):
    # other sources carry no token and must not be blocked
    assert client.post("/v1/leads", json={"email": "s@corp.com", "source": "support",
                                          "message": "hi"}).status_code == 200


def test_verify_skips_when_no_secret_configured():
    # default test config has no RECAPTCHA_SECRET_KEY -> verification is skipped (True)
    assert leads._verify_recaptcha(None) is True
