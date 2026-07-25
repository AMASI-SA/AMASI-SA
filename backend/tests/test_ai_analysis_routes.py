import json
import asyncio
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Request
from openai import APITimeoutError
import ai_analysis_routes
from ai_analysis_routes import make_ai_analysis_router, sanitize_context

async def current_user(): return {"id": "main", "role": "owner"}

class FakeResponse:
    output_text = json.dumps({"summary":"يوجد فشل واحد يحتاج مراجعة.","severity":"warning","findings":[{"title":"فشل قيود","evidence":"failed = 1","impact":"قد تتوقف المزامنة."}],"next_actions":[{"priority":"P0","action":"راجع سجل الخطأ.","verification":"يصبح failed = 0."}],"safe_to_act":False,"limitations":[]}, ensure_ascii=False)
class FakeResponses:
    def __init__(self): self.kwargs = None
    async def create(self, **kwargs): self.kwargs = kwargs; return FakeResponse()
class FakeClient:
    def __init__(self): self.responses = FakeResponses()

@pytest.mark.asyncio
async def test_analysis_is_authenticated_structured_and_read_only(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    fake = FakeClient(); app = FastAPI()
    app.include_router(make_ai_analysis_router(current_user, client_factory=lambda: fake), prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/ai/analyze", json={"question":"لماذا توقف الإرسال؟","context":{"metrics":{"qoyod_failed":1,"customer_email":"hidden@example.com"},"errors":[{"code":"unexpected","token":"secret"}],"raw_payload":{"must":"not pass"}}})
    assert response.status_code == 200
    payload = response.json(); assert payload["mode"] == "read_only_analysis"; assert payload["writes_performed"] is False
    sent = json.loads(fake.responses.kwargs["input"])
    assert "raw_payload" not in sent["operational_context"]
    assert "customer_email" not in sent["operational_context"]["metrics"]
    assert "token" not in sent["operational_context"]["errors"][0]
    assert fake.responses.kwargs["text"]["format"]["strict"] is True
    assert fake.responses.kwargs["max_output_tokens"] == 1200

def test_sanitizer_bounds_lists_and_removes_sensitive_fields():
    safe = sanitize_context({"anomalies":[{"message":"x"*900,"authorization":"Bearer secret"} for _ in range(60)],"unknown":{"value":"ignored"}})
    assert len(safe["anomalies"]) == 30; assert len(safe["anomalies"][0]["message"]) == 500
    assert "authorization" not in safe["anomalies"][0]; assert "unknown" not in safe

@pytest.mark.asyncio
async def test_status_does_not_reveal_secret(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "never-return-me")
    app = FastAPI(); app.include_router(make_ai_analysis_router(current_user), prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/ai/status")
    assert response.status_code == 200; assert response.json()["configured"] is True
    assert "never-return-me" not in response.text

def test_default_client_has_bounded_timeout_and_no_retries(monkeypatch):
    captured = {}

    class ConfiguredClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("MEZAN_OPENAI_TIMEOUT_SECONDS", "12")
    monkeypatch.setattr(ai_analysis_routes, "AsyncOpenAI", ConfiguredClient)

    ai_analysis_routes._default_client()

    assert captured["api_key"] == "test-only"
    assert captured["max_retries"] == 0
    assert captured["timeout"] == 12

class HangingResponses:
    async def create(self, **kwargs):
        del kwargs
        await asyncio.sleep(60)

class HangingClient:
    def __init__(self):
        self.responses = HangingResponses()

@pytest.mark.asyncio
async def test_analysis_timeout_returns_controlled_json(monkeypatch):
    monkeypatch.setenv("MEZAN_OPENAI_TIMEOUT_SECONDS", "0.05")
    app = FastAPI()
    app.include_router(
        make_ai_analysis_router(current_user, client_factory=HangingClient),
        prefix="/api",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai/analyze",
            json={"context": {"metrics": {"qoyod_failed": 1}}},
        )
    assert response.status_code == 504
    assert response.headers["content-type"].startswith("application/json")
    assert "انتهت مهلة" in response.json()["detail"]

class SdkTimeoutResponses:
    async def create(self, **kwargs):
        del kwargs
        raise APITimeoutError(
            request=Request("POST", "https://api.openai.com/v1/responses")
        )

class SdkTimeoutClient:
    def __init__(self):
        self.responses = SdkTimeoutResponses()

@pytest.mark.asyncio
async def test_sdk_timeout_also_returns_504_json():
    app = FastAPI()
    app.include_router(
        make_ai_analysis_router(current_user, client_factory=SdkTimeoutClient),
        prefix="/api",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai/analyze",
            json={"context": {"metrics": {"qoyod_failed": 1}}},
        )
    assert response.status_code == 504
    assert response.headers["content-type"].startswith("application/json")

@pytest.mark.asyncio
async def test_client_factory_failure_returns_controlled_json():
    def failing_factory():
        raise RuntimeError("client init failed")

    app = FastAPI()
    app.include_router(
        make_ai_analysis_router(current_user, client_factory=failing_factory),
        prefix="/api",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai/analyze",
            json={"context": {"metrics": {"qoyod_failed": 1}}},
        )
    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/json")
    assert "لم يتم تعديل أو إرسال" in response.json()["detail"]

class InvalidResponse:
    output_text = "<html>upstream error</html>"

class InvalidResponses:
    async def create(self, **kwargs):
        del kwargs
        return InvalidResponse()

class InvalidClient:
    def __init__(self):
        self.responses = InvalidResponses()

@pytest.mark.asyncio
async def test_invalid_model_output_returns_controlled_json():
    app = FastAPI()
    app.include_router(
        make_ai_analysis_router(current_user, client_factory=InvalidClient),
        prefix="/api",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai/analyze",
            json={"context": {"metrics": {"qoyod_failed": 1}}},
        )
    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/json")
    assert "نتيجة غير صالحة" in response.json()["detail"]
