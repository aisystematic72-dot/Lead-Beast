from fastapi.testclient import TestClient

from leadbeast.api.auth import hash_password, verify_password
from leadbeast.api.main import create_app
from leadbeast.audit.report import HtmlPdfRenderer


def client() -> TestClient:
    return TestClient(create_app())


def test_healthz_and_login_page():
    c = client()
    assert c.get("/healthz").json()["status"] == "ok"
    r = c.get("/login")
    assert r.status_code == 200 and "Log in" in r.text


def test_openapi_lists_spec_endpoints():
    paths = client().get("/api/openapi.json").json()["paths"]
    for p in ("/api/campaigns", "/api/campaigns/{cid}/launch", "/api/businesses",
              "/api/businesses/{bid}/evidence", "/api/drafts/{did}/decision", "/api/outcomes",
              "/api/connectors/{cid}/probe", "/api/cost", "/api/market/summary", "/api/watches",
              "/api/alerts/{aid}/feedback", "/api/problems"):
        assert p in paths, p


def test_web_redirects_to_login_without_session(monkeypatch):
    from leadbeast.core.config import get_settings

    monkeypatch.setattr(get_settings(), "environment", "production")
    c = TestClient(create_app())
    # the auth dependency needs a DB session; override it so no database is required
    from leadbeast.core.db import get_session

    async def _no_db():
        yield None

    c.app.dependency_overrides[get_session] = _no_db
    r = c.get("/market", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert c.get("/api/cost").status_code == 401


def test_password_hashing():
    h = hash_password("s3cret")
    assert verify_password("s3cret", h) and not verify_password("nope", h)


def test_report_renders_estimate_and_citations():
    html = HtmlPdfRenderer().html({
        "id": "a1", "status": "ready",
        "business": {"name": "Acme Plumbing", "trade": "plumbing", "city": "Brooklyn",
                     "website": None},
        "findings": [{"signal": "listing_claimed", "statement": "Listing is unclaimed.",
                      "fix": "Claim it.", "evidence_id": "0192abcd-0000", "observed_at":
                      "2026-11-01T00:00:00", "source_url": None}],
        "gap_types": ["Invisible"],
        "peers": {"ids": ["x"], "radius_km": 25, "summary": {"has_website": {"share_yes": 0.8,
                                                                              "n": 10}}},
        "payback": {"value_low": 600, "value_high": 1500, "monthly_fee": 300,
                    "multiple_low": 2.0, "multiple_high": 5.0, "provisional": True},
        "open_items": [], "score": None,
    })
    assert "Estimate." in html and "Evidence 0192abcd" in html and "provisional" in html
    assert "80% yes" in html
