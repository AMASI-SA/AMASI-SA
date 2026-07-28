"""GPT Image 2 compatibility route for governed Product V2 media execution.

GPT Image 2 automatically uses high-fidelity image inputs and rejects the
legacy ``input_fidelity`` field. It also does not currently support transparent
background output. This route shadows the older execute route, keeps the same
governance contract, and allows an explicit retry of a previously failed job.
"""
from __future__ import annotations

import base64
import os
from typing import Any, Callable

import httpx
from fastapi import APIRouter, Depends, HTTPException

from ai_provider_status import openai_runtime_status
from product_media_ai_routes import (
    AI_MEDIA_JOBS,
    OPENAI_API_BASE,
    OPERATION_CATALOG,
    PENDING_STATUSES,
    AiMediaExecutionError,
    _assignment,
    _audit,
    _available_source_rows,
    _bounded_timeout,
    _extract_b64_image,
    _extract_response_text,
    _has_permission,
    _image_quality,
    _image_size,
    _now,
    _product,
    _source_image_bytes,
    _store_result_image,
    _text,
    image_provider_status,
)

RETRYABLE_EXECUTION_STATUSES = frozenset(set(PENDING_STATUSES) | {"failed"})
GPT_IMAGE_2_COMPAT_REVISION = "gpt-image-2-edit-v2"


def _is_gpt_image_2(model: str) -> bool:
    return str(model or "").strip().lower().startswith("gpt-image-2")


def _operation_prompt(job: dict[str, Any], product: dict[str, Any]) -> tuple[str, str]:
    operation = str(job.get("operation") or "")
    instructions = {
        "improve_quality": "Improve sharpness, lighting, white balance, and presentation while keeping the original composition and product unchanged.",
        "remove_background": "Remove the existing background cleanly. Place the product on a pure white opaque ecommerce background (#FFFFFF) with accurate edges and no unrelated objects.",
        "studio_background": "Place the product in a clean professional ecommerce studio scene. The background may change, but the product itself must remain unchanged.",
        "ad_creative": "Create a polished ecommerce advertising creative centered on this product. Keep the product accurate and leave safe visual space for later ad copy; do not render text unless explicitly requested.",
        "generate_from_prompt": "Create one high-quality ecommerce product image from the supplied brief. Do not include watermarks or unrequested text.",
    }
    parts = [instructions.get(operation, "")]
    if operation != "generate_from_prompt":
        parts.append(
            "Preserve the product identity, exact color, material, proportions, logos, embroidery, printed text, and all visible product details. Do not alter the product design. Do not add watermarks, brand marks, or unrelated objects."
        )
    product_name = _text(product.get("name"))[:200]
    if product_name:
        parts.append(f"Product name for context: {product_name}.")
    if _text(job.get("prompt")):
        parts.append(f"Merchant instructions: {_text(job.get('prompt'))}")
    # GPT Image 2 does not currently support transparent output. A clean opaque
    # white background is the safe ecommerce equivalent for background removal.
    return " ".join(part for part in parts if part), "opaque" if operation == "remove_background" else "auto"


def build_gpt_image_request_fields(
    *, job: dict[str, Any], product: dict[str, Any], model: str
) -> tuple[dict[str, Any], bool]:
    """Return provider fields and whether legacy input_fidelity may be sent."""
    prompt, background = _operation_prompt(job, product)
    fields: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "size": _image_size(str(job.get("aspect_ratio") or "original")),
        "quality": _image_quality(),
        "output_format": "webp",
        "output_compression": "85",
        "background": background,
        "n": "1",
    }
    return fields, not _is_gpt_image_2(model)


def _provider_error_payload(response: httpx.Response) -> tuple[str, str]:
    code = "openai_image_request_failed"
    param = ""
    try:
        payload = response.json()
        error = payload.get("error") if isinstance(payload, dict) else None
        candidate = _text((error or {}).get("code") or (error or {}).get("type"))
        param = _text((error or {}).get("param"))
        if candidate and all(character.isalnum() or character in "._-" for character in candidate):
            code = candidate[:80]
    except Exception:
        pass
    if response.status_code == 429:
        return code, "وصل محرك الصور إلى حد الاستخدام مؤقتًا؛ حاول لاحقًا."
    if response.status_code in {401, 403}:
        return code, "إعداد OpenAI لا يسمح بتنفيذ الصور حاليًا."
    if response.status_code == 400:
        if param == "input_fidelity":
            return code, "إعداد دقة الصورة غير متوافق مع GPT Image 2؛ تم إصلاحه ويمكن إعادة المحاولة."
        if param == "background":
            return code, "الخلفية الشفافة غير مدعومة في GPT Image 2؛ تم تحويلها إلى خلفية بيضاء ويمكن إعادة المحاولة."
        return code, "رفض محرك الصور الطلب. تم حفظ رمز الخطأ بأمان ويمكن إعادة المحاولة بعد التحقق من الإعدادات."
    return code, "تعذر تنفيذ طلب الصورة عبر OpenAI."


