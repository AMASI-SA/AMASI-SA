"""Read-only OpenAI analysis for the Mezan control center.

The route only accepts a bounded, allow-listed operational summary.  It never
passes customer PII, credentials, raw payloads, or arbitrary database content
to the model, and it exposes no write/execute capability.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException
from openai import APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError

MAX_LIST_ITEMS = 30
MAX_TEXT_LENGTH = 500
DEFAULT_ANALYSIS_TIMEOUT_SECONDS = 20.0
MIN_ANALYSIS_TIMEOUT_SECONDS = 0.05
MAX_ANALYSIS_TIMEOUT_SECONDS = 45.0
MAX_ANALYSIS_OUTPUT_TOKENS = 1200
ALLOWED_CONTEXT_KEYS = {"period", "readiness", "metrics", "gates", "coverage", "errors", "anomalies", "recommendations"}
FORBIDDEN_KEY_FRAGMENTS = {"email", "phone", "mobile", "address", "token", "secret", "password", "authorization", "cookie", "customer_name"}
logger = logging.getLogger(__name__)

class AIAnalysisIn(BaseModel):
    question: str = Field(default="حلّل حالة ميزان وحدد أهم مشكلة والخطوة التالية.", min_length=3, max_length=500)
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
    if depth > 4: return None
    if value is None or isinstance(value, (bool, int, float)): return value
    if isinstance(value, str): return value[:MAX_TEXT_LENGTH]
    if isinstance(value, list): return [_sanitize(item, depth=depth + 1) for item in value[:MAX_LIST_ITEMS]]
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:50]:
            key = str(raw_key)[:80]
            if _is_forbidden_key(key): continue
            cleaned[key] = _sanitize(raw_value, depth=depth + 1)
        return cleaned
    return str(value)[:MAX_TEXT_LENGTH]

def sanitize_context(context: dict[str, Any]) -> dict[str, Any]:
    return {key: _sanitize(context[key]) for key in ALLOWED_CONTEXT_KEYS if key in context}

ANALYSIS_SCHEMA = {"type":"object","additionalProperties":False,"properties":{"summary":{"type":"string"},"severity":{"type":"string","enum":["ok","info","warning","critical"]},"findings":{"type":"array","items":{"type":"object","additionalProperties":False,"properties":{"title":{"type":"string"},"evidence":{"type":"string"},"impact":{"type":"string"}},"required":["title","evidence","impact"]}},"next_actions":{"type":"array","items":{"type":"object","additionalProperties":False,"properties":{"priority":{"type":"string","enum":["P0","P1","P2"]},"action":{"type":"string"},"verification":{"type":"string"}},"required":["priority","action","verification"]}},"safe_to_act":{"type":"boolean"},"limitations":{"type":"array","items":{"type":"string"}}},"required":["summary","severity","findings","next_actions","safe_to_act","limitations"]}

def _analysis_timeout_seconds() -> float:
    raw_value = os.environ.get("MEZAN_OPENAI_TIMEOUT_SECONDS", "").strip()
    try:
        value = float(raw_value) if raw_value else DEFAULT_ANALYSIS_TIMEOUT_SECONDS
    except ValueError:
        value = DEFAULT_ANALYSIS_TIMEOUT_SECONDS
    return min(max(value, MIN_ANALYSIS_TIMEOUT_SECONDS), MAX_ANALYSIS_TIMEOUT_SECONDS)

def _default_client() -> AsyncOpenAI:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=503, detail="خدمة الذكاء غير مهيأة: OPENAI_API_KEY غير موجود في بيئة الإنتاج.")
    return AsyncOpenAI(
        api_key=api_key,
        max_retries=0,
        timeout=_analysis_timeout_seconds(),
    )

def make_ai_analysis_router(current_user: Callable, client_factory: Callable[[], Any] = _default_client) -> APIRouter:
    router = APIRouter(prefix="/ai", tags=["ai-readonly"])
    @router.get("/status")
    async def ai_status(user: dict = Depends(current_user)) -> dict[str, Any]:
        del user
        return {"ok": True, "configured": bool(os.environ.get("OPENAI_API_KEY", "").strip()), "mode": "read_only_analysis", "model": os.environ.get("MEZAN_OPENAI_MODEL", "gpt-5-mini"), "writes_enabled": False}
    @router.post("/analyze")
    async def analyze(body: AIAnalysisIn, user: dict = Depends(current_user)) -> dict[str, Any]:
        del user
        safe_context = sanitize_context(body.context)
        if not safe_context: raise HTTPException(status_code=422, detail="لا توجد بيانات تشغيلية آمنة للتحليل.")
        client: Any | None = None
        try:
            client = client_factory()
            response = await asyncio.wait_for(
                client.responses.create(
                    model=os.environ.get("MEZAN_OPENAI_MODEL", "gpt-5-mini"),
                    instructions="أنت محلل تشغيل ومحاسبة داخل نظام Mezan OS. أجب بالعربية وبالاعتماد حصراً على السياق المرسل. لا تخترع بيانات، ولا تطلب بيانات شخصية أو أسراراً، ولا تدّعي تنفيذ أي تعديل. رتّب المشاكل حسب الأثر. safe_to_act يكون false إذا كان الدليل غير كافٍ أو توجد بوابة حرجة. اقترح خطوات تحقق قابلة للقياس وقراءة فقط.",
                    input=json.dumps({"question": body.question, "operational_context": safe_context}, ensure_ascii=False),
                    max_output_tokens=MAX_ANALYSIS_OUTPUT_TOKENS,
                    text={"format":{"type":"json_schema","name":"mezan_readonly_analysis","strict":True,"schema":ANALYSIS_SCHEMA}},
                ),
                timeout=_analysis_timeout_seconds(),
            )
            output_text = getattr(response, "output_text", None)
            if not isinstance(output_text, str) or not output_text.strip():
                raise ValueError("empty_openai_output")
            result = AIAnalysisResult.model_validate_json(output_text).model_dump()
        except (asyncio.TimeoutError, APITimeoutError) as exc:
            logger.warning("Mezan AI analysis timed out")
            raise HTTPException(
                status_code=504,
                detail="انتهت مهلة تحليل الذكاء. لم يتم تعديل أو إرسال أي بيانات؛ حاول مرة أخرى.",
            ) from exc
        except HTTPException:
            raise
        except (ValidationError, json.JSONDecodeError, ValueError, TypeError, AttributeError) as exc:
            logger.warning(
                "Mezan AI analysis returned an invalid result: %s",
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=502,
                detail="عاد محلل الذكاء بنتيجة غير صالحة. لم يتم تعديل أو إرسال أي بيانات.",
            ) from exc
        except Exception as exc:
            logger.warning(
                "Mezan AI analysis request failed: %s",
                type(exc).__name__,
            )
            raise HTTPException(status_code=502, detail="تعذر إكمال تحليل الذكاء الآن. لم يتم تعديل أو إرسال أي بيانات.") from exc
        finally:
            if client is not None and client_factory is _default_client:
                try:
                    await client.close()
                except Exception:
                    logger.warning("Mezan AI client close failed")
        return {"ok": True, "mode": "read_only_analysis", "writes_performed": False, "analysis": result}
    return router
