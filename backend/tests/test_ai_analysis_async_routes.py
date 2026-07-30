import copy
import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Request
from openai import APITimeoutError

from ai_analysis_routes import (
    AI_ANALYSIS_JOB_COLLECTION,
    create_ai_analysis_job,
    get_ai_analysis_job,
    make_ai_analysis_router,
)


async def current_user():
    return {"id": "owner-1", "role": "owner"}


class UpdateResult:
    def __init__(self, modified_count=1):
        self.modified_count = modified_count


class MemoryCollection:
    def __init__(self):
        self.documents = {}

    @staticmethod
    def _matches(document, query):
        for key, expected in query.items():
            actual = document.get(key)
            if isinstance(expected, dict) and "$in" in expected:
                if actual not in expected["$in"]:
                    return False
            elif actual != expected:
                return False
        return True

    async def insert_one(self, document):
        self.documents[document["run_id"]] = copy.deepcopy(document)
        return object()

    async def find_one(self, query, *args, **kwargs):
        del args, kwargs
        for document in self.documents.values():
            if self._matches(document, query):
                return copy.deepcopy(document)
        return None

    async def update_one(self, query, update):
        for run_id, document in self.documents.items():
            if not self._matches(document, query):
                continue
            for key, value in update.get("$set", {}).items():
                document[key] = copy.deepcopy(value)
            for key in update.get("$unset", {}):
                document.pop(key, None)
            self.documents[run_id] = document
            return UpdateResult(1)
        return UpdateResult(0)


class MemoryDb:
    def __init__(self):
        self.collections = {AI_ANALYSIS_JOB_COLLECTION: MemoryCollection()}

    def __getitem__(self, name):
        return self.collections[name]


class FakeResponse:
    output_text = json.dumps(
        {
            "summary": "الربط يعمل والتحليل اكتمل.",
            "severity": "info",
            "findings": [],
            "next_actions": [
                {
                    "priority": "P1",
                    "action": "راجع بوابات الجاهزية.",
                    "verification": "لا تبقى بوابة حرجة دون تفسير.",
                }
            ],
            "safe_to_act": False,
            "limitations": [],
        },
        ensure_ascii=False,
    )


class FakeResponses:
    async def create(self, **kwargs):
        self.kwargs = kwargs
        return FakeResponse()


class FakeClient:
    def __init__(self):
        self.responses = FakeResponses()


class TimeoutResponses:
    async def create(self, **kwargs):
        del kwargs
        raise APITimeoutError(
            request=Request("POST", "https://api.openai.com/v1/responses")
        )


class TimeoutClient:
    def __init__(self):
        self.responses = TimeoutResponses()


@pytest.mark.asyncio
async def test_async_analysis_returns_202_then_terminal_result(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    db = MemoryDb()
    app = FastAPI()
    app.include_router(
        make_ai_analysis_router(
            current_user,
            client_factory=FakeClient,
            job_db_factory=lambda: db,
        ),
        prefix="/api",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        accepted = await client.post(
            "/api/ai/analyze-async",
            json={
                "question": "ما أهم مشكلة؟",
                "context": {
                    "metrics": {"qoyod_failed": 1},
                    "raw_payload": {"must": "not pass"},
                },
            },
        )
        assert accepted.status_code == 202
        run_id = accepted.json()["run_id"]
        assert accepted.json()["status"] == "queued"

        terminal = await client.get(f"/api/ai/analyze-async/{run_id}")

    assert terminal.status_code == 200
    payload = terminal.json()
    assert payload["status"] == "complete"
    assert payload["analysis"]["summary"] == "الربط يعمل والتحليل اكتمل."
    assert payload["writes_performed"] is False
    stored = db[AI_ANALYSIS_JOB_COLLECTION].documents[run_id]
    assert "context" not in stored
    assert "question" not in stored


@pytest.mark.asyncio
async def test_async_timeout_is_saved_as_controlled_failure(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    db = MemoryDb()
    app = FastAPI()
    app.include_router(
        make_ai_analysis_router(
            current_user,
            client_factory=TimeoutClient,
            job_db_factory=lambda: db,
        ),
        prefix="/api",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        accepted = await client.post(
            "/api/ai/analyze-async",
            json={"context": {"metrics": {"qoyod_failed": 1}}},
        )
        terminal = await client.get(
            f"/api/ai/analyze-async/{accepted.json()['run_id']}"
        )

    payload = terminal.json()
    assert payload["status"] == "failed"
    assert payload["ok"] is False
    assert payload["error"]["code"] == "ai_analysis_timeout"
    assert payload["error"]["http_status"] == 504
    assert "test-only" not in terminal.text


@pytest.mark.asyncio
async def test_active_analysis_job_is_reused_without_duplicate(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    db = MemoryDb()

    first = await create_ai_analysis_job(
        "owner-1", job_db_factory=lambda: db
    )
    second = await create_ai_analysis_job(
        "owner-1", job_db_factory=lambda: db
    )

    assert second["run_id"] == first["run_id"]
    assert second["reused"] is True
    assert len(db[AI_ANALYSIS_JOB_COLLECTION].documents) == 1


@pytest.mark.asyncio
async def test_analysis_job_is_scoped_to_authenticated_user(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    db = MemoryDb()
    accepted = await create_ai_analysis_job(
        "owner-1", job_db_factory=lambda: db
    )

    with pytest.raises(Exception) as exc_info:
        await get_ai_analysis_job(
            "owner-2",
            accepted["run_id"],
            job_db_factory=lambda: db,
        )

    assert getattr(exc_info.value, "status_code", None) == 404