async def _openai_request_gpt2_compatible(
    *, job: dict[str, Any], product: dict[str, Any], source: tuple[bytes, str, str] | None
) -> dict[str, Any]:
    runtime = openai_runtime_status()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = _text(runtime["images"].get("model"))
    if not key or not runtime["images"].get("ready") or not model:
        raise AiMediaExecutionError(
            "ai_image_engine_not_ready",
            "محرك الصور غير مفعّل في بيئة التشغيل.",
            status_code=409,
        )

    headers = {"Authorization": f"Bearer {key}"}
    operation = str(job.get("operation") or "")
    async with httpx.AsyncClient(timeout=httpx.Timeout(_bounded_timeout(), connect=20.0)) as client:
        if operation == "suggest_alt":
            if not source:
                raise AiMediaExecutionError("source_image_required", "يلزم تحديد الصورة الأصلية.", status_code=422)
            content, content_type, _ = source
            data_url = f"data:{content_type};base64,{base64.b64encode(content).decode('ascii')}"
            response = await client.post(
                f"{OPENAI_API_BASE}/responses",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "model": runtime["analysis"]["model"],
                    "input": [{
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": f"اكتب نص ALT عربيًا دقيقًا لهذه الصورة التجارية في سطر واحد لا يتجاوز 140 حرفًا. لا تستخدم هاشتاقات أو مبالغة. اسم المنتج: {_text(product.get('name'))}. تعليمات التاجر: {_text(job.get('prompt'))}",
                            },
                            {"type": "input_image", "image_url": data_url, "detail": "high"},
                        ],
                    }],
                    "max_output_tokens": 120,
                },
            )
            if response.status_code >= 400:
                code, message = _provider_error_payload(response)
                raise AiMediaExecutionError(code, message, status_code=response.status_code)
            alt = " ".join(_extract_response_text(response.json()).split())[:160]
            if not alt:
                raise AiMediaExecutionError("openai_alt_result_missing", "لم يُعد المحرك وصف ALT صالحًا.")
            return {"kind": "alt", "alt": alt}

        fields, allow_input_fidelity = build_gpt_image_request_fields(
            job=job, product=product, model=model
        )
        if source:
            content, content_type, filename = source
            edit_fields = dict(fields)
            if allow_input_fidelity:
                edit_fields["input_fidelity"] = "high"
            response = await client.post(
                f"{OPENAI_API_BASE}/images/edits",
                headers=headers,
                data=edit_fields,
                files={"image": (filename, content, content_type)},
            )
        else:
            generation_fields = {
                **fields,
                "output_compression": 85,
                "n": 1,
            }
            response = await client.post(
                f"{OPENAI_API_BASE}/images/generations",
                headers={**headers, "Content-Type": "application/json"},
                json=generation_fields,
            )
        if response.status_code >= 400:
            code, message = _provider_error_payload(response)
            raise AiMediaExecutionError(code, message, status_code=response.status_code)
        return {"kind": "image", "content": _extract_b64_image(response.json())}


