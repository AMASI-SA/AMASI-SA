"""Short-lived, owner-only control for provisioning one WhatsApp binding.

This route exists only to execute the already-reviewed provisioner against the
database injected into the deployed Mezan process.  It is deliberately hidden,
default-off, same-origin only, and has a non-configurable expiry.  Provider
identifiers are accepted only in bounded POST bodies and are never logged,
returned, stored in raw form, or copied into process environment variables.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from pymongo.errors import DuplicateKeyError

from .whatsapp_provisioning import (
    BINDING_HMAC_ENV,
    ProvisioningError,
    ProvisioningPlan,
    apply_plan,
    build_plan,
)


FEATURE_FLAG_ENV = "MEZAN_TEMP_WHATSAPP_PROVISION_ENABLED"
INTENT_HEADER = "X-Mezan-Provisioning-Intent"
INTENT_VALUE = "receive-only-whatsapp-v1"
CSRF_HEADER = "X-Mezan-Provisioning-CSRF"
CSRF_COOKIE = "__Host-mezan-wa-provision"
CONFIRMATION_LITERAL = "APPLY_RECEIVE_ONLY_WHATSAPP"
COMPLETIONS_COLLECTION = "mezan_whatsapp_provision_completion_v1"
MAX_BODY_BYTES = 4096
CSRF_TTL = timedelta(minutes=10)
PLAN_TTL = timedelta(minutes=5)

# This cannot be extended through configuration.  The route must be removed
# after the one-off production binding has been completed.
HARD_EXPIRES_AT = datetime(2026, 8, 13, 22, 0, tzinfo=timezone.utc)

NO_STORE_HEADERS = {
    "Cache-Control": "no-store, private, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _enabled() -> bool:
    return os.getenv(FEATURE_FLAG_ENV, "false").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _fail(
    code: str,
    *,
    status_code: int,
    message: str,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
        headers=NO_STORE_HEADERS,
    )


def _require_available(now: datetime) -> None:
    if not _enabled() or now >= HARD_EXPIRES_AT:
        raise _fail(
            "temporary_route_unavailable",
            status_code=status.HTTP_404_NOT_FOUND,
            message="مسار الربط المؤقت غير متاح.",
        )


def _trusted_origins() -> frozenset[str]:
    candidates = {"https://mezansalla.com"}
    configured = os.getenv("MEZAN_TEMP_WHATSAPP_PROVISION_ORIGIN", "").strip()
    if configured:
        candidates.add(configured.rstrip("/"))
    return frozenset(
        value
        for value in candidates
        if re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?", value)
    )


def _require_post_transport(request: Request) -> None:
    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    if origin not in _trusted_origins():
        raise _fail(
            "same_origin_required",
            status_code=status.HTTP_403_FORBIDDEN,
            message="يلزم فتح أداة الربط من موقع ميزان نفسه.",
        )
    if request.headers.get("sec-fetch-site", "").strip().casefold() != "same-origin":
        raise _fail(
            "same_origin_required",
            status_code=status.HTTP_403_FORBIDDEN,
            message="يلزم فتح أداة الربط من موقع ميزان نفسه.",
        )
    content_type = request.headers.get("content-type", "").split(";", 1)[0]
    if content_type.strip().casefold() != "application/json":
        raise _fail(
            "json_required",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            message="صيغة الطلب غير مدعومة.",
        )
    if not hmac.compare_digest(
        request.headers.get(INTENT_HEADER, ""),
        INTENT_VALUE,
    ):
        raise _fail(
            "provisioning_intent_required",
            status_code=status.HTTP_403_FORBIDDEN,
            message="تأكيد غرض الربط مفقود.",
        )


async def _bounded_json(request: Request) -> dict[str, Any]:
    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) > MAX_BODY_BYTES:
                raise ValueError
        except ValueError as exc:
            raise _fail(
                "invalid_request_body",
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                message="حجم الطلب غير صالح.",
            ) from exc

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise _fail(
                "invalid_request_body",
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                message="حجم الطلب غير صالح.",
            )
        chunks.append(chunk)
    try:
        value = json.loads(b"".join(chunks))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail(
            "invalid_request_body",
            status_code=status.HTTP_400_BAD_REQUEST,
            message="بيانات الطلب غير صالحة.",
        ) from exc
    if not isinstance(value, dict):
        raise _fail(
            "invalid_request_body",
            status_code=status.HTTP_400_BAD_REQUEST,
            message="بيانات الطلب غير صالحة.",
        )
    return value


async def _owner(current_user: Callable, request: Request) -> dict[str, Any]:
    user = await current_user(request)
    if not isinstance(user, dict) or str(user.get("role") or "").casefold() != "owner":
        raise _fail(
            "owner_only",
            status_code=status.HTTP_403_FORBIDDEN,
            message="هذه الأداة متاحة لمالك المتجر فقط.",
        )
    if not str(user.get("id") or "").strip():
        raise _fail(
            "owner_identity_required",
            status_code=status.HTTP_401_UNAUTHORIZED,
            message="تعذر تحديد هوية مالك المتجر.",
        )
    return user


def _key() -> bytes:
    root = os.getenv(BINDING_HMAC_ENV, "").strip()
    if len(root) < 32:
        raise _fail(
            "provisioning_key_unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message="مفتاح الربط غير متاح في بيئة التشغيل.",
        )
    return hmac.new(
        root.encode("utf-8"),
        b"mezan-temp-whatsapp-provision-route-v1",
        hashlib.sha256,
    ).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _session_binding(request: Request, key: bytes) -> str:
    access_token = request.cookies.get("access_token", "")
    if not access_token:
        raise _fail(
            "browser_session_required",
            status_code=status.HTTP_401_UNAUTHORIZED,
            message="يلزم تسجيل الدخول إلى ميزان من المتصفح.",
        )
    return hmac.new(
        key,
        b"session\x1f" + access_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _signed_token(payload: dict[str, Any], key: bytes) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(key, encoded, hashlib.sha256).digest()
    return f"{_b64(encoded)}.{_b64(signature)}"


def _verified_token(
    token: str,
    *,
    key: bytes,
    purpose: str,
    session_binding: str,
    now: datetime,
) -> dict[str, Any]:
    try:
        if len(token) > 2048 or token.count(".") != 1:
            raise ValueError
        payload_part, signature_part = token.split(".", 1)
        encoded = _unb64(payload_part)
        provided = _unb64(signature_part)
        expected = hmac.new(key, encoded, hashlib.sha256).digest()
        if not hmac.compare_digest(provided, expected):
            raise ValueError
        payload = json.loads(encoded)
        if not isinstance(payload, dict):
            raise ValueError
        expires = int(payload.get("exp"))
        if payload.get("v") != 1 or payload.get("purpose") != purpose:
            raise ValueError
        if not hmac.compare_digest(
            str(payload.get("session") or ""),
            session_binding,
        ):
            raise ValueError
        if expires <= int(now.timestamp()):
            raise ValueError
        return payload
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
        raise _fail(
            "invalid_or_expired_proof",
            status_code=status.HTTP_403_FORBIDDEN,
            message="انتهت صلاحية تأكيد الربط أو أصبح غير صالح.",
        ) from exc


def _csrf_token(session_binding: str, key: bytes, now: datetime) -> str:
    return _signed_token(
        {
            "v": 1,
            "purpose": "csrf",
            "session": session_binding,
            "nonce": secrets.token_urlsafe(24),
            "exp": int((now + CSRF_TTL).timestamp()),
        },
        key,
    )


def _require_csrf(
    request: Request,
    *,
    key: bytes,
    session_binding: str,
    now: datetime,
) -> None:
    header = request.headers.get(CSRF_HEADER, "")
    cookie = request.cookies.get(CSRF_COOKIE, "")
    if not header or not cookie or not hmac.compare_digest(header, cookie):
        raise _fail(
            "invalid_or_expired_csrf",
            status_code=status.HTTP_403_FORBIDDEN,
            message="انتهت جلسة أداة الربط. أعد فتح الصفحة.",
        )
    _verified_token(
        header,
        key=key,
        purpose="csrf",
        session_binding=session_binding,
        now=now,
    )


def _phone(value: Any) -> str:
    if not isinstance(value, str):
        raise _fail(
            "invalid_phone_number_id",
            status_code=status.HTTP_400_BAD_REQUEST,
            message="معرّف رقم Meta غير صالح.",
        )
    normalized = value.strip()
    if not re.fullmatch(r"[0-9]{5,30}", normalized):
        raise _fail(
            "invalid_phone_number_id",
            status_code=status.HTTP_400_BAD_REQUEST,
            message="معرّف رقم Meta غير صالح.",
        )
    return normalized


def _preview_input(body: dict[str, Any]) -> tuple[str, bool]:
    if set(body) != {"phone_number_id", "allow_additional_channel"}:
        raise _fail(
            "invalid_request_body",
            status_code=status.HTTP_400_BAD_REQUEST,
            message="بيانات الطلب غير صالحة.",
        )
    allow = body.get("allow_additional_channel")
    if not isinstance(allow, bool):
        raise _fail(
            "invalid_request_body",
            status_code=status.HTTP_400_BAD_REQUEST,
            message="بيانات الطلب غير صالحة.",
        )
    return _phone(body.get("phone_number_id")), allow


def _apply_input(body: dict[str, Any]) -> tuple[str, bool, str]:
    if set(body) != {
        "phone_number_id",
        "allow_additional_channel",
        "plan_proof",
        "confirmation",
    }:
        raise _fail(
            "invalid_request_body",
            status_code=status.HTTP_400_BAD_REQUEST,
            message="بيانات الطلب غير صالحة.",
        )
    allow = body.get("allow_additional_channel")
    proof = body.get("plan_proof")
    if not isinstance(allow, bool) or not isinstance(proof, str):
        raise _fail(
            "invalid_request_body",
            status_code=status.HTTP_400_BAD_REQUEST,
            message="بيانات الطلب غير صالحة.",
        )
    if not hmac.compare_digest(str(body.get("confirmation") or ""), CONFIRMATION_LITERAL):
        raise _fail(
            "apply_confirmation_required",
            status_code=status.HTTP_409_CONFLICT,
            message="يلزم تأكيد التطبيق النهائي.",
        )
    return _phone(body.get("phone_number_id")), allow, proof


def _plan_commitment(plan: ProvisioningPlan, allow: bool, key: bytes) -> str:
    fields = (
        "schema_version",
        "user_id",
        "merchant_id",
        "channel_id",
        "provider",
        "external_account_key",
        "status",
        "ingress_enabled",
        "egress_mode",
        "send_allowed",
        "ai_auto_reply_allowed",
        "plaintext_credentials_stored",
    )
    canonical = {
        "action": plan.action,
        "document": {name: str(plan.document.get(name)) for name in fields},
        "scope_tokens": list(plan.expected_scope_tokens),
        "allow_additional_channel": allow,
        "requires_additional_channel_gate": plan.requires_additional_channel_gate,
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(key, b"plan\x1f" + encoded, hashlib.sha256).hexdigest()


def _plan_proof(
    plan: ProvisioningPlan,
    *,
    allow: bool,
    key: bytes,
    session_binding: str,
    now: datetime,
) -> str:
    return _signed_token(
        {
            "v": 1,
            "purpose": "plan",
            "session": session_binding,
            "commitment": _plan_commitment(plan, allow, key),
            "nonce": secrets.token_urlsafe(24),
            "exp": int((now + PLAN_TTL).timestamp()),
        },
        key,
    )


def _latch_id(plan: ProvisioningPlan, key: bytes) -> str:
    scope = json.dumps(plan.scope_filter, sort_keys=True, separators=(",", ":"))
    digest = hmac.new(
        key,
        b"completion\x1f" + scope.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"whatsapp-provision:v1:{digest}"


async def _check_completed(db: Any, plan: ProvisioningPlan, key: bytes) -> None:
    row = await getattr(db, COMPLETIONS_COLLECTION).find_one(
        {"_id": _latch_id(plan, key)},
        {"_id": 0, "status": 1, "binding_fingerprint": 1},
    )
    if not row or row.get("status") != "completed":
        return
    if (
        plan.action == "noop"
        and hmac.compare_digest(
            str(row.get("binding_fingerprint") or ""),
            plan.binding_fingerprint,
        )
    ):
        return
    raise _fail(
        "provisioning_already_completed",
        status_code=status.HTTP_409_CONFLICT,
        message="اكتمل ربط واتساب لهذا المتجر من قبل.",
    )


async def _reserve_completion(
    db: Any,
    plan: ProvisioningPlan,
    *,
    key: bytes,
    now: datetime,
) -> str | None:
    collection = getattr(db, COMPLETIONS_COLLECTION)
    latch_id = _latch_id(plan, key)
    current = await collection.find_one({"_id": latch_id})
    if current and current.get("status") == "completed":
        await _check_completed(db, plan, key)
        return None

    lease_owner = secrets.token_hex(24)
    # Fence an uncertain apply beyond this route's hard sunset.  A process can
    # fail after Mongo inserted the channel but before the completion marker;
    # in that case a short retry lease could permit a second provider binding.
    # Known ProvisioningError paths explicitly remove this reservation below.
    expires = HARD_EXPIRES_AT + timedelta(days=1)
    if current:
        stored_expiry = current.get("lease_expires_at")
        current_expiry = stored_expiry
        if isinstance(current_expiry, datetime) and current_expiry.tzinfo is None:
            current_expiry = current_expiry.replace(tzinfo=timezone.utc)
        if not isinstance(current_expiry, datetime) or current_expiry > now:
            raise _fail(
                "provisioning_in_progress",
                status_code=status.HTTP_409_CONFLICT,
                message="توجد عملية ربط أخرى قيد التنفيذ.",
            )
        result = await collection.update_one(
            {
                "_id": latch_id,
                "status": "pending",
                "lease_expires_at": stored_expiry,
            },
            {
                "$set": {
                    "lease_owner": lease_owner,
                    "lease_expires_at": expires,
                    "binding_fingerprint": plan.binding_fingerprint,
                    "updated_at": now,
                }
            },
        )
        if result.modified_count != 1:
            raise _fail(
                "provisioning_in_progress",
                status_code=status.HTTP_409_CONFLICT,
                message="توجد عملية ربط أخرى قيد التنفيذ.",
            )
        return lease_owner

    try:
        await collection.insert_one(
            {
                "_id": latch_id,
                "schema_version": 1,
                "status": "pending",
                "lease_owner": lease_owner,
                "lease_expires_at": expires,
                "binding_fingerprint": plan.binding_fingerprint,
                "created_at": now,
                "updated_at": now,
            }
        )
    except DuplicateKeyError as exc:
        raise _fail(
            "provisioning_in_progress",
            status_code=status.HTTP_409_CONFLICT,
            message="توجد عملية ربط أخرى قيد التنفيذ.",
        ) from exc
    return lease_owner


async def _finish_completion(
    db: Any,
    plan: ProvisioningPlan,
    *,
    key: bytes,
    lease_owner: str,
    now: datetime,
) -> None:
    result = await getattr(db, COMPLETIONS_COLLECTION).update_one(
        {
            "_id": _latch_id(plan, key),
            "status": "pending",
            "lease_owner": lease_owner,
        },
        {
            "$set": {
                "status": "completed",
                "completed_at": now,
                "updated_at": now,
            },
            "$unset": {"lease_owner": "", "lease_expires_at": ""},
        },
    )
    if result.modified_count != 1:
        raise _fail(
            "completion_record_failed",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="تم الربط لكن تعذر إغلاق أداة التهيئة؛ لا تحاول رقمًا آخر.",
        )


async def _release_completion(
    db: Any,
    plan: ProvisioningPlan,
    *,
    key: bytes,
    lease_owner: str,
) -> None:
    await getattr(db, COMPLETIONS_COLLECTION).delete_one(
        {
            "_id": _latch_id(plan, key),
            "status": "pending",
            "lease_owner": lease_owner,
        }
    )


def _provisioning_blocked() -> HTTPException:
    return _fail(
        "provisioning_blocked",
        status_code=status.HTTP_409_CONFLICT,
        message="تعذر إنشاء خطة الربط الآمنة لحالة المتجر الحالية.",
    )


def _json(payload: dict[str, Any], *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code, headers=NO_STORE_HEADERS)


def _control_html(csrf: str, nonce: str) -> str:
    csrf_json = json.dumps(csrf)
    confirmation_json = json.dumps(CONFIRMATION_LITERAL)
    return f"""<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ربط واتساب المؤقت — ميزان</title>
