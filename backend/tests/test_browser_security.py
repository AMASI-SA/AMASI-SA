from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from browser_security import BrowserSecurityMiddleware


async def ok(request):
    return JSONResponse({"ok": True})


def _client(base_url: str = "https://mezansalla.com"):
    app = Starlette(routes=[
        Route("/read", ok, methods=["GET"]),
        Route("/mutate", ok, methods=["POST"]),
    ])
    app.add_middleware(
        BrowserSecurityMiddleware,
        trusted_origins={"https://mezansalla.com"},
    )
    return TestClient(app, base_url=base_url)


def test_cross_site_cookie_mutation_is_blocked():
    client = _client()
    response = client.post(
        "/mutate",
        headers={
            "Origin": "https://evil.example",
            "Sec-Fetch-Site": "cross-site",
            "Cookie": "access_token=example",
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "csrf_origin_denied"


def test_trusted_cookie_mutation_is_allowed():
    client = _client()
    response = client.post(
        "/mutate",
        headers={
            "Origin": "https://mezansalla.com",
            "Sec-Fetch-Site": "same-origin",
            "Cookie": "access_token=example",
        },
    )
    assert response.status_code == 200


def test_bearer_client_without_cookie_is_not_treated_as_csrf():
    client = _client()
    response = client.post(
        "/mutate",
        headers={
            "Origin": "https://api-client.example",
            "Authorization": "Bearer example",
        },
    )
    assert response.status_code == 200


def test_api_security_headers_are_present():
    response = _client().get("/read")
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-permitted-cross-domain-policies"] == "none"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "microphone=()" in response.headers["permissions-policy"]
    assert response.headers["strict-transport-security"] == (
        "max-age=31536000; includeSubDomains"
    )


def test_hsts_is_not_emitted_over_plain_http():
    response = _client(base_url="http://mezansalla.com").get("/read")
    assert "strict-transport-security" not in response.headers


def test_forwarded_https_receives_hsts():
    response = _client(base_url="http://internal").get(
        "/read",
        headers={"X-Forwarded-Proto": "https"},
    )
    assert response.headers["strict-transport-security"].startswith("max-age=31536000")