def make_product_media_ai_gpt2_compat_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/products-v2", tags=["Product V2 AI Media GPT2 Compatibility"])

    @router.post("/{product_id}/media-ai/jobs/{job_id}/execute")
    async def execute_media_ai_job(
        product_id: str,
        job_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        salla_product_id = str(product.get("salla_product_id"))
        assignment = await _assignment(db, user_id)
        job = await db[AI_MEDIA_JOBS].find_one(
            {"id": job_id, "user_id": user_id, "salla_product_id": salla_product_id},
            {"_id": 0},
        )
        if not job:
            raise HTTPException(status_code=404, detail={"code": "ai_media_job_not_found"})
        execution_permission = _text(
            job.get("execution_permission")
            or OPERATION_CATALOG.get(str(job.get("operation")), {}).get("execution_permission")
        )
        if not execution_permission or not _has_permission(user, assignment, execution_permission):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "ai_media_execution_permission_required",
                    "permission": execution_permission,
                },
            )
        provider = image_provider_status()
        if not provider["execution_available"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ai_image_engine_not_ready",
                    "message": "فعّل سياسة محرك الصور في بيئة التشغيل أولًا.",
                    "provider": provider,
                },
            )
        if job.get("status") not in RETRYABLE_EXECUTION_STATUSES:
            raise HTTPException(
                status_code=409,
                detail={"code": "ai_media_job_not_executable", "status": job.get("status")},
            )
        source_url = _text(job.get("source_image_url")) or None
        if source_url and source_url not in {
            row["url"] for row in await _available_source_rows(db, user_id, product)
        }:
            raise HTTPException(status_code=409, detail={"code": "source_image_no_longer_available"})

        started_at = _now()
        claim = await db[AI_MEDIA_JOBS].update_one(
            {
                "id": job_id,
                "user_id": user_id,
                "status": {"$in": sorted(RETRYABLE_EXECUTION_STATUSES)},
            },
            {
                "$set": {
                    "status": "executing",
                    "execution_attempted": True,
                    "execution_started_at": started_at,
                    "execution_started_by": user_id,
                    "provider_snapshot": {
                        **provider,
                        "compatibility_revision": GPT_IMAGE_2_COMPAT_REVISION,
                    },
                    "last_error": None,
                    "updated_at": started_at,
                },
                "$inc": {"execution_attempt_count": 1},
            },
        )
        if int(getattr(claim, "matched_count", 0) or 0) != 1:
            raise HTTPException(status_code=409, detail={"code": "ai_media_job_already_claimed"})

        try:
            source = (
                await _source_image_bytes(
                    db,
                    user_id=user_id,
                    salla_product_id=salla_product_id,
                    url=source_url,
                )
                if source_url
                else None
            )
            result = await _openai_request_gpt2_compatible(
                job=job, product=product, source=source
            )
            finished_at = _now()
            update: dict[str, Any] = {
                "status": "completed",
                "execution_finished_at": finished_at,
                "updated_at": finished_at,
                "provider_snapshot": {
                    **image_provider_status(),
                    "compatibility_revision": GPT_IMAGE_2_COMPAT_REVISION,
                },
                "last_error": None,
            }
            if result.get("kind") == "image":
                update["result_image"] = await _store_result_image(
                    db,
                    user_id=user_id,
                    salla_product_id=salla_product_id,
                    job_id=job_id,
                    content=result["content"],
                )
            elif result.get("kind") == "alt":
                update["result_alt"] = _text(result.get("alt"))[:160]
            else:
                raise AiMediaExecutionError(
                    "ai_media_result_invalid", "عاد المحرك بنتيجة غير متوقعة."
                )
            await db[AI_MEDIA_JOBS].update_one(
                {"id": job_id, "user_id": user_id}, {"$set": update}
            )
            job.update(update)
            await _audit(db, user, "product_media_ai_job_executed", job_id, job)
            return {
                "ok": True,
                "job": job,
                "provider": image_provider_status(),
                "direct_publish": False,
                "next_step": "add_result_to_media_draft",
            }
        except AiMediaExecutionError as exc:
            failed_at = _now()
            failure = {
                "status": "failed",
                "last_error": {
                    "code": exc.code,
                    "message": exc.safe_message,
                    "retry_allowed": True,
                },
                "execution_finished_at": failed_at,
                "updated_at": failed_at,
            }
            await db[AI_MEDIA_JOBS].update_one(
                {"id": job_id, "user_id": user_id}, {"$set": failure}
            )
            await _audit(
                db,
                user,
                "product_media_ai_job_failed",
                job_id,
                failure,
                status="failed",
            )
            raise HTTPException(
                status_code=exc.status_code,
                detail={"code": exc.code, "message": exc.safe_message, "retry_allowed": True},
            ) from exc
        except Exception as exc:
            failed_at = _now()
            failure = {
                "status": "failed",
                "last_error": {
                    "code": "ai_media_execution_failed",
                    "message": "تعذر تنفيذ الصورة الآن دون إجراء أي نشر.",
                    "retry_allowed": True,
                },
                "execution_finished_at": failed_at,
                "updated_at": failed_at,
            }
            await db[AI_MEDIA_JOBS].update_one(
                {"id": job_id, "user_id": user_id}, {"$set": failure}
            )
            await _audit(
                db,
                user,
                "product_media_ai_job_failed",
                job_id,
                failure,
                status="failed",
            )
            raise HTTPException(status_code=502, detail=failure["last_error"]) from exc

    return router
