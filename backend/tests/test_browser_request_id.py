import asyncio

from browser_security import BrowserSecurityMiddleware


async def receive():
    return {"type": "http.request", "body": b"", "more_body": False}


def run_middleware(scope, app):
    messages = []

    async def send(message):
        messages.append(message)

    middleware = BrowserSecurityMiddleware(
        app,
        trusted_origins={"https://mezansalla.com"},
    )
    asyncio.run(middleware(scope, receive, send))
    return messages


def response_headers(messages):
    start = next(message for message in messages if message["type"] == "http.response.start")
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in start.get("headers", [])
    }


def test_request_id_is_echoed_and_total_timing_is_added():
    async def app(_scope, _receive, send):
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [],
        })
        await send({"type": "http.response.body", "body": b"{}", "more_body": False})

    messages = run_middleware({
        "type": "http",
        "method": "GET",
        "scheme": "https",
        "path": "/api/dashboard-v2",
        "headers": [(b"x-request-id", b"network-request-123")],
        "state": {},
    }, app)
    headers = response_headers(messages)

    assert headers["x-request-id"] == "network-request-123"
    assert headers["server-timing"].startswith("total;dur=")
    assert headers["strict-transport-security"].startswith("max-age=31536000")


def test_dashboard_server_timing_from_route_is_not_overwritten():
    async def app(_scope, _receive, send):
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"server-timing", b"auth;dur=1.00, db;dur=2.00")],
        })
        await send({"type": "http.response.body", "body": b"{}", "more_body": False})

    messages = run_middleware({
        "type": "http",
        "method": "GET",
        "scheme": "https",
        "path": "/api/dashboard-v2",
        "headers": [],
        "state": {},
    }, app)
    headers = response_headers(messages)

    assert headers["server-timing"] == "auth;dur=1.00, db;dur=2.00"
    assert headers["x-request-id"]
