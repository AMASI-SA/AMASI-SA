"""Public content seams are bounded, owner-only and separate from ads OAuth."""
import httpx
import pytest
from fastapi import APIRouter, FastAPI, HTTPException

from integrations_control_center.tiktok_connections import attach_tiktok_connection_routes
from integrations_control_center.tiktok_native_reporting_routes import attach_tiktok_native_reporting_routes


def app_for(user, db=None):
    async def current_user():
        return user

    def require_owner(value):
        if value.get("role") != "owner":
            raise HTTPException(403, detail={"code": "owner_only"})
        return value

    app, router = FastAPI(), APIRouter(prefix="/api/integrations-v2")
    database = db if db is not None else object()
    attach_tiktok_connection_routes(router, database, current_user, require_owner)
    attach_tiktok_native_reporting_routes(router, database, current_user, require_owner)
    app.include_router(router)
    return app


@pytest.mark.asyncio
async def test_content_preview_is_a_real_typed_public_route():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "owner-1", "role": "owner"})), base_url="http://test") as client:
        response = await client.post("/api/integrations-v2/tiktok/content/proposals", json={})
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", [
    ("POST", "/creators/connect/start", None),
    ("GET", "/creators", None),
    ("POST", "/creators/" + "a" * 64 + "/readiness", {}),
    ("POST", "/proposals/proposal-1/approve-and-publish", {"confirmation_digest": "a" * 64}),
])
async def test_non_owner_cannot_reach_content_or_creator_authorization(method, path, body):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "staff-1", "role": "staff"})), base_url="http://test") as client:
        response = await client.request(method, "/api/integrations-v2/tiktok/content" + path, json=body)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_content_request_is_rejected_before_parsing_large_media_sized_json():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "owner-1", "role": "owner"})), base_url="http://test") as client:
        response = await client.post("/api/integrations-v2/tiktok/content/proposals", content=b'{"caption":"' + b'a' * 32768 + b'"}', headers={"Content-Type": "application/json"})
    assert response.status_code == 413


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["identity", "settings", "media_properties"])
@pytest.mark.parametrize("known", [False, True])
async def test_publishing_read_failure_identifies_phase_without_leaking_or_retrying(monkeypatch, caplog, phase, known):
    from integrations_control_center import tiktok_content_publishing as publishing

    calls = []
    secret = "private-token-and-provider-url"
    failure = HTTPException(409, detail={"code": "tiktok_creator_missing_permission", "message": "راجع الصلاحيات."}) if known else RuntimeError(secret)

    async def creator(*args):
        calls.append("identity")
        if phase == "identity":
            raise failure
        return {}, "fixture-token", "fixture-creator"

    class Provider:
        async def call(self, method, path, **kwargs):
            assert method == "GET"
            current = "settings" if path == "/business/video/settings/" else "media_properties"
            calls.append(current)
            if current == phase:
                raise failure
            return {"privacy_level_options": ["SELF_ONLY"], "max_video_post_duration_sec": 60, "comment_disabled": False, "duet_disabled": False, "stitch_disabled": False}

    monkeypatch.setattr(publishing, "verified_creator", creator)
    monkeypatch.setenv("TIKTOK_MARKETING_APP_ID", "fixture-app")
    monkeypatch.setenv("TIKTOK_MARKETING_APP_SECRET", secret)
    with pytest.raises(HTTPException) as caught:
        await publishing.context(object(), "owner", "creator", Provider())
    assert caught.value.status_code == (409 if known else 502)
    assert caught.value.detail["phase"] == phase
    assert caught.value.detail["code"] == ("tiktok_creator_missing_permission" if known else "tiktok_publishing_context_failed")
    assert secret not in str(caught.value.detail) + caplog.text
    assert calls == ["identity", "settings", "media_properties"][:["identity", "settings", "media_properties"].index(phase) + 1]


