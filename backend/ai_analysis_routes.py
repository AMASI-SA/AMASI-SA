"""Read-only OpenAI analysis for the Mezan control center.

The route only accepts a bounded, allow-listed operational summary. It never
passes customer PII, credentials, raw payloads, or arbitrary database content
to the model, and it exposes no write/execute capability.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from openai import APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError

from ai_provider_status import openai_runtime_status

MAX_LIST_ITEMS = 30
MAX_TEXT_LENGTH = 500
DEFAULT_ANALYSIS_TIMEOUT_SECONDS = 20.0
MIN_ANALYSIS_TIMEOUT_SECONDS = 0.05
MAX_ANALYSIS_TIMEOUT_SECONDS = 45.0
MAX_ANALYSIS_OUTPUT_TOKENS = 1200
AI_ANALYSIS_JOB_COLLECTION = "mezan_ai_analysis_jobs"
AI_ANALYSIS_JOB_STALE_SECONDS = 120
ACTIVE_JOB_STATUSES = ("queued", "running")
ALLOWED_CONTEXT_KEYS = {
    "period",
    "readiness",
    "metrics",
    "gates",
    "coverage",
    "errors",
    "anomalies",
    "recommendations",
}
FORBIDDEN_KEY_FRAGMENTS = {
    "email",
    "phone",
    "mobile",
    "address",
    "token",
    "secret",
    "password",
    "authorization",
    "cookie",
    "customer_name",
}
logger = logging.getLogger(__name__)
_job_client: AsyncIOMotorClient | None = None
_job_db: Any | None = None


class AIAnalysisIn(BaseModel):
    question: str = Field(
        default="حلّل حالة ميزان وحدد أهم مشكلة والخطوة التالية.",
        min_length=3,
        max_length=500,
    )
    context: dict[str, Any] = Field(default_factory=dict)


class AIFinding(BaseModel):
    title: str
    evidence: str
    impact: str


class AINextAction(BaseModel):
    priority: Literal["P0", "P1", "P2"]
    action: str
    verification: str


class AIAnalysisResult(BaseModel):
    summary: str
    severity: Literal["ok", "info", "warning", "critical"]
    findings: list[AIFinding]
    next_actions: list[AINextAction]
    safe_to_act: bool
    limitations: list[str]


def _is_forbidden_key(key: str) -> bool:
    normalized = key.strip().lower()
    return any(fragment in normalized for fragment in FORBIDDEN_KEY_FRAGMENTS)


def _sanitize(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_TEXT_LENGTH]
    if isinstance(value, list):
        return [
            _sanitize(item, depth=depth + 1)
            for item in value[:MAX_LIST_ITEMS]
        ]
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:50]:
            key = str(raw_key)[:80]
            if _is_forbidden_key(key):
                continue
            cleaned[key] = _sanitize(raw_value, depth=depth + 1)
        return cleaned
    return str(value)[:MAX_TEXT_LENGTH]


def sanitize_context(context: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _sanitize(context[key])
        for key in ALLOWED_CONTEXT_KEYS
        if key in context
    }


ANALYSIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "severity": {
            "type": "string",
            "enum": ["ok", "info", "warning", "critical"],
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "evidence": {"type": "string"},
                    "impact": {"type": "string"},
                },
                "required": ["title", "evidence", "impact"],
            },
        },
        "next_actions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "priority": {
                        "type": "string",
                        "enum": ["P0", "P1", "P2"],
                    },
                    "action": {"type": "string"},
                    "verification": {"type": "string"},
                },
                "required": ["priority", "action", "verification"],
            },
        },
        "safe_to_act": {"type": "boolean"},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "summary",
        "severity",
        "findings",
        "next_actions",
        "safe_to_act",
        "limitations",
    ],
}


def _analysis_timeout_seconds() -> float:
    raw_value = os.environ.get("MEZAN_OPENAI_TIMEOUT_SECONDS", "").strip()
    try:
        value = (
            float(raw_value)
            if raw_value
            else DEFAULT_ANALYSIS_TIMEOUT_SECONDS
        )
    except ValueError:
        value = DEFAULT_ANALYSIS_TIMEOUT_SECONDS
    return min(
        max(value, MIN_ANALYSIS_TIMEOUT_SECONDS),
        MAX_ANALYSIS_TIMEOUT_SECONDS,
    )


def _default_client() -> AsyncOpenAI:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "خدمة الذكاء غير مهيأة: OPENAI_API_KEY غير موجود "
                "في بيئة الإنتاج."
            ),
        )
    return AsyncOpenAI(
        api_key=api_key,
        max_retries=0,
        timeout=_analysis_timeout_seconds(),
    )


def _default_job_db() -> Any:
    global _job_client, _job_db
    if _job_db is not None:
        return _job_db
    mongo_url = os.environ.get("MONGO_URL", "").strip()
    db_name = os.environ.get("DB_NAME", "").strip()
    if not mongo_url or not db_name:
        raise HTTPException(
            status_code=503,
            detail="مخزن مهام محلل ميزان غير مهيأ في بيئة الإنتاج.",
        )
    _job_client = AsyncIOMotorClient(mongo_url)
    _job_db = _job_client[db_name]
    return _job_db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _user_id(user: dict[str, Any]) -> str:
    for key in ("id", "_id", "user_id"):
        value = user.get(key)
        if value is not None and str(value).strip():
            return str(value)
    raise HTTPException(status_code=401, detail="تعذر تحديد هوية المستخدم.")


def _prepare_analysis(body: AIAnalysisIn) -> dict[str, Any]:
    safe_context = sanitize_context(body.context)
    if not safe_context:
        raise HTTPException(
            status_code=422,
            detail="لا توجد بيانات تشغيلية آمنة للتحليل.",
        )
    return {"question": body.question, "context": safe_context}


async def _run_openai_analysis(
    prepared: dict[str, Any],
    client_factory: Callable[[], Any],
) -> dict[str, Any]:
    client: Any | None = None
    try:
        client = client_factory()
        response = await asyncio.wait_for(
            client.responses.create(
                model=os.environ.get("MEZAN_OPENAI_MODEL", "gpt-5-mini"),
                instructions=(
                    "أنت محلل تشغيل ومحاسبة داخل نظام Mezan OS. أجب "
                    "بالعربية وبالاعتماد حصراً على السياق المرسل. لا تخترع "
                    "بيانات، ولا تطلب بيانات شخصية أو أسراراً، ولا تدّعي "
                    "تنفيذ أي تعديل. رتّب المشاكل حسب الأثر. safe_to_act "
                    "يكون false إذا كان الدليل غير كافٍ أو توجد بوابة حرجة. "
                    "اقترح خطوات تحقق قابلة للقياس وقراءة فقط."
                ),
                input=json.dumps(
                    {
                        "question": prepared["question"],
                        "operational_context": prepared["context"],
                    },
                    ensure_ascii=False,
                ),
                max_output_tokens=MAX_ANALYSIS_OUTPUT_TOKENS,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "mezan_readonly_analysis",
                        "strict": True,
                        "schema": ANALYSIS_SCHEMA,
                    }
                },
                timeout=_analysis_timeout_seconds(),
            ),
            timeout=_analysis_timeout_seconds(),
        )
        output_text = getattr(response, "output_text", None)
        if not isinstance(output_text, str) or not output_text.strip():
            raise ValueError("empty_openai_output")
        return AIAnalysisResult.model_validate_json(output_text).model_dump()
    except (asyncio.TimeoutError, APITimeoutError) as exc:
        raise HTTPException(
            status_code=504,
            detail=(
                "انتهت مهلة تحليل الذكاء. لم يتم تعديل أو إرسال أي بيانات؛ "
                "حاول مرة أخرى."
            ),
        ) from exc
    except HTTPException:
        raise
    except (
        ValidationError,
        json.JSONDecodeError,
        ValueError,
        TypeError,
        AttributeError,
    ) as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "عاد محلل الذكاء بنتيجة غير صالحة. لم يتم تعديل أو إرسال "
                "أي بيانات."
            ),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "تعذر إكمال تحليل الذكاء الآن. لم يتم تعديل أو إرسال "
                "أي بيانات."
            ),
        ) from exc
    finally:
        if client is not None and client_factory is _default_client:
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                logger.warning("Mezan AI client close failed")


def _job_error(exc: HTTPException) -> dict[str, Any]:
    detail = exc.detail
    if isinstance(detail, dict):
        message = str(detail.get("message") or detail.get("detail") or detail)
    else:
        message = str(detail)
    code_by_status = {
        422: "ai_analysis_invalid_request",
        502: "ai_analysis_upstream_failed",
        503: "ai_analysis_not_configured",
        504: "ai_analysis_timeout",
    }
    return {
        "code": code_by_status.get(exc.status_code, "ai_analysis_failed"),
        "message": message,
        "http_status": exc.status_code,
        "retryable": exc.status_code >= 500,
    }


def _safe_job(document: dict[str, Any]) -> dict[str, Any]:
    status = str(document.get("status") or "failed")
    error = document.get("error")
    safe_error = None
    if isinstance(error, dict):
        safe_error = {
            "code": error.get("code"),
            "message": error.get("message"),
            "http_status": int(error.get("http_status") or 502),
            "retryable": bool(error.get("retryable")),
        }
    return {
        "ok": status != "failed",
        "run_id": document.get("run_id"),
        "status": status,
        "mode": "read_only_analysis",
        "writes_performed": False,
        "created_at": document.get("created_at"),
        "started_at": document.get("started_at"),
        "finished_at": document.get("finished_at"),
        "analysis": (
            document.get("analysis")
            if status == "complete"
            and isinstance(document.get("analysis"), dict)
            else None
        ),
        "error": safe_error,
    }


async def _recover_stale_job(
    collection: Any,
    user_id: str,
    document: dict[str, Any],
) -> dict[str, Any]:
    if document.get("status") not in ACTIVE_JOB_STATUSES:
        return document
    expires_at = _parse_datetime(document.get("expires_at"))
    if expires_at and expires_at > _utcnow():
        return document
    finished_at = _iso(_utcnow())
    error = {
        "code": "ai_analysis_job_stale",
        "message": (
            "توقفت مهمة التحليل قبل إكمالها. لم يتم تعديل أو إرسال أي بيانات."
        ),
        "http_status": 504,
        "retryable": True,
    }
    await collection.update_one(
        {
            "user_id": user_id,
            "run_id": document.get("run_id"),
            "status": {"$in": list(ACTIVE_JOB_STATUSES)},
        },
        {
            "$set": {
                "status": "failed",
                "finished_at": finished_at,
                "error": error,
            }
        },
    )
    return {**document, "status": "failed", "finished_at": finished_at, "error": error}


async def create_ai_analysis_job(
    user_id: str,
    *,
    job_db_factory: Callable[[], Any] = _default_job_db,
) -> dict[str, Any]:
    status = openai_runtime_status()
    if not status["connected"]:
        raise HTTPException(
            status_code=503,
            detail="خدمة الذكاء غير مهيأة في بيئة الإنتاج.",
        )
    db = job_db_factory()
    collection = db[AI_ANALYSIS_JOB_COLLECTION]
    active = await collection.find_one(
        {
            "user_id": user_id,
            "status": {"$in": list(ACTIVE_JOB_STATUSES)},
        }
    )
    if active:
        active = await _recover_stale_job(collection, user_id, active)
        if active.get("status") in ACTIVE_JOB_STATUSES:
            return {**_safe_job(active), "reused": True}

    now = _utcnow()
    document = {
        "run_id": str(uuid.uuid4()),
        "user_id": user_id,
        "status": "queued",
        "created_at": _iso(now),
        "started_at": None,
        "finished_at": None,
        "expires_at": _iso(
            now + timedelta(seconds=AI_ANALYSIS_JOB_STALE_SECONDS)
        ),
        "analysis": None,
        "error": None,
        "mode": "read_only_analysis",
        "writes_performed": False,
    }
    await collection.insert_one(document)
    return _safe_job(document)


async def execute_ai_analysis_job(
    user_id: str,
    run_id: str,
    prepared: dict[str, Any],
    client_factory: Callable[[], Any] = _default_client,
    job_db_factory: Callable[[], Any] = _default_job_db,
) -> None:
    db = job_db_factory()
    collection = db[AI_ANALYSIS_JOB_COLLECTION]
    started_at = _iso(_utcnow())
    await collection.update_one(
        {"user_id": user_id, "run_id": run_id, "status": "queued"},
        {"$set": {"status": "running", "started_at": started_at}},
    )
    try:
        result = await _run_openai_analysis(prepared, client_factory)
    except HTTPException as exc:
        await collection.update_one(
            {"user_id": user_id, "run_id": run_id},
            {
                "$set": {
                    "status": "failed",
                    "finished_at": _iso(_utcnow()),
                    "error": _job_error(exc),
                    "analysis": None,
                }
            },
        )
        return
    except Exception:  # noqa: BLE001
        await collection.update_one(
            {"user_id": user_id, "run_id": run_id},
            {
                "$set": {
                    "status": "failed",
                    "finished_at": _iso(_utcnow()),
                    "error": {
                        "code": "ai_analysis_worker_failed",
                        "message": (
                            "تعذر إكمال مهمة التحليل. لم يتم تعديل أو إرسال "
                            "أي بيانات."
                        ),
                        "http_status": 502,
                        "retryable": True,
                    },
                    "analysis": None,
                }
            },
        )
        return

    await collection.update_one(
        {"user_id": user_id, "run_id": run_id},
        {
            "$set": {
                "status": "complete",
                "finished_at": _iso(_utcnow()),
                "analysis": result,
                "error": None,
            }
        },
    )


async def get_ai_analysis_job(
    user_id: str,
    run_id: str,
    *,
    job_db_factory: Callable[[], Any] = _default_job_db,
) -> dict[str, Any]:
    db = job_db_factory()
    collection = db[AI_ANALYSIS_JOB_COLLECTION]
    document = await collection.find_one(
        {"user_id": user_id, "run_id": run_id}
    )
    if not document:
        raise HTTPException(
            status_code=404,
            detail="تعذر العثور على مهمة التحليل المطلوبة.",
        )
    document = await _recover_stale_job(collection, user_id, document)
    return _safe_job(document)


def make_ai_analysis_router(
    current_user: Callable,
    client_factory: Callable[[], Any] = _default_client,
    job_db_factory: Callable[[], Any] = _default_job_db,
) -> APIRouter:
    router = APIRouter(prefix="/ai", tags=["ai-readonly"])

    @router.get("/status")
    async def ai_status(user: dict = Depends(current_user)) -> dict[str, Any]:
        del user
        status = openai_runtime_status()
        return {
            "ok": True,
            **status,
            "configured": status["connected"],
            "mode": "read_only_analysis",
            "model": status["analysis"]["model"],
            "writes_enabled": False,
        }

    @router.post("/analyze")
    async def analyze(
        body: AIAnalysisIn,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        del user
        result = await _run_openai_analysis(
            _prepare_analysis(body), client_factory
        )
        return {
            "ok": True,
            "mode": "read_only_analysis",
            "writes_performed": False,
            "analysis": result,
        }

    @router.post("/analyze-async", status_code=202)
    async def start_async_analysis(
        body: AIAnalysisIn,
        background_tasks: BackgroundTasks,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        prepared = _prepare_analysis(body)
        user_id = _user_id(user)
        accepted = await create_ai_analysis_job(
            user_id,
            job_db_factory=job_db_factory,
        )
        if not accepted.get("reused"):
            background_tasks.add_task(
                execute_ai_analysis_job,
                user_id,
                str(accepted["run_id"]),
                prepared,
                client_factory,
                job_db_factory,
            )
        return accepted

    @router.get("/analyze-async/{run_id}")
    async def read_async_analysis(
        run_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        return await get_ai_analysis_job(
            _user_id(user),
            run_id,
            job_db_factory=job_db_factory,
        )

    return router
