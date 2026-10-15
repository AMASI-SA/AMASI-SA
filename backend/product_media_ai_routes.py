"""Governed OpenAI image execution for Mezan Product V2.

Jobs are created as proposals, executed only on an explicit human action, stored
as temporary Mezan media, and never published directly to Salla. Generated
results must be added to the ordinary media draft and pass its approval flow.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import os
import secrets
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import unquote, urljoin, urlparse

import httpx
from bson.binary import Binary
from fastapi import APIRouter, Body, Depends, HTTPException

from ai_provider_status import openai_runtime_status
from ai_store_access_contract import (
    find_role_assignment,
    role_assignment_owner_user_id,
)
from ai_store_operations_foundation import AI_ACTION_LOG
from product_media_draft_routes import MEDIA_DRAFTS
from product_media_upload_routes import (
    ALLOWED_TYPES,
    MAX_IMAGE_BYTES,
    MEDIA_UPLOADS,
    UPLOAD_TTL_DAYS,
    _detected_type,
)
from product_v2_routes import PRODUCTS

AI_MEDIA_JOBS = "mezan_product_media_ai_jobs_v2"
MAX_PROMPT_LENGTH = 1200
MAX_SOURCE_IMAGE_BYTES = 15 * 1024 * 1024
OPENAI_API_BASE = "https://api.openai.com/v1"
PENDING_STATUSES = {
    "waiting_provider",
    "waiting_image_engine",
    "ready_for_execution",
    "proposal_created",
}
EXECUTABLE_STATUSES = PENDING_STATUSES

OPERATION_CATALOG: dict[str, dict[str, Any]] = {
    "improve_quality": {
        "label": "تحسين الجودة والإضاءة",
        "permission": "products.media.ai_edit",
        "execution_permission": "products.ai.execute_low_risk",
        "requires_source": True,
        "risk": "low",
    },
    "remove_background": {
        "label": "إزالة الخلفية",
        "permission": "products.media.ai_edit",
        "execution_permission": "products.ai.execute_low_risk",
        "requires_source": True,
        "risk": "low",
    },
    "studio_background": {
        "label": "إنشاء خلفية استوديو",
        "permission": "products.media.ai_edit",
        "execution_permission": "products.ai.execute_high_risk",
        "requires_source": True,
        "risk": "medium",
    },
    "ad_creative": {
        "label": "إنشاء صورة إعلانية",
        "permission": "products.media.ai_generate",
        "execution_permission": "products.ai.execute_high_risk",
        "requires_source": True,
        "risk": "medium",
    },
    "generate_from_prompt": {
        "label": "إنشاء صورة من وصف",
        "permission": "products.media.ai_generate",
        "execution_permission": "products.ai.execute_high_risk",
        "requires_source": False,
        "risk": "medium",
    },
    "suggest_alt": {
        "label": "اقتراح ALT للصورة",
        "permission": "products.media.ai_edit",
        "execution_permission": "products.ai.execute_low_risk",
        "requires_source": True,
        "risk": "low",
    },
}


class AiMediaExecutionError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 502):
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.status_code = status_code


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _now_dt().isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bounded_timeout() -> float:
    raw = os.environ.get("MEZAN_OPENAI_IMAGE_TIMEOUT_SECONDS", "").strip()
    try:
        value = float(raw) if raw else 120.0
    except ValueError:
        value = 120.0
    return min(max(value, 30.0), 180.0)


def _image_quality() -> str:
    quality = os.environ.get("MEZAN_OPENAI_IMAGE_QUALITY", "medium").strip().lower()
    return quality if quality in {"low", "medium", "high", "auto"} else "medium"


def _image_size(aspect_ratio: str) -> str:
    return {
        "1:1": "1024x1024",
        "4:5": "1024x1536",
        "9:16": "1024x1536",
        "16:9": "1536x1024",
        "original": "auto",
    }.get(aspect_ratio, "auto")


def image_provider_status() -> dict[str, Any]:
    runtime = openai_runtime_status()
    images = runtime["images"]
    return {
        "provider": "openai",
        "connected": runtime["connected"],
        "connection_status": runtime["connection_status"],
        "state": runtime["state"],
        "label_ar": runtime["label_ar"],
        "analysis_ready": runtime["analysis"]["ready"],
        "analysis_model": runtime["analysis"]["model"],
        "image_policy_enabled": images["policy_enabled"],
        "image_model": images["model"],
        "ready": images["ready"],
        "execution_available": images["execution_available"],
        "mode": images["mode"],
        "human_approval_required": True,
        "direct_publish_allowed": False,
    }


def _has_permission(user: dict[str, Any], assignment: dict[str, Any] | None, permission: str) -> bool:
    if str(user.get("role") or "").lower() == "owner" or user.get("is_owner"):
        return True
    if not assignment or assignment.get("enabled") is False:
        return False
    return permission in set(assignment.get("effective_permissions") or [])


async def _product(db: Any, user_id: str, product_id: str) -> dict[str, Any]:
    row = await db[PRODUCTS].find_one({
        "user_id": user_id,
        "$or": [{"id": product_id}, {"mezan_product_id": product_id}, {"salla_product_id": product_id}],
    }, {"_id": 0})
    if not row:
        raise HTTPException(status_code=404, detail={"code": "product_v2_not_found"})
    return row


async def _assignment(db: Any, user: dict[str, Any]) -> dict[str, Any] | None:
    return await find_role_assignment(
        db,
        owner_user_id=role_assignment_owner_user_id(user),
        user_id=str(user.get("id") or ""),
    )


def _safe_source_row(raw: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    url = _text(raw.get("url"))
    if not url:
        return None
    return {
        "id": _text(raw.get("id")) or None,
        "url": url,
        "alt": _text(raw.get("alt"))[:250],
        "is_main": bool(raw.get("is_main") or raw.get("main") or index == 0),
        "source": _text(raw.get("source")) or ("salla" if raw.get("id") else "external_url"),
    }


async def _available_source_rows(db: Any, user_id: str, product: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_many(values: Any) -> None:
        for index, raw in enumerate(values if isinstance(values, list) else []):
            row = _safe_source_row(raw, index)
            if not row or row["url"] in seen:
                continue
            seen.add(row["url"])
            rows.append(row)

    add_many(product.get("images") or [])
    draft = await db[MEDIA_DRAFTS].find_one({
        "user_id": user_id,
        "salla_product_id": str(product.get("salla_product_id")),
        "status": {"$in": ["draft", "approved"]},
    }, {"_id": 0}, sort=[("updated_at", -1)])
    add_many((draft or {}).get("images") or [])
    return rows[:10]


def validate_ai_media_request(payload: dict[str, Any], available_urls: set[str]) -> dict[str, Any]:
    operation = _text(payload.get("operation"))
    spec = OPERATION_CATALOG.get(operation)
    if not spec:
        raise ValueError("invalid_ai_media_operation")
    prompt = _text(payload.get("prompt"))
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise ValueError("ai_media_prompt_too_long")
    source_url = _text(payload.get("source_image_url")) or None
    if spec["requires_source"] and not source_url:
        raise ValueError("source_image_required")
    if source_url and source_url not in available_urls:
        raise ValueError("source_image_not_owned_by_product")
    aspect_ratio = _text(payload.get("aspect_ratio")) or "original"
    if aspect_ratio not in {"original", "1:1", "4:5", "9:16", "16:9"}:
        raise ValueError("invalid_aspect_ratio")
    return {
        "operation": operation,
        "prompt": prompt,
        "source_image_url": source_url,
        "aspect_ratio": aspect_ratio,
        "permission": spec["permission"],
        "execution_permission": spec["execution_permission"],
        "risk": spec["risk"],
        "operation_label": spec["label"],
    }


def _extract_temp_token(url: str) -> str | None:
    try:
        path = unquote(urlparse(url).path)
    except Exception:
        return None
    marker = "/media-upload/file/"
    if marker not in path:
        return None
    token = path.rsplit(marker, 1)[-1].strip("/")
    return token or None


def _forbidden_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return True
    return bool(address.is_private or address.is_loopback or address.is_link_local or address.is_multicast or address.is_reserved or address.is_unspecified)


async def _assert_public_hostname(hostname: str, port: int) -> None:
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise AiMediaExecutionError("source_image_host_unreachable", "تعذر الوصول إلى خادم الصورة الأصلية.", status_code=422) from exc
    if not addresses:
        raise AiMediaExecutionError("source_image_host_unreachable", "تعذر الوصول إلى خادم الصورة الأصلية.", status_code=422)
    for address in addresses:
        candidate = str(address[4][0]).split("%", 1)[0]
        if _forbidden_ip(candidate):
            raise AiMediaExecutionError("source_image_private_network_blocked", "رابط الصورة يشير إلى شبكة داخلية غير مسموح بها.", status_code=422)


async def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AiMediaExecutionError("invalid_source_image_url", "رابط الصورة الأصلية غير صالح.", status_code=422)
    if parsed.username or parsed.password:
        raise AiMediaExecutionError("source_image_credentials_blocked", "رابط الصورة لا يجوز أن يحتوي بيانات دخول.", status_code=422)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in {80, 443}:
        raise AiMediaExecutionError("source_image_port_blocked", "منفذ رابط الصورة غير مسموح.", status_code=422)
    await _assert_public_hostname(parsed.hostname, port)


async def _download_public_image(url: str) -> tuple[bytes, str, str]:
    current = url
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=False) as client:
        for _ in range(4):
            await _validate_public_url(current)
            async with client.stream("GET", current, headers={"Accept": "image/jpeg,image/png,image/webp"}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise AiMediaExecutionError("source_image_redirect_invalid", "تعذر متابعة رابط الصورة الأصلية.", status_code=422)
                    current = urljoin(current, location)
                    continue
                if response.status_code >= 400:
                    raise AiMediaExecutionError("source_image_download_failed", "تعذر تنزيل الصورة الأصلية.", status_code=422)
                declared = response.headers.get("content-type", "").split(";", 1)[0].lower()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_SOURCE_IMAGE_BYTES:
                        raise AiMediaExecutionError("source_image_too_large", "حجم الصورة الأصلية أكبر من الحد المسموح.", status_code=413)
            content = bytes(data)
            detected = _detected_type(content)
            if not content or detected not in ALLOWED_TYPES:
                raise AiMediaExecutionError("source_image_invalid", "الصورة الأصلية ليست JPG أو PNG أو WEBP صالحة.", status_code=422)
            if declared and declared not in ALLOWED_TYPES:
                raise AiMediaExecutionError("source_image_content_type_invalid", "نوع محتوى الصورة الأصلية غير مسموح.", status_code=422)
            filename = urlparse(current).path.rsplit("/", 1)[-1] or "source-image"
            return content, detected, filename[:160]
    raise AiMediaExecutionError("source_image_redirect_limit", "تجاوز رابط الصورة عدد التحويلات المسموح.", status_code=422)


async def _source_image_bytes(db: Any, *, user_id: str, salla_product_id: str, url: str) -> tuple[bytes, str, str]:
    token = _extract_temp_token(url)
    if token:
        row = await db[MEDIA_UPLOADS].find_one({
            "token": token,
            "user_id": user_id,
            "salla_product_id": salla_product_id,
            "expires_at": {"$gt": _now_dt()},
        })
        if not row:
            raise AiMediaExecutionError("temporary_source_image_missing", "الصورة المؤقتة مفقودة أو انتهت صلاحيتها.", status_code=410)
        return bytes(row["content"]), str(row.get("content_type") or "image/png"), str(row.get("filename") or "source-image")[:160]
    return await _download_public_image(url)


def _operation_prompt(job: dict[str, Any], product: dict[str, Any]) -> tuple[str, str]:
    instructions = {
        "improve_quality": "Improve sharpness, lighting, white balance, and presentation while keeping the original composition and product unchanged.",
        "remove_background": "Remove the existing background cleanly and return the product on a transparent background with accurate edges and no shadow artifacts.",
        "studio_background": "Place the product in a clean professional ecommerce studio scene. The background may change, but the product itself must remain unchanged.",
        "ad_creative": "Create a polished ecommerce advertising creative centered on this product. Keep the product accurate and leave safe visual space for later ad copy; do not render text unless explicitly requested.",
        "generate_from_prompt": "Create one high-quality ecommerce product image from the supplied brief. Do not include watermarks or unrequested text.",
    }
    operation = str(job.get("operation") or "")
    parts = [instructions.get(operation, "")]
    if operation != "generate_from_prompt":
        parts.append("Preserve the product identity, exact color, material, proportions, logos, embroidery, printed text, and all visible product details. Do not alter the product design. Do not add watermarks, brand marks, or unrelated objects.")
    product_name = _text(product.get("name"))[:200]
    if product_name:
        parts.append(f"Product name for context: {product_name}.")
    if _text(job.get("prompt")):
        parts.append(f"Merchant instructions: {_text(job.get('prompt'))}")
    return " ".join(part for part in parts if part), "transparent" if operation == "remove_background" else "auto"


def _provider_error_payload(response: httpx.Response) -> tuple[str, str]:
    code = "openai_image_request_failed"
    try:
        payload = response.json()
        error = payload.get("error") if isinstance(payload, dict) else None
        candidate = _text((error or {}).get("code") or (error or {}).get("type"))
        if candidate and all(character.isalnum() or character in "._-" for character in candidate):
            code = candidate[:80]
    except Exception:
        pass
    if response.status_code == 429:
        return code, "وصل محرك الصور إلى حد الاستخدام مؤقتًا؛ حاول لاحقًا."
    if response.status_code in {401, 403}:
        return code, "إعداد OpenAI لا يسمح بتنفيذ الصور حاليًا."
    if response.status_code == 400:
        return code, "رفض محرك الصور الطلب؛ راجع الصورة والتعليمات والمقاس."
    return code, "تعذر تنفيذ طلب الصورة عبر OpenAI."


def _extract_b64_image(payload: Any) -> bytes:
    try:
        encoded = payload["data"][0]["b64_json"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AiMediaExecutionError("openai_image_result_missing", "لم يُعد محرك الصور ملف صورة صالحًا.") from exc
    try:
        content = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise AiMediaExecutionError("openai_image_result_invalid", "عاد محرك الصور بنتيجة غير صالحة.") from exc
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise AiMediaExecutionError("openai_image_result_too_large", "حجم الصورة الناتجة أكبر من الحد المسموح للنشر.", status_code=413)
    if _detected_type(content) not in ALLOWED_TYPES:
        raise AiMediaExecutionError("openai_image_result_invalid", "صيغة الصورة الناتجة غير مدعومة.")
    return content


def _extract_response_text(payload: Any) -> str:
    direct = _text(payload.get("output_text")) if isinstance(payload, dict) else ""
    if direct:
        return direct
    if isinstance(payload, dict):
        for item in payload.get("output") or []:
            if isinstance(item, dict):
                for part in item.get("content") or []:
                    if isinstance(part, dict) and part.get("type") == "output_text" and _text(part.get("text")):
                        return _text(part.get("text"))
    return ""


async def _openai_request(*, job: dict[str, Any], product: dict[str, Any], source: tuple[bytes, str, str] | None) -> dict[str, Any]:
    runtime = openai_runtime_status()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = _text(runtime["images"].get("model"))
    if not key or not runtime["images"].get("ready") or not model:
        raise AiMediaExecutionError("ai_image_engine_not_ready", "محرك الصور غير مفعّل في بيئة التشغيل.", status_code=409)
    headers = {"Authorization": f"Bearer {key}"}
    operation = str(job.get("operation") or "")
    async with httpx.AsyncClient(timeout=httpx.Timeout(_bounded_timeout(), connect=20.0)) as client:
        if operation == "suggest_alt":
            if not source:
                raise AiMediaExecutionError("source_image_required", "يلزم تحديد الصورة الأصلية.", status_code=422)
            content, content_type, _ = source
            data_url = f"data:{content_type};base64,{base64.b64encode(content).decode('ascii')}"
            response = await client.post(f"{OPENAI_API_BASE}/responses", headers={**headers, "Content-Type": "application/json"}, json={
                "model": runtime["analysis"]["model"],
                "input": [{"role": "user", "content": [
                    {"type": "input_text", "text": f"اكتب نص ALT عربيًا دقيقًا لهذه الصورة التجارية في سطر واحد لا يتجاوز 140 حرفًا. لا تستخدم هاشتاقات أو مبالغة. اسم المنتج: {_text(product.get('name'))}. تعليمات التاجر: {_text(job.get('prompt'))}"},
                    {"type": "input_image", "image_url": data_url, "detail": "high"},
                ]}],
                "max_output_tokens": 120,
            })
            if response.status_code >= 400:
                code, message = _provider_error_payload(response)
                raise AiMediaExecutionError(code, message, status_code=response.status_code)
            alt = " ".join(_extract_response_text(response.json()).split())[:160]
            if not alt:
                raise AiMediaExecutionError("openai_alt_result_missing", "لم يُعد المحرك وصف ALT صالحًا.")
            return {"kind": "alt", "alt": alt}

        prompt, background = _operation_prompt(job, product)
        fields = {
            "model": model,
            "prompt": prompt,
            "size": _image_size(str(job.get("aspect_ratio") or "original")),
            "quality": _image_quality(),
            "output_format": "webp",
            "output_compression": "85",
            "background": background,
            "n": "1",
        }
        if source:
            content, content_type, filename = source
            response = await client.post(f"{OPENAI_API_BASE}/images/edits", headers=headers, data={**fields, "input_fidelity": "high"}, files={"image": (filename, content, content_type)})
        else:
            response = await client.post(f"{OPENAI_API_BASE}/images/generations", headers={**headers, "Content-Type": "application/json"}, json={**fields, "output_compression": 85, "n": 1})
        if response.status_code >= 400:
            code, message = _provider_error_payload(response)
            raise AiMediaExecutionError(code, message, status_code=response.status_code)
        return {"kind": "image", "content": _extract_b64_image(response.json())}


async def _store_result_image(db: Any, *, user_id: str, salla_product_id: str, job_id: str, content: bytes) -> dict[str, Any]:
    detected = _detected_type(content)
    if detected not in ALLOWED_TYPES:
        raise AiMediaExecutionError("openai_image_result_invalid", "صيغة الصورة الناتجة غير مدعومة.")
    token = secrets.token_urlsafe(32)
    now = _now_dt()
    expires_at = now + timedelta(days=UPLOAD_TTL_DAYS)
    filename = f"mezan-ai-{job_id[:12]}{ALLOWED_TYPES[detected]}"
    await db[MEDIA_UPLOADS].create_index("expires_at", expireAfterSeconds=0)
    await db[MEDIA_UPLOADS].insert_one({
        "token": token,
        "user_id": user_id,
        "salla_product_id": salla_product_id,
        "filename": filename,
        "content_type": detected,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "content": Binary(content),
        "source": "openai_ai_result",
        "ai_job_id": job_id,
        "created_at": now,
        "expires_at": expires_at,
    })
    return {
        "id": None,
        "url": f"/api/products-v2/media-upload/file/{token}",
        "alt": "",
        "is_main": False,
        "source": "temporary_upload",
        "upload_token": token,
        "filename": filename,
        "size": len(content),
        "expires_at": expires_at.isoformat(),
        "ai_generated": True,
        "ai_job_id": job_id,
    }


async def _audit(db: Any, user: dict[str, Any], action: str, target_id: str, after: Any, *, status: str = "completed") -> None:
    await db[AI_ACTION_LOG].insert_one({
        "id": uuid.uuid4().hex,
        "actor_type": "human",
        "actor_id": str(user.get("id") or ""),
        "actor_name": user.get("name"),
        "action": action,
        "target_type": "product_media_ai_job",
        "target_id": target_id,
        "before": None,
        "after": after,
        "status": status,
        "occurred_at": _now(),
    })


def make_product_media_ai_router(db: Any, current_user: Callable, executor: Callable[..., Any] = _openai_request) -> APIRouter:
    router = APIRouter(prefix="/products-v2", tags=["Product V2 AI Media"])

    @router.get("/{product_id}/media-ai")
    async def media_ai_state(product_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        assignment = await _assignment(db, user)
        operations = [{
            "key": key,
            **spec,
            "allowed": _has_permission(user, assignment, spec["permission"]),
            "execution_allowed": _has_permission(user, assignment, spec["execution_permission"]),
        } for key, spec in OPERATION_CATALOG.items()]
        jobs = await db[AI_MEDIA_JOBS].find({"user_id": user_id, "salla_product_id": str(product.get("salla_product_id"))}, {"_id": 0}).sort("created_at", -1).limit(20).to_list(20)
        provider = image_provider_status()
        return {
            "ok": True,
            "mode": provider["mode"],
            "provider": provider,
            "operations": operations,
            "source_images": await _available_source_rows(db, user_id, product),
            "jobs": jobs,
            "publish_from_ai": False,
            "human_approval_required": True,
        }

    @router.post("/{product_id}/media-ai/jobs")
    async def create_media_ai_job(product_id: str, payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        assignment = await _assignment(db, user)
        source_rows = await _available_source_rows(db, user_id, product)
        try:
            normalized = validate_ai_media_request(payload, {row["url"] for row in source_rows})
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
        if not _has_permission(user, assignment, normalized["permission"]):
            raise HTTPException(status_code=403, detail={"code": "ai_media_permission_required", "permission": normalized["permission"]})
        provider = image_provider_status()
        status = "waiting_provider" if not provider["connected"] else "waiting_image_engine" if not provider["ready"] else "ready_for_execution"
        now = _now()
        job = {
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "salla_product_id": str(product.get("salla_product_id")),
            "mezan_product_id": product.get("mezan_product_id") or product.get("id"),
            "product_name": product.get("name"),
            **normalized,
            "status": status,
            "provider_snapshot": provider,
            "execution_mode": provider["mode"],
            "execution_attempted": False,
            "result_image": None,
            "result_alt": None,
            "publish_allowed": False,
            "human_approval_required": True,
            "created_by": user_id,
            "created_by_name": user.get("name"),
            "created_at": now,
            "updated_at": now,
        }
        await db[AI_MEDIA_JOBS].insert_one(dict(job))
        await _audit(db, user, "product_media_ai_job_created", job["id"], job)
        return {"ok": True, "job": job, "provider": provider}

    @router.post("/{product_id}/media-ai/jobs/{job_id}/execute")
    async def execute_media_ai_job(product_id: str, job_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        salla_product_id = str(product.get("salla_product_id"))
        assignment = await _assignment(db, user)
        job = await db[AI_MEDIA_JOBS].find_one({"id": job_id, "user_id": user_id, "salla_product_id": salla_product_id}, {"_id": 0})
        if not job:
            raise HTTPException(status_code=404, detail={"code": "ai_media_job_not_found"})
        execution_permission = _text(job.get("execution_permission") or OPERATION_CATALOG.get(str(job.get("operation")), {}).get("execution_permission"))
        if not execution_permission or not _has_permission(user, assignment, execution_permission):
            raise HTTPException(status_code=403, detail={"code": "ai_media_execution_permission_required", "permission": execution_permission})
        provider = image_provider_status()
        if not provider["execution_available"]:
            raise HTTPException(status_code=409, detail={"code": "ai_image_engine_not_ready", "message": "فعّل سياسة محرك الصور في بيئة التشغيل أولًا.", "provider": provider})
        if job.get("status") not in EXECUTABLE_STATUSES:
            raise HTTPException(status_code=409, detail={"code": "ai_media_job_not_executable", "status": job.get("status")})
        source_url = _text(job.get("source_image_url")) or None
        if source_url and source_url not in {row["url"] for row in await _available_source_rows(db, user_id, product)}:
            raise HTTPException(status_code=409, detail={"code": "source_image_no_longer_available"})
        started_at = _now()
        claim = await db[AI_MEDIA_JOBS].update_one({
            "id": job_id,
            "user_id": user_id,
            "status": {"$in": sorted(EXECUTABLE_STATUSES)},
            "execution_attempted": {"$ne": True},
        }, {"$set": {
            "status": "executing",
            "execution_attempted": True,
            "execution_started_at": started_at,
            "execution_started_by": user_id,
            "provider_snapshot": provider,
            "updated_at": started_at,
        }})
        if int(getattr(claim, "matched_count", 0) or 0) != 1:
            raise HTTPException(status_code=409, detail={"code": "ai_media_job_already_claimed"})
        try:
            source = await _source_image_bytes(db, user_id=user_id, salla_product_id=salla_product_id, url=source_url) if source_url else None
            result = await executor(job=job, product=product, source=source)
            finished_at = _now()
            update: dict[str, Any] = {
                "status": "completed",
                "execution_finished_at": finished_at,
                "updated_at": finished_at,
                "provider_snapshot": image_provider_status(),
                "last_error": None,
            }
            if result.get("kind") == "image":
                update["result_image"] = await _store_result_image(db, user_id=user_id, salla_product_id=salla_product_id, job_id=job_id, content=result["content"])
            elif result.get("kind") == "alt":
                update["result_alt"] = _text(result.get("alt"))[:160]
            else:
                raise AiMediaExecutionError("ai_media_result_invalid", "عاد المحرك بنتيجة غير متوقعة.")
            await db[AI_MEDIA_JOBS].update_one({"id": job_id, "user_id": user_id}, {"$set": update})
            job.update(update)
            await _audit(db, user, "product_media_ai_job_executed", job_id, job)
            return {"ok": True, "job": job, "provider": image_provider_status(), "direct_publish": False, "next_step": "add_result_to_media_draft"}
        except AiMediaExecutionError as exc:
            failed_at = _now()
            failure = {"status": "failed", "last_error": {"code": exc.code, "message": exc.safe_message}, "execution_finished_at": failed_at, "updated_at": failed_at}
            await db[AI_MEDIA_JOBS].update_one({"id": job_id, "user_id": user_id}, {"$set": failure})
            await _audit(db, user, "product_media_ai_job_failed", job_id, failure, status="failed")
            raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.safe_message}) from exc
        except Exception as exc:
            failed_at = _now()
            failure = {"status": "failed", "last_error": {"code": "ai_media_execution_failed", "message": "تعذر تنفيذ الصورة الآن دون إجراء أي نشر."}, "execution_finished_at": failed_at, "updated_at": failed_at}
            await db[AI_MEDIA_JOBS].update_one({"id": job_id, "user_id": user_id}, {"$set": failure})
            await _audit(db, user, "product_media_ai_job_failed", job_id, failure, status="failed")
            raise HTTPException(status_code=502, detail=failure["last_error"]) from exc

    @router.post("/{product_id}/media-ai/jobs/{job_id}/cancel")
    async def cancel_media_ai_job(product_id: str, job_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        job = await db[AI_MEDIA_JOBS].find_one({"id": job_id, "user_id": user_id, "salla_product_id": str(product.get("salla_product_id"))}, {"_id": 0})
        if not job:
            raise HTTPException(status_code=404, detail={"code": "ai_media_job_not_found"})
        if job.get("status") not in PENDING_STATUSES:
            raise HTTPException(status_code=409, detail={"code": "ai_media_job_not_cancellable"})
        now = _now()
        update = {"status": "cancelled", "cancelled_by": user_id, "cancelled_at": now, "updated_at": now}
        await db[AI_MEDIA_JOBS].update_one({"id": job_id}, {"$set": update})
        job.update(update)
        await _audit(db, user, "product_media_ai_job_cancelled", job_id, job)
        return {"ok": True, "job": job}

    return router