@pytest.mark.asyncio
async def test_publishing_context_keeps_existing_timeout_boundary(monkeypatch):
    from integrations_control_center import tiktok_content_publishing as publishing

    async def timeout(*args):
        raise TimeoutError()

    monkeypatch.setattr(publishing, "verified_creator", timeout)
    with pytest.raises(TimeoutError):
        await publishing.context(object(), "owner", "creator", object())


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    {"code": "tiktok_creator_provider_error", "phase": "identity"},
    {"code": "private-token-and-provider-url", "phase": "private-token-and-provider-url"},
    {"code": ["private-token-and-provider-url"], "phase": {"private-token-and-provider-url": True}},
])
async def test_readiness_report_preserves_blocked_state_through_gateway_errors(monkeypatch, failure):
    from integrations_control_center import tiktok_content_publishing as publishing

    calls = []

    async def check(db, user_id, creator_ref):
        calls.append((user_id, creator_ref))
        raise HTTPException(502, detail={**failure, "message": "private-token-and-provider-url"})

    monkeypatch.setattr(publishing, "readiness", check)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "owner-1", "role": "owner"})), base_url="http://test") as client:
        response = await client.post("/api/integrations-v2/tiktok/content/creators/" + "a" * 64 + "/readiness", json={})
    assert response.status_code == 200
    result = response.json()
    assert result["ready"] is False
    assert result["provider_write_reached"] is False
    assert result["diagnostic"]["status"] == 502
    assert "private-token-and-provider-url" not in response.text
    assert result["diagnostic"]["phase"] in {"identity", "request"}
    assert calls == [("owner-1", "a" * 64)]
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_readiness_report_success_is_additive_and_old_verify_keeps_error_status(monkeypatch):
    from integrations_control_center import tiktok_content_publishing as publishing

    async def check(*args):
        return {"creator_ref": "a" * 64, "settings": {"comment_disabled": True}}

    monkeypatch.setattr(publishing, "readiness", check)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "owner-1", "role": "owner"})), base_url="http://test") as client:
        response = await client.post("/api/integrations-v2/tiktok/content/creators/" + "a" * 64 + "/readiness", json={})
        assert response.status_code == 200
        assert response.json()["ready"] is True
        assert response.json()["settings"] == {"comment_disabled": True}

        async def denied(*args):
            raise HTTPException(502, detail={"code": "tiktok_creator_provider_error", "phase": "settings"})

        monkeypatch.setattr(publishing, "readiness", denied)
        previous = await client.post("/api/integrations-v2/tiktok/content/creators/" + "a" * 64 + "/verify", json={})
        assert previous.status_code == 502


@pytest.mark.parametrize("provider_limit", [900, 1800, 3600])
def test_account_duration_privilege_is_capped_to_mezan_limit(provider_limit):
    from integrations_control_center.tiktok_content_publishing import settings_proof

    native = {"privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"],
              "max_video_post_duration_sec": provider_limit,
              "comment_disabled": False, "duet_disabled": True, "stitch_disabled": False}
    result = settings_proof(native)
    assert result["max_video_post_duration_sec"] == 600
    assert native["max_video_post_duration_sec"] == provider_limit
    assert result["duet_disabled"] is True


@pytest.mark.parametrize("field,value,issue", [
    ("privacy_level_options", [], "no_options"),
    ("privacy_level_options", ["private-token-and-provider-url"], "unsupported_value"),
    ("max_video_post_duration_sec", True, "invalid_type"),
    ("max_video_post_duration_sec", "private-token-and-provider-url", "invalid_type"),
    ("comment_disabled", 0, "invalid_type"),
    ("duet_disabled", "private-token-and-provider-url", "invalid_type"),
])
def test_settings_still_require_proven_privacy_and_interaction_types(field, value, issue):
    from integrations_control_center.tiktok_content_publishing import settings_proof

    native = {"privacy_level_options": ["SELF_ONLY"], "max_video_post_duration_sec": 600,
              "comment_disabled": False, "duet_disabled": False, "stitch_disabled": False}
    native[field] = value
    with pytest.raises(HTTPException) as caught:
        settings_proof(native)
    assert caught.value.detail["settings_field"] == field
    assert caught.value.detail["settings_issue"] == issue
    assert "private-token-and-provider-url" not in str(caught.value.detail)


@pytest.mark.asyncio
async def test_readiness_field_diagnostics_are_fixed_metadata_and_never_native_values(monkeypatch):
    from integrations_control_center import tiktok_content_publishing as publishing

    async def check(*args):
        raise HTTPException(502, detail={"code": "tiktok_creator_settings_unproven", "phase": "settings",
            "settings_field": "comment_disabled", "settings_issue": "invalid_type", "observed_type": "int",
            "message": "private-token-and-provider-url", "raw_value": "private-token-and-provider-url"})

    monkeypatch.setattr(publishing, "readiness", check)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "owner-1", "role": "owner"})), base_url="http://test") as client:
        response = await client.post("/api/integrations-v2/tiktok/content/creators/" + "a" * 64 + "/readiness", json={})
    assert response.status_code == 200 and response.json()["ready"] is False
    diagnostic = response.json()["diagnostic"]
    assert (diagnostic["field"], diagnostic["issue"], diagnostic["observed_type"]) == ("comment_disabled", "invalid_type", "int")
    assert "private-token-and-provider-url" not in response.text