<style>
body{{font-family:system-ui;background:#f5f7f6;color:#102820;margin:0;padding:24px}}
main{{max-width:720px;margin:auto;background:white;border:1px solid #d7e2dd;border-radius:18px;padding:24px}}
h1{{margin-top:0}}label{{display:block;font-weight:700;margin:16px 0 7px}}
input[type=password]{{box-sizing:border-box;width:100%;padding:12px;border:1px solid #a8bcb3;border-radius:10px}}
.check{{display:flex;gap:9px;align-items:center;font-weight:600;margin:16px 0}}
button{{padding:11px 18px;border:0;border-radius:10px;background:#075f46;color:white;font-weight:800;margin-left:8px}}
button[disabled]{{opacity:.45}}pre{{white-space:pre-wrap;background:#eff6f3;padding:14px;border-radius:10px;min-height:70px}}
.warn{{background:#fff7e6;border:1px solid #f0cc7b;padding:12px;border-radius:10px}}
</style></head><body><main>
<h1>ربط واتساب الوارد — قراءة فقط</h1>
<p class="warn">أداة مؤقتة. لا تفعّل الإرسال أو الرد التلقائي. أدخل معرّف Phone Number ID وليس رقم الهاتف.</p>
<label for="phone">Meta Phone Number ID</label>
<input id="phone" type="password" inputmode="numeric" autocomplete="off" maxlength="30">
<label class="check"><input id="additional" type="checkbox">الحفاظ على القناة الآمنة الحالية وإضافة الربط الحقيقي بجانبها</label>
<button id="preview">معاينة بلا كتابة</button>
<label class="check"><input id="confirm" type="checkbox">أؤكد تطبيق ربط استقبال واتساب للقراءة فقط</label>
<button id="apply" disabled>تطبيق الربط</button>
<pre id="output" aria-live="polite">لم تُنفذ أي كتابة.</pre>
<script nonce="{nonce}">
const csrf={csrf_json}, confirmation={confirmation_json};
const phone=document.getElementById('phone'), additional=document.getElementById('additional');
const preview=document.getElementById('preview'), apply=document.getElementById('apply');
const confirmBox=document.getElementById('confirm'), output=document.getElementById('output');
let proof='', plannedAdditional=false;
async function post(path,body){{
 const response=await fetch(path,{{method:'POST',credentials:'same-origin',headers:{{
  'Content-Type':'application/json','{INTENT_HEADER}':'{INTENT_VALUE}','{CSRF_HEADER}':csrf
 }},body:JSON.stringify(body)}});
 const data=await response.json().catch(()=>({{detail:{{message:'تعذر قراءة الاستجابة.'}}}}));
 if(!response.ok) throw new Error(data?.detail?.message||'فشل الطلب.');
 return data;
}}
preview.onclick=async()=>{{
 let raw=phone.value.trim(); phone.value=''; proof=''; confirmBox.checked=false; apply.disabled=true;
 plannedAdditional=additional.checked; output.textContent='جارٍ تنفيذ معاينة بلا كتابة…';
 try{{const data=await post('/api/customer-intelligence/v1/owner/whatsapp-provisioning/preview',{{phone_number_id:raw,allow_additional_channel:plannedAdditional}});
  raw=''; proof=data.plan_proof; output.textContent=JSON.stringify(data.plan,null,2)+'\\n\\nأعد إدخال Phone Number ID نفسه قبل التطبيق.';
  apply.disabled=!confirmBox.checked;
 }}catch(error){{raw='';output.textContent=error.message;}}
}};
additional.onchange=()=>{{proof='';confirmBox.checked=false;apply.disabled=true;
 output.textContent='تغير خيار القناة؛ نفّذ المعاينة من جديد.';}};
confirmBox.onchange=()=>{{apply.disabled=!(confirmBox.checked&&proof);}};
apply.onclick=async()=>{{
 let raw=phone.value.trim(); phone.value=''; output.textContent='جارٍ تطبيق الربط الآمن…'; apply.disabled=true;
 try{{const data=await post('/api/customer-intelligence/v1/owner/whatsapp-provisioning/apply',{{phone_number_id:raw,allow_additional_channel:plannedAdditional,plan_proof:proof,confirmation}});
  raw=''; proof=''; output.textContent=JSON.stringify(data.result,null,2);
 }}catch(error){{raw='';output.textContent=error.message;apply.disabled=!(confirmBox.checked&&proof);}}
}};
</script></main></body></html>"""


def make_temporary_whatsapp_provisioning_router(
    db: Any,
    current_user: Callable,
    *,
    clock: Callable[[], datetime] | None = None,
) -> APIRouter:
    """Build the removable route against the deployed application's database."""

    router = APIRouter(
        prefix="/customer-intelligence/v1/owner/whatsapp-provisioning",
        tags=["temporary-whatsapp-provisioning"],
    )
    now_fn = clock or _now

    @router.get("", include_in_schema=False, response_class=HTMLResponse)
    async def control(request: Request) -> HTMLResponse:
        now = now_fn()
        _require_available(now)
        await _owner(current_user, request)
        key = _key()
        session = _session_binding(request, key)
        csrf = _csrf_token(session, key, now)
        nonce = secrets.token_urlsafe(18)
        response = HTMLResponse(
            _control_html(csrf, nonce),
            headers={
                **NO_STORE_HEADERS,
                "Content-Security-Policy": (
                    "default-src 'none'; connect-src 'self'; style-src 'unsafe-inline'; "
                    f"script-src 'nonce-{nonce}'; base-uri 'none'; form-action 'none'; "
                    "frame-ancestors 'none'"
                ),
            },
        )
        response.set_cookie(
            CSRF_COOKIE,
            csrf,
            secure=True,
            httponly=True,
            samesite="strict",
            path="/",
            max_age=int(CSRF_TTL.total_seconds()),
        )
        return response

    async def _post_context(request: Request) -> tuple[datetime, dict, bytes, str]:
        now = now_fn()
        _require_available(now)
        _require_post_transport(request)
        owner = await _owner(current_user, request)
        key = _key()
        session = _session_binding(request, key)
        _require_csrf(
            request,
            key=key,
            session_binding=session,
            now=now,
        )
        return now, owner, key, session

    @router.post("/preview", include_in_schema=False)
    async def preview(request: Request) -> Response:
        now, owner, key, session = await _post_context(request)
        body = await _bounded_json(request)
        phone_number_id, allow = _preview_input(body)
        try:
            plan = await build_plan(
                db,
                phone_number_id=phone_number_id,
                owner_id=str(owner["id"]).strip(),
                allow_additional_channel=allow,
                now=now,
            )
        except ProvisioningError as exc:
            raise _provisioning_blocked() from exc
        await _check_completed(db, plan, key)
        return _json(
            {
                "ok": True,
                "plan": plan.public(applied=False),
                "plan_proof": _plan_proof(
                    plan,
                    allow=allow,
                    key=key,
                    session_binding=session,
                    now=now,
                ),
            }
        )

    @router.post("/apply", include_in_schema=False)
    async def apply(request: Request) -> Response:
        now, owner, key, session = await _post_context(request)
        body = await _bounded_json(request)
        phone_number_id, allow, proof = _apply_input(body)
        receipt = _verified_token(
            proof,
            key=key,
            purpose="plan",
            session_binding=session,
            now=now,
        )
        try:
            plan = await build_plan(
                db,
                phone_number_id=phone_number_id,
                owner_id=str(owner["id"]).strip(),
                allow_additional_channel=allow,
                now=now,
            )
        except ProvisioningError as exc:
            raise _provisioning_blocked() from exc
        expected = _plan_commitment(plan, allow, key)
        if not hmac.compare_digest(str(receipt.get("commitment") or ""), expected):
            raise _fail(
                "stale_or_mismatched_plan",
                status_code=status.HTTP_409_CONFLICT,
                message="تغيرت حالة الربط بعد المعاينة؛ أعد المعاينة.",
            )

        lease_owner = await _reserve_completion(db, plan, key=key, now=now)
        if lease_owner is None:
            try:
                result = await apply_plan(
                    db,
                    plan,
                    allow_additional_channel=allow,
                    now=now,
                )
            except ProvisioningError as exc:
                raise _provisioning_blocked() from exc
            return _json({"ok": True, "result": result})

        try:
            result = await apply_plan(
                db,
                plan,
                allow_additional_channel=allow,
                now=now,
            )
        except ProvisioningError as exc:
            await _release_completion(
                db,
                plan,
                key=key,
                lease_owner=lease_owner,
            )
            raise _provisioning_blocked() from exc

        # The binding may already exist now.  Never delete the pending fence if
        # finalizing the marker fails; that could permit a different binding.
        await _finish_completion(
            db,
            plan,
            key=key,
            lease_owner=lease_owner,
            now=now,
        )
        return _json({"ok": True, "result": result})

    return router


__all__ = [
    "CONFIRMATION_LITERAL",
    "FEATURE_FLAG_ENV",
    "HARD_EXPIRES_AT",
    "make_temporary_whatsapp_provisioning_router",
]
