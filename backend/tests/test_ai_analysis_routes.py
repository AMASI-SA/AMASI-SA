import json
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
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
