"""Governed AI pilot for Google Product Category classification in Mezan OS.

The pilot is proposal-first. OpenAI receives only bounded product-catalog facts
(no customer PII, credentials, costs, orders, or raw arbitrary payloads). Model
output is validated against Google's official taxonomy before it is persisted.

No Salla write is ever performed here. High-confidence proposals can only be
applied to Mezan after an explicit human batch approval, with source-revision
checks, verify-after-write, and an audit row for every applied product.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, RateLimitError
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from ai_provider_status import openai_runtime_status
from product_category_variant_support import _get_google_taxonomy
from product_v2_routes import PRODUCTS

CLASSIFICATIONS = "mezan_product_google_taxonomy_classifications_v2"
RUNS = "mezan_product_google_taxonomy_runs_v2"
AI_ACTION_LOG = "mezan_ai_action_log_v2"

DEFAULT_PILOT_LIMIT = 20
MIN_PILOT_LIMIT = 20
MAX_PILOT_LIMIT = 200
RUN_LEASE_SECONDS = 180
RESUME_SCAN_SECONDS = 30
ACTIVE_RUN_STATUSES = ("queued", "running")
MAX_PRODUCTS_SCANNED = 5000
MAX_CANDIDATES = 24
SEARCH_TERMS_CHUNK_SIZE = 20
CLASSIFICATION_CHUNK_SIZE = 5
# Keep provider calls sequential. The previous three-way burst caused a 200-item
# run to persist 170 transient 429 failures before the account window recovered.
CLASSIFICATION_CONCURRENCY = 1
CLASSIFICATION_WAVE_SIZE = 15
OPENAI_RETRY_ATTEMPTS = 6
OPENAI_RETRY_BASE_SECONDS = 3.0
OPENAI_RETRY_MAX_SECONDS = 45.0
OPENAI_REQUEST_SPACING_SECONDS = 1.0
MAX_DESCRIPTION_CHARS = 900
MAX_REASON_CHARS = 500
MAX_EVIDENCE_ITEMS = 5
CANDIDATE_RETRIEVER_VERSION = 3
MAX_CLASSIFICATIONS_SCANNED = 10000
RETRYABLE_UNCERTAIN_STATUSES = (
    "low_confidence",
    "review_required",
    "review_required_existing_category",
)

# The Products V2 documents can contain large raw Salla payloads. Loading 5,000
# complete documents can exceed the production container's memory before the
# heartbeat gets a chance to run, so the pilot reads only classification facts.
PILOT_PRODUCT_PROJECTION: dict[str, Any] = {
    "_id": 0,
    "mezan_product_id": 1,
    "id": 1,
    "salla_product_id": 1,
    "name": 1,
    "description_html": 1,
    "description": 1,
    "short_description": 1,
    "categories": 1,
    "options": 1,
    "product_type": 1,
    "brand": 1,
    "sku": 1,
    "google_category": 1,
    "google_category_id": 1,
    "main_image": 1,
    "images": {"$slice": 1},
    "details_loaded": 1,
    "updated_at": 1,
    "raw_salla.name": 1,
    "raw_salla.description": 1,
    "raw_salla.subtitle": 1,
    "raw_salla.short_description": 1,
    "raw_salla.type": 1,
    "raw_salla.brand": 1,
    "raw_salla.sku": 1,
    "raw_salla.gtin": 1,
    "raw_salla.mpn": 1,
    "raw_salla.google_taxonomy": 1,
    "raw_salla.google_product_category": 1,
    "raw_salla_details.name": 1,
    "raw_salla_details.description": 1,
    "raw_salla_details.subtitle": 1,
    "raw_salla_details.short_description": 1,
    "raw_salla_details.type": 1,
    "raw_salla_details.brand": 1,
    "raw_salla_details.sku": 1,
    "raw_salla_details.gtin": 1,
    "raw_salla_details.mpn": 1,
    "raw_salla_details.google_taxonomy": 1,
    "raw_salla_details.google_product_category": 1,
}
PILOT_SELECTION_PROJECTION: dict[str, Any] = {
    "_id": 0,
    "mezan_product_id": 1,
    "id": 1,
    "name": 1,
    "categories": {"$slice": 1},
    "product_type": 1,
    "google_category": 1,
    "google_category_id": 1,
    "details_loaded": 1,
    "updated_at": 1,
}

APPLY_CONFIRMATION = "اعتماد تصنيفات Google عالية الثقة في ميزان"
CREDIT_EXHAUSTED_ERROR_CODES = {
    "credit_balance_exhausted",
    "insufficient_quota",
    "billing_hard_limit_reached",
    "billing_not_active",
}
CREDIT_EXHAUSTED_MESSAGE = (
    "توقف التصنيف لأن رصيد OpenAI API غير كافٍ. "
    "بعد إضافة الرصيد شغّل الدفعة التالية لاستكمال المنتجات المتبقية فقط."
)


class PilotStartIn(BaseModel):
    limit: int = Field(default=DEFAULT_PILOT_LIMIT, ge=MIN_PILOT_LIMIT, le=MAX_PILOT_LIMIT)
    selection_mode: Literal["sample", "next_unseen", "retry_review"] = "sample"


class PilotApplyIn(BaseModel):
    confirmation: str = Field(default="", max_length=120)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _serialize(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return row
    result = dict(row)
    result.pop("_id", None)
    for key, value in list(result.items()):
        if hasattr(value, "isoformat"):
            result[key] = value.isoformat()
    return result


def _selected_lookup_values(selected_ids: list[str]) -> list[Any]:
    """Match both string ids and legacy numeric Mongo ids without broad scans."""
    values: list[Any] = list(selected_ids)
    values.extend(int(value) for value in selected_ids if value.isdigit())
    return values


def _successfully_seen_filter(user_id: str) -> dict[str, Any]:
    """Keep old AI failures eligible for a later recovery batch."""
    return {
        "user_id": user_id,
        "decision_status": {"$ne": "ai_failed"},
    }


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("name", "title", "label", "value", "text"):
            if value.get(key) not in (None, "", [], {}):
                return _text(value.get(key))
        return ""
    return " ".join(str(value).strip().split())


def _strip_html(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())[:MAX_DESCRIPTION_CHARS]


def _normalize_ar(value: Any) -> str:
    text = _text(value).casefold()
    text = re.sub(r"[\u064b-\u065f\u0670]", "", text)
    text = text.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"}))
    text = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", text)
    return " ".join(text.split())


_STOPWORDS = {
    "من", "في", "على", "الى", "إلى", "مع", "او", "أو", "و", "عن", "هذا", "هذه",
    "لون", "باللون", "مقاس", "مقاسات", "منتج", "طقم", "جديد", "فاخر", "فاخرة",
}


def _tokens(value: Any) -> set[str]:
    return {
        token for token in _normalize_ar(value).split()
        if len(token) >= 2 and token not in _STOPWORDS
    }


_ALIAS_TERMS = {
    "عبايه": ["ملابس تقليديه", "ملابس الاحتفالات"],
    "عبايات": ["ملابس تقليديه", "ملابس الاحتفالات"],
    "فروه": ["المعاطف والسترات"],
    "معطف": ["المعاطف والسترات"],
    "جاكيت": ["المعاطف والسترات"],
    "جاكت": ["المعاطف والسترات"],
    "بشت": ["ملابس الاحتفالات والملابس التقليديه"],
    "دقله": ["ملابس الاحتفالات والملابس التقليديه"],
    "ثوب": ["ملابس الاحتفالات والملابس التقليديه"],
    "سديري": ["ملابس الاحتفالات والملابس التقليديه"],
    "سلسال": ["قلادات"],
    "سلاسل": ["قلادات"],
    "قلاده": ["قلادات"],
    "قلادات": ["قلادات"],
    "تعليقه": ["قلادات ودلايات", "دلايات"],
    "دلايه": ["قلادات ودلايات", "دلايات"],
    "خاتم": ["خواتم"],
    "خواتم": ["خواتم"],
    "اسواره": ["اساور"],
    "اساور": ["اساور"],
    "حلق": ["اقراط"],
    "اقراط": ["اقراط"],
    "بروش": ["دبابيس زينه", "حلي"],
    "طوق": ["اكسسوارات الشعر", "اطقم مجوهرات"],
    "فواحه": ["معطرات هواء للمركبات"],
    "مريول": ["ملابس", "فساتين"],
    "شنطه": ["حقائب ظهر", "حقائب"],
    "مدرسيه": ["حقائب مدرسيه", "حقائب ظهر"],
    "ساعه": ["ساعات يد"],
    "كاسيو": ["ساعات يد"],
    "سواتش": ["ساعات يد"],
    "قلم": ["اقلام حبر", "اقلام"],
    "كبك": ["ازرار الاكمام", "ازرار اكمام"],
    "شال": ["الاوشحه والشالات", "اوشحه وشالات"],
    "وشاح": ["الاوشحه والشالات", "اوشحه وشالات"],
    "تيشيرت": ["قمصان وبلوزات", "تي شيرت"],
    "تيشبرت": ["قمصان وبلوزات", "تي شيرت"],
    "تیشیرت": ["قمصان وبلوزات", "تي شيرت"],
    "هودي": ["قمصان وبلوزات", "سويت شيرت", "بلوفرات"],
    "سويتر": ["قمصان وبلوزات", "بلوفرات", "سويت شيرت"],
    "بلوفر": ["قمصان وبلوزات", "بلوفرات", "سويت شيرت"],
    "قميص": ["قمصان وبلوزات", "قمصان"],
    "كوب": ["الاكواب", "اكواب"],
    "دمية": ["دمى", "العاب محشوه"],
    "لابوبو": ["حيوانات محشوه"],
    "مجسم": ["دمي وشخصيات ومجموعات لعب", "مجسمات", "دمى"],
    "بجامه": ["البيجامات", "ملابس نوم"],
    "بيجامه": ["البيجامات", "ملابس نوم"],
    "افرول": ["ملابس الرضع والاطفال الصغار", "ملابس مواليد", "افرولات"],
}

_PHRASE_ALIAS_TERMS = {
    "طقم بناتي بالاسم": ["اطقم مجوهرات"],
    "طوق الورد وسلسال": ["اطقم مجوهرات", "اكسسوارات الشعر", "قلادات"],
    "طقم اطفال رمضاني": ["اطقم ملابس"],
    "وشاح تخرج": ["الاوشحه والشالات"],
    "تعليقه سياره": ["ديكور المركبات"],
    "افرول رمضان مواليد": ["ملابس الرضع والاطفال الصغار"],
    "كوب شعار": ["الاكواب"],
    "مجسم لابوبو": ["حيوانات محشوه", "دمي وشخصيات ومجموعات لعب"],
}

def _product_raw(product: dict[str, Any]) -> dict[str, Any]:
    for key in ("raw_salla_details", "raw_salla"):
        value = product.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _category_labels(product: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for row in product.get("categories") or []:
        label = _text(row.get("path") or row.get("name") if isinstance(row, dict) else row)
        if label and label not in result:
            result.append(label)
    return result[:8]


def _option_labels(product: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for option in product.get("options") or []:
        if not isinstance(option, dict):
            continue
        name = _text(option.get("name"))
        values = [
            _text(value.get("name") if isinstance(value, dict) else value)
            for value in (option.get("values") or [])[:8]
        ]
        values = [value for value in values if value]
        label = f"{name}: {', '.join(values)}" if name and values else name
        if label:
            result.append(label[:220])
    return result[:8]


def _brand(product: dict[str, Any], raw: dict[str, Any]) -> str:
    return _text(product.get("brand") or raw.get("brand"))


def _product_evidence(product: dict[str, Any]) -> dict[str, Any]:
    raw = _product_raw(product)
    description = _strip_html(
        product.get("description_html")
        or product.get("description")
        or raw.get("description")
    )
    short_description = _strip_html(
        product.get("short_description") or raw.get("subtitle") or raw.get("short_description")
    )[:400]
    categories = _category_labels(product)
    options = _option_labels(product)
    return {
        "product_id": str(product.get("mezan_product_id") or product.get("id") or ""),
        "salla_product_id": str(product.get("salla_product_id") or ""),
        "name": _text(product.get("name") or raw.get("name"))[:300],
        "description": description,
        "short_description": short_description,
        "salla_categories": categories,
        "options": options,
        "product_type": _text(product.get("product_type") or raw.get("type"))[:80],
        "brand": _brand(product, raw)[:160],
        "sku": _text(product.get("sku") or raw.get("sku"))[:100],
        "gtin": _text(raw.get("gtin"))[:100],
        "mpn": _text(raw.get("mpn"))[:100],
        "current_google_category": _text(
            product.get("google_category")
            or product.get("google_category_id")
            or raw.get("google_taxonomy")
            or raw.get("google_product_category")
        )[:300],
        "has_image": bool(product.get("main_image") or product.get("images")),
    }


def _input_revision(evidence: dict[str, Any]) -> str:
    relevant = {
        key: evidence.get(key)
        for key in (
            "name", "description", "short_description", "salla_categories", "options",
            "product_type", "brand", "sku", "gtin", "mpn", "current_google_category",
        )
    }
    payload = json.dumps(relevant, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _evidence_limited(evidence: dict[str, Any]) -> bool:
    supporting = bool(
        evidence.get("description")
        or evidence.get("short_description")
        or evidence.get("salla_categories")
        or evidence.get("options")
    )
    return bool(evidence.get("name")) and not supporting


def _fallback_search_terms(evidence: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    primary_values = [evidence.get("name"), *(evidence.get("salla_categories") or [])]
    primary_text = " ".join(_normalize_ar(value) for value in primary_values if value)
    for phrase, aliases in _PHRASE_ALIAS_TERMS.items():
        if phrase in primary_text:
            terms.extend(aliases)
    for value in primary_values:
        normalized = _normalize_ar(value)
        if normalized:
            terms.append(normalized[:160])
        for token in normalized.split():
            for alias in _ALIAS_TERMS.get(token, []):
                terms.append(alias)
    # Descriptions rescue placeholder names such as ".", "0", and "D1".
    # Only known aliases are emitted from free text so long marketing copy
    # cannot swamp the official-taxonomy candidate set.
    for value in [evidence.get("short_description"), evidence.get("description")]:
        normalized = _normalize_ar(value)
        for token in normalized.split():
            for alias in _ALIAS_TERMS.get(token, []):
                terms.append(alias)
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        key = _normalize_ar(term)
        if key and key not in seen:
            seen.add(key)
            result.append(term)
    return result[:24]


def _taxonomy_maps(items: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    by_id = {str(row.get("id")): row for row in items if row.get("id") is not None}
    by_path = {_normalize_ar(row.get("path")): str(row.get("id")) for row in items if row.get("path")}
    return by_id, by_path


def _resolve_current_id(current: str, by_id: dict[str, Any], by_path: dict[str, str]) -> str | None:
    text = _text(current)
    if not text:
        return None
    if text in by_id:
        return text
    return by_path.get(_normalize_ar(text))


def _candidate_rows(
    evidence: dict[str, Any],
    ai_terms: list[str],
    taxonomy: list[dict[str, Any]],
    current_id: str | None,
) -> list[dict[str, Any]]:
    terms = [*ai_terms, *_fallback_search_terms(evidence)]
    term_rows = [(_normalize_ar(term), _tokens(term)) for term in terms if _normalize_ar(term)]
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in taxonomy:
        path = _normalize_ar(row.get("path"))
        name = _normalize_ar(row.get("name"))
        path_tokens = _tokens(path)
        name_tokens = _tokens(name)
        score = 0.0
        for phrase, phrase_tokens in term_rows:
            if not phrase:
                continue
            # An exact taxonomy label is stronger evidence than merely finding
            # the same ancestor phrase somewhere in a deep descendant path.
            # Without this, generic Arabic terms such as "ملابس تقليدية" can
            # push unrelated descendants (for example baptism clothing) above
            # the correct parent category solely because they are deeper.
            if phrase == name:
                score += 36.0
            elif phrase in name:
                score += 20.0
            if phrase in path:
                score += 12.0 + min(6.0, len(phrase_tokens) * 1.5)
            overlap = len(phrase_tokens.intersection(path_tokens))
            if overlap:
                score += overlap * 3.0
                if phrase_tokens and overlap == len(phrase_tokens):
                    score += 4.0
            name_overlap = len(phrase_tokens.intersection(name_tokens))
            if name_overlap:
                score += name_overlap * 4.0
        if score > 0:
            # Prefer precision only when the category label itself has lexical
            # support; do not reward arbitrary descendants of a matched parent.
            if name_tokens.intersection(set().union(*(tokens for _, tokens in term_rows))):
                score += min(float(row.get("depth") or 0), 6.0) * 0.15
            scored.append((score, row))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("path") or "")))

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    if current_id:
        current = next((row for row in taxonomy if str(row.get("id")) == current_id), None)
        if current:
            result.append(current)
            seen.add(current_id)
    for _, row in scored:
        category_id = str(row.get("id") or "")
        if not category_id or category_id in seen:
            continue
        seen.add(category_id)
        result.append(row)
        if len(result) >= MAX_CANDIDATES:
            break
    return result


def _diversity_key(product: dict[str, Any]) -> str:
    categories = _category_labels(product)
    if categories:
        return _normalize_ar(categories[0]) or "category"
    product_type = _normalize_ar(product.get("product_type"))
    if product_type:
        return f"type:{product_type}"
    name_tokens = sorted(_tokens(product.get("name")))
    return f"name:{name_tokens[0] if name_tokens else 'unknown'}"


def _round_robin(pool: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if count <= 0:
        return []
    groups: dict[str, list[dict[str, Any]]] = {}
    for product in pool:
        groups.setdefault(_diversity_key(product), []).append(product)
    keys = sorted(groups)
    result: list[dict[str, Any]] = []
    while keys and len(result) < count:
        next_keys: list[str] = []
        for key in keys:
            rows = groups[key]
            if rows and len(result) < count:
                result.append(rows.pop(0))
            if rows:
                next_keys.append(key)
        keys = next_keys
    return result


def _select_pilot_products(products: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    missing = [row for row in products if not _text(row.get("google_category") or row.get("google_category_id"))]
    existing = [row for row in products if _text(row.get("google_category") or row.get("google_category_id"))]
    existing_target = min(len(existing), max(1, limit // 5))
    selected = _round_robin(missing, max(0, limit - existing_target))
    selected_ids = {str(row.get("mezan_product_id") or row.get("id")) for row in selected}
    for row in _round_robin(existing, existing_target):
        key = str(row.get("mezan_product_id") or row.get("id"))
        if key not in selected_ids:
            selected.append(row)
            selected_ids.add(key)
    if len(selected) < limit:
        remaining = [
            row for row in products
            if str(row.get("mezan_product_id") or row.get("id")) not in selected_ids
        ]
        selected.extend(_round_robin(remaining, limit - len(selected)))
    return selected[:limit]


def _select_unseen_products(
    products: list[dict[str, Any]],
    seen_product_ids: set[str],
    limit: int,
) -> list[dict[str, Any]]:
    unseen = [
        row for row in products
        if str(row.get("mezan_product_id") or row.get("id") or "") not in seen_product_ids
    ]
    return _round_robin(unseen, limit)


def _select_retryable_uncertain_products(
    products: list[dict[str, Any]],
    newest_classifications: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Retry the newest uncertain result once after a retriever upgrade."""
    def retriever_version(row: dict[str, Any]) -> int:
        try:
            return int(row.get("candidate_retriever_version") or 1)
        except (TypeError, ValueError):
            return 1

    latest_by_product: dict[str, dict[str, Any]] = {}
    for row in newest_classifications:
        product_id = str(row.get("mezan_product_id") or "")
        if product_id and product_id not in latest_by_product:
            latest_by_product[product_id] = row

    eligible_ids = {
        product_id
        for product_id, row in latest_by_product.items()
        if row.get("decision_status") in RETRYABLE_UNCERTAIN_STATUSES
        and retriever_version(row) < CANDIDATE_RETRIEVER_VERSION
    }
    eligible_products = [
        row for row in products
        if str(row.get("mezan_product_id") or row.get("id") or "") in eligible_ids
    ]
    return _round_robin(eligible_products, limit)


def _openai_client() -> AsyncOpenAI:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=503, detail={"code": "openai_not_configured"})
    return AsyncOpenAI(api_key=api_key, max_retries=0, timeout=35.0)


def _model() -> str:
    return os.environ.get("MEZAN_OPENAI_MODEL", "gpt-5-mini").strip() or "gpt-5-mini"


SEARCH_TERMS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "product_id": {"type": "string"},
                    "search_terms": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": 8,
                    },
                },
                "required": ["product_id", "search_terms"],
            },
        }
    },
    "required": ["items"],
}


CLASSIFICATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "product_id": {"type": "string"},
                    "category_id": {"type": "string"},
                    "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
                    "reason": {"type": "string"},
                    "evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": MAX_EVIDENCE_ITEMS,
                    },
                },
                "required": ["product_id", "category_id", "confidence", "reason", "evidence"],
            },
        }
    },
    "required": ["items"],
}


def _openai_error_code(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        nested = body.get("error")
        if isinstance(nested, dict) and nested.get("code"):
            return _text(nested.get("code"))
        if body.get("code"):
            return _text(body.get("code"))
    return _text(getattr(exc, "code", None))


def _is_credit_exhausted_openai_error(exc: Exception) -> bool:
    code = _openai_error_code(exc).casefold()
    if code in CREDIT_EXHAUSTED_ERROR_CODES:
        return True
    # Some SDK/proxy versions omit ``code`` but preserve it in the error body
    # or message. Keep this bounded to the known provider billing markers.
    haystack = f"{getattr(exc, 'body', '')} {exc}".casefold()
    return any(marker in haystack for marker in CREDIT_EXHAUSTED_ERROR_CODES)


def _is_retryable_openai_error(exc: Exception) -> bool:
    if _is_credit_exhausted_openai_error(exc):
        return False
    if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError)):
        return True
    try:
        status_code = int(getattr(exc, "status_code", 0) or 0)
    except (TypeError, ValueError):
        status_code = 0
    return status_code == 429 or 500 <= status_code < 600


def _run_error_fields(exc: Exception, *, now: datetime) -> dict[str, Any]:
    if _is_credit_exhausted_openai_error(exc):
        return {
            "status": "credit_exhausted",
            "finished_at": now,
            "heartbeat_at": now,
            "provider_error_code": (
                _openai_error_code(exc) or "credit_balance_exhausted"
            ),
            "action_required": "top_up_openai_credit",
            "error": CREDIT_EXHAUSTED_MESSAGE,
            "progress.phase": "provider_credit_exhausted",
            "progress.updated_at": now,
        }
    retryable_provider_error = _is_retryable_openai_error(exc)
    return {
        "status": "queued" if retryable_provider_error else "failed",
        "finished_at": None if retryable_provider_error else now,
        "heartbeat_at": now,
        "provider_error_code": _openai_error_code(exc) or None,
        "error": f"{type(exc).__name__}: {str(exc)[:300]}",
        "progress.phase": (
            "provider_rate_limited" if retryable_provider_error else "failed"
        ),
        "progress.updated_at": now,
    }


def _retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not hasattr(headers, "get"):
        return None
    raw_value = headers.get("retry-after")
    if raw_value in (None, ""):
        return None
    try:
        return max(
            0.0,
            min(float(raw_value), OPENAI_RETRY_MAX_SECONDS),
        )
    except (TypeError, ValueError):
        return None


async def _call_openai_with_backoff(
    operation: Callable[[], Awaitable[Any]],
    *,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> Any:
    """Retry transient provider failures without converting a whole wave to failure."""
    for attempt in range(OPENAI_RETRY_ATTEMPTS):
        try:
            result = await operation()
        except Exception as exc:
            if (
                not _is_retryable_openai_error(exc)
                or attempt + 1 >= OPENAI_RETRY_ATTEMPTS
            ):
                raise
            retry_after = _retry_after_seconds(exc)
            delay = (
                retry_after
                if retry_after is not None
                else min(
                    OPENAI_RETRY_MAX_SECONDS,
                    OPENAI_RETRY_BASE_SECONDS * (2 ** attempt),
                )
            )
            await sleep(delay)
            continue
        await sleep(OPENAI_REQUEST_SPACING_SECONDS)
        return result
    raise RuntimeError("openai_retry_loop_exhausted")


async def _ai_search_terms(client: AsyncOpenAI, evidences: list[dict[str, Any]]) -> dict[str, list[str]]:
    response = await _call_openai_with_backoff(
        lambda: client.responses.create(
        model=_model(),
        instructions=(
            "أنت خبير في Google Product Taxonomy. لكل منتج أعطِ من 2 إلى 8 عبارات بحث قصيرة "
            "بالعربية تساعد الخادم على العثور على التصنيف الرسمي الأدق. لا تعطِ أرقام تصنيفات، "
            "ولا تخترع حقائق أو ماركات، ولا تعتمد على تصنيف سلة وحده. استخدم الاسم والوصف والنوع "
            "والخيارات معًا. أمثلة لنمط العبارات فقط: قلادات، ملابس تقليدية، حقائب يد."
        ),
        input=json.dumps({"products": evidences}, ensure_ascii=False),
        max_output_tokens=1800,
        text={"format": {"type": "json_schema", "name": "google_taxonomy_search_terms", "strict": True, "schema": SEARCH_TERMS_SCHEMA}},
        )
    )
    payload = json.loads(response.output_text)
    result: dict[str, list[str]] = {}
    valid_ids = {str(row.get("product_id")) for row in evidences}
    for row in payload.get("items") or []:
        product_id = str(row.get("product_id") or "")
        if product_id not in valid_ids:
            continue
        terms = [_text(term)[:160] for term in (row.get("search_terms") or []) if _text(term)]
        result[product_id] = terms[:8]
    return result


async def _ai_classify_chunk(client: AsyncOpenAI, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    response = await _call_openai_with_backoff(
        lambda: client.responses.create(
        model=_model(),
        instructions=(
            "أنت مصنف منتجات داخل Mezan OS. اختر لكل منتج Google Product Category واحدة فقط من "
            "candidate_categories المرسلة لذلك المنتج. category_id يجب أن يكون حرفيًا من القائمة؛ "
            "إذا لم يوجد مرشح مناسب اتركه كسلسلة فارغة وخفّض الثقة. اختر التصنيف الأكثر تحديدًا "
            "الذي تدعمه الأدلة، ولا تعتمد على تصنيف سلة وحده. لا تخترع GTIN/MPN/brand. "
            "معايرة الثقة: 90-100 تطابق صريح وقوي بلا غموض؛ 70-89 مرجح لكن يحتاج مراجعة؛ "
            "أقل من 70 دليل ضعيف أو المرشحات غير كافية. السبب مختصر ومبني على حقائق المنتج."
        ),
        input=json.dumps({"products": rows}, ensure_ascii=False),
        max_output_tokens=2400,
        text={"format": {"type": "json_schema", "name": "google_taxonomy_classification", "strict": True, "schema": CLASSIFICATION_SCHEMA}},
        )
    )
    payload = json.loads(response.output_text)
    result: dict[str, dict[str, Any]] = {}
    valid_ids = {str(row.get("product_id")) for row in rows}
    for row in payload.get("items") or []:
        product_id = str(row.get("product_id") or "")
        if product_id in valid_ids:
            result[product_id] = row
    return result


async def _gather_bounded(
    items: list[Any],
    worker: Callable[[Any], Awaitable[Any]],
    *,
    concurrency: int,
) -> list[Any]:
    """Run rollout chunks concurrently without exceeding the provider budget."""
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def run(item: Any) -> Any:
        async with semaphore:
            return await worker(item)

    return await asyncio.gather(*(run(item) for item in items))


def _decision_status(
    *,
    current_id: str | None,
    chosen_id: str | None,
    confidence: int,
) -> str:
    if not chosen_id:
        return "low_confidence"
    if current_id:
        if current_id == chosen_id:
            return "no_change"
        return "review_required_existing_category"
    if confidence >= 90:
        return "high_confidence"
    if confidence >= 70:
        return "review_required"
    return "low_confidence"


def _run_counters(records: list[dict[str, Any]], selected_count: int) -> dict[str, int]:
    return {
        "selected": selected_count,
        "analyzed": sum(1 for row in records if row.get("decision_status") not in {"missing_data", "ai_failed"}),
        "no_change": sum(1 for row in records if row.get("decision_status") == "no_change"),
        "high_confidence": sum(1 for row in records if row.get("decision_status") == "high_confidence"),
        "review_required": sum(1 for row in records if str(row.get("decision_status")).startswith("review_required")),
        "low_confidence": sum(1 for row in records if row.get("decision_status") == "low_confidence"),
        "ai_failed": sum(1 for row in records if row.get("decision_status") == "ai_failed"),
        "missing_data": sum(1 for row in records if row.get("decision_status") == "missing_data"),
        "visual_checked": sum(
            1 for row in records
            if row.get("visual_verification_status") in {"consistent", "conflict", "unclear", "failed"}
        ),
        "visual_failed": sum(1 for row in records if row.get("visual_verification_status") == "failed"),
        "applied": sum(1 for row in records if row.get("apply_status") == "applied"),
    }


async def _ensure_indexes(db: Any) -> None:
    await db[RUNS].create_index([("user_id", 1), ("created_at", -1)], name="ix_google_taxonomy_ai_runs")
    await db[CLASSIFICATIONS].create_index(
        [("user_id", 1), ("run_id", 1), ("mezan_product_id", 1)],
        unique=True,
        name="uq_google_taxonomy_ai_run_product",
    )
    await db[CLASSIFICATIONS].create_index(
        [("user_id", 1), ("mezan_product_id", 1), ("classified_at", -1)],
        name="ix_google_taxonomy_ai_product_history",
    )


def _as_utc_datetime(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _run_needs_resume(run: dict[str, Any], *, now: datetime | None = None) -> bool:
    """Return whether an active run has no live worker lease.

    Old runs created before resumable rollout support have no lease fields and
    are therefore immediately eligible for recovery after deployment.
    """
    if run.get("status") not in ACTIVE_RUN_STATUSES:
        return False
    lease_expires_at = _as_utc_datetime(run.get("lease_expires_at"))
    return lease_expires_at is None or lease_expires_at <= (now or _now())


async def _recover_stale_run(db: Any, run: dict[str, Any]) -> dict[str, Any]:
    """Expose recoverability without turning interrupted work into failure.

    Actual recovery is claimed atomically by ``_execute_pilot``. Keeping the
    status active means the UI continues polling while a replacement container
    takes over the same run id.
    """
    if not _run_needs_resume(run):
        return run
    return {**run, "recovery_pending": True}


async def _claim_run_lease(
    db: Any,
    user_id: str,
    run_id: str,
    lease_owner: str,
) -> dict[str, Any] | None:
    run = await db[RUNS].find_one(
        {"user_id": user_id, "run_id": run_id, "status": {"$in": list(ACTIVE_RUN_STATUSES)}},
        {"_id": 0},
    )
    if not run:
        return None
    now = _now()
    started_at = run.get("started_at") or now
    update: dict[str, Any] = {
        "$set": {
            "status": "running",
            "started_at": started_at,
            "heartbeat_at": now,
            "lease_owner": lease_owner,
            "lease_expires_at": now + timedelta(seconds=RUN_LEASE_SECONDS),
            "finished_at": None,
        },
        "$inc": {"attempt_count": 1},
    }
    if run.get("started_at"):
        update["$inc"]["resume_count"] = 1
    claimed = await db[RUNS].update_one(
        {
            "user_id": user_id,
            "run_id": run_id,
            "status": {"$in": list(ACTIVE_RUN_STATUSES)},
            "$or": [
                {"lease_owner": {"$exists": False}},
                {"lease_owner": None},
                {"lease_expires_at": {"$lte": now}},
            ],
        },
        update,
    )
    if int(getattr(claimed, "modified_count", 0) or 0) != 1:
        return None
    return {**run, "status": "running", "started_at": started_at, "lease_owner": lease_owner}


async def _renew_run_lease(
    db: Any,
    user_id: str,
    run_id: str,
    lease_owner: str,
    *,
    phase: str,
) -> bool:
    now = _now()
    result = await db[RUNS].update_one(
        {
            "user_id": user_id,
            "run_id": run_id,
            "status": "running",
            "lease_owner": lease_owner,
        },
        {"$set": {
            "heartbeat_at": now,
            "lease_expires_at": now + timedelta(seconds=RUN_LEASE_SECONDS),
            "progress.phase": phase,
        }},
    )
    return int(getattr(result, "matched_count", 0) or 0) == 1


async def _lease_heartbeat_loop(
    db: Any,
    user_id: str,
    run_id: str,
    lease_owner: str,
) -> None:
    """Keep ownership live while a provider call or visual gate is in flight."""
    while True:
        await asyncio.sleep(max(10, RUN_LEASE_SECONDS // 3))
        now = _now()
        result = await db[RUNS].update_one(
            {
                "user_id": user_id,
                "run_id": run_id,
                "status": "running",
                "lease_owner": lease_owner,
            },
            {"$set": {
                "heartbeat_at": now,
                "lease_expires_at": now + timedelta(seconds=RUN_LEASE_SECONDS),
            }},
        )
        if int(getattr(result, "matched_count", 0) or 0) != 1:
            return


async def _load_run_records(db: Any, user_id: str, run_id: str) -> list[dict[str, Any]]:
    return await db[CLASSIFICATIONS].find(
        {"user_id": user_id, "run_id": run_id},
        {"_id": 0},
    ).to_list(length=MAX_PILOT_LIMIT)


async def _persist_records(db: Any, records: list[dict[str, Any]]) -> None:
    """Durably checkpoint product results without overwriting prior work."""
    for record in records:
        try:
            await db[CLASSIFICATIONS].update_one(
                {
                    "user_id": record["user_id"],
                    "run_id": record["run_id"],
                    "mezan_product_id": record["mezan_product_id"],
                },
                {"$setOnInsert": record},
                upsert=True,
            )
        except DuplicateKeyError:
            # Another worker may have completed the same product exactly as an
            # expired lease changed hands. The unique index makes that safe.
            continue


async def _checkpoint_run(
    db: Any,
    user_id: str,
    run_id: str,
    lease_owner: str,
    *,
    selected_count: int,
    phase: str,
    coverage: dict[str, int] | None,
) -> list[dict[str, Any]]:
    records = await _load_run_records(db, user_id, run_id)
    counters = _run_counters(records, selected_count)
    now = _now()
    processed = len(records)
    set_fields: dict[str, Any] = {
        "heartbeat_at": now,
        "lease_expires_at": now + timedelta(seconds=RUN_LEASE_SECONDS),
        "counters": counters,
        "progress": {
            "phase": phase,
            "saved": processed,
            "remaining": max(0, selected_count - processed),
            "updated_at": now,
        },
    }
    if coverage is not None:
        seen_before = int(coverage.get("seen_before") or 0)
        completed_now = sum(1 for row in records if row.get("decision_status") != "ai_failed")
        set_fields["coverage"] = {
            **coverage,
            "processed_now": processed,
            "seen_after": min(int(coverage.get("total_products") or 0), seen_before + completed_now),
            "remaining_after": max(
                0,
                int(coverage.get("total_products") or 0) - seen_before - completed_now,
            ),
        }
    await db[RUNS].update_one(
        {
            "user_id": user_id,
            "run_id": run_id,
            "status": "running",
            "lease_owner": lease_owner,
        },
        {"$set": set_fields},
    )
    return records


async def _execute_pilot(
    db: Any,
    user_id: str,
    run_id: str,
    limit: int,
    selection_mode: str = "sample",
) -> None:
    lease_owner = uuid.uuid4().hex
    run = await _claim_run_lease(db, user_id, run_id, lease_owner)
    if not run:
        return
    client: AsyncOpenAI | None = None
    heartbeat_task = asyncio.create_task(
        _lease_heartbeat_loop(db, user_id, run_id, lease_owner)
    )
    try:
        await _renew_run_lease(
            db, user_id, run_id, lease_owner, phase="loading_taxonomy"
        )
        taxonomy_version, taxonomy = await _get_google_taxonomy()
        by_id, by_path = _taxonomy_maps(taxonomy)
        selected_ids = [
            str(value)
            for value in (run.get("selected_product_ids") or [])
            if value not in (None, "")
        ]
        coverage: dict[str, int] | None = (
            dict(run.get("coverage")) if isinstance(run.get("coverage"), dict) else None
        )
        retry_queue: dict[str, int] | None = (
            dict(run.get("retry_queue")) if isinstance(run.get("retry_queue"), dict) else None
        )
        selection_by_id: dict[str, dict[str, Any]] = {}
        if not selected_ids:
            catalog_rows = await db[PRODUCTS].find(
                {"user_id": user_id, "archived": {"$ne": True}},
                PILOT_SELECTION_PROJECTION,
            ).sort(
                [("details_loaded", -1), ("updated_at", -1), ("name", 1)]
            ).limit(MAX_PRODUCTS_SCANNED).to_list(length=MAX_PRODUCTS_SCANNED)
            if selection_mode == "next_unseen":
                seen_values = await db[CLASSIFICATIONS].distinct(
                    "mezan_product_id",
                    _successfully_seen_filter(user_id),
                )
                seen_ids = {str(value) for value in seen_values if value not in (None, "")}
                active_ids = {
                    str(row.get("mezan_product_id") or row.get("id") or "")
                    for row in catalog_rows
                }
                seen_before = len(active_ids.intersection(seen_ids))
                selected_summaries = _select_unseen_products(
                    catalog_rows, seen_ids, limit
                )
                coverage = {
                    "total_products": len(catalog_rows),
                    "seen_before": seen_before,
                    "selected_now": len(selected_summaries),
                    "processed_now": 0,
                    "seen_after": seen_before,
                    "remaining_after": max(0, len(catalog_rows) - seen_before),
                }
            elif selection_mode == "retry_review":
                newest_classifications = await db[CLASSIFICATIONS].find(
                    {"user_id": user_id},
                    {
                        "_id": 0,
                        "mezan_product_id": 1,
                        "decision_status": 1,
                        "candidate_retriever_version": 1,
                        "classified_at": 1,
                    },
                ).sort([("classified_at", -1)]).limit(
                    MAX_CLASSIFICATIONS_SCANNED
                ).to_list(length=MAX_CLASSIFICATIONS_SCANNED)
                selected_summaries = _select_retryable_uncertain_products(
                    catalog_rows,
                    newest_classifications,
                    limit,
                )
                retry_queue = {
                    "eligible_before": len(selected_summaries),
                    "selected_now": len(selected_summaries),
                    "candidate_retriever_version": CANDIDATE_RETRIEVER_VERSION,
                }
            else:
                selected_summaries = _select_pilot_products(catalog_rows, limit)
            selected_ids = [
                str(row.get("mezan_product_id") or row.get("id") or "")
                for row in selected_summaries
                if row.get("mezan_product_id") or row.get("id")
            ]
            selection_by_id = {
                str(row.get("mezan_product_id") or row.get("id") or ""): row
                for row in selected_summaries
            }
            await db[RUNS].update_one(
                {
                    "user_id": user_id,
                    "run_id": run_id,
                    "status": "running",
                    "lease_owner": lease_owner,
                },
                {"$set": {
                    "selected_product_ids": selected_ids,
                    "selection_saved_at": _now(),
                    "taxonomy_version": taxonomy_version,
                    "coverage": coverage,
                    "retry_queue": retry_queue,
                    "counters.selected": len(selected_ids),
                }},
            )

        # Fetch evidence only for the durable selection (at most 200 rows).
        # Numeric legacy ids are included in both their stored and string forms.
        selected_lookup_values = _selected_lookup_values(selected_ids)
        products = await db[PRODUCTS].find(
            {
                "user_id": user_id,
                "archived": {"$ne": True},
                "$or": [
                    {"mezan_product_id": {"$in": selected_lookup_values}},
                    {"id": {"$in": selected_lookup_values}},
                ],
            },
            PILOT_PRODUCT_PROJECTION,
        ).to_list(length=len(selected_ids))
        product_by_id = {
            str(row.get("mezan_product_id") or row.get("id") or ""): row
            for row in products
        }
        selected = [
            product_by_id.get(product_id)
            or selection_by_id.get(product_id)
            or {"mezan_product_id": product_id}
            for product_id in selected_ids
        ]

        if not selected:
            if selection_mode in {"next_unseen", "retry_review"}:
                await db[RUNS].update_one(
                    {
                        "user_id": user_id,
                        "run_id": run_id,
                        "status": "running",
                        "lease_owner": lease_owner,
                    },
                    {
                        "$set": {
                            "status": "completed",
                            "finished_at": _now(),
                            "taxonomy_version": taxonomy_version,
                            "counters": _run_counters([], 0),
                            "coverage": coverage,
                            "retry_queue": retry_queue,
                            "progress": {"phase": "completed", "saved": 0, "remaining": 0, "updated_at": _now()},
                            "error": None,
                        },
                        "$unset": {"lease_owner": "", "lease_expires_at": ""},
                    },
                )
                return
            raise RuntimeError("no_products_available_for_pilot")

        existing_records = await _load_run_records(db, user_id, run_id)
        saved_product_ids = {
            str(row.get("mezan_product_id") or "")
            for row in existing_records
        }
        remaining_selected = [
            product for product in selected
            if str(product.get("mezan_product_id") or product.get("id") or "") not in saved_product_ids
        ]
        evidences: list[dict[str, Any]] = []
        missing_records: list[dict[str, Any]] = []
        for product in remaining_selected:
            evidence = _product_evidence(product)
            product_id = evidence["product_id"]
            product_by_id[product_id] = product
            if not evidence.get("name"):
                missing_records.append({
                    "id": uuid.uuid4().hex,
                    "user_id": user_id,
                    "run_id": run_id,
                    "mezan_product_id": product_id,
                    "salla_product_id": evidence.get("salla_product_id"),
                    "product_name": "",
                    "main_image": product.get("main_image"),
                    "classification_input_revision": _input_revision(evidence),
                    "classification_source": "openai_pilot",
                    "candidate_retriever_version": CANDIDATE_RETRIEVER_VERSION,
                    "classified_at": _now(),
                    "decision_status": "missing_data",
                    "apply_status": "not_eligible",
                    "ai_confidence": 0,
                    "ai_reason": "اسم المنتج مفقود؛ لم يتم إرسال المنتج إلى الذكاء الاصطناعي.",
                    "google_category_id": None,
                    "google_category_path": None,
                    "current_google_category": evidence.get("current_google_category"),
                    "model": _model(),
                })
                continue
            evidences.append(evidence)

        if missing_records:
            await _persist_records(db, missing_records)
            await _checkpoint_run(
                db,
                user_id,
                run_id,
                lease_owner,
                selected_count=len(selected_ids),
                phase="saved_missing_data",
                coverage=coverage,
            )

        if not evidences:
            records = await _checkpoint_run(
                db,
                user_id,
                run_id,
                lease_owner,
                selected_count=len(selected_ids),
                phase="finalizing",
                coverage=coverage,
            )
            counters = _run_counters(records, len(selected_ids))
            status = (
                "completed"
                if counters["ai_failed"] == 0 and counters["visual_failed"] == 0
                else "completed_with_errors"
            )
            await db[RUNS].update_one(
                {"user_id": user_id, "run_id": run_id, "lease_owner": lease_owner},
                {
                    "$set": {
                        "status": status,
                        "finished_at": _now(),
                        "taxonomy_version": taxonomy_version,
                        "model": _model(),
                        "counters": counters,
                        "progress.phase": "completed",
                        "progress.remaining": 0,
                        "error": None,
                    },
                    "$unset": {"lease_owner": "", "lease_expires_at": ""},
                },
            )
            return

        client = _openai_client()
        term_generation_errors: list[str] = []

        async def generate_search_terms(
            evidence_chunk: list[dict[str, Any]],
        ) -> tuple[dict[str, list[str]], Exception | None]:
            try:
                return await _ai_search_terms(client, evidence_chunk), None
            except Exception as exc:  # per-chunk fallback keeps the rollout progressing
                return {}, exc

        async def classify_chunk(
            chunk: list[dict[str, Any]],
        ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], Exception | None]:
            try:
                return chunk, await _ai_classify_chunk(client, chunk), None
            except Exception as exc:  # per-chunk partial failure isolation
                return chunk, {}, exc

        # Complete search -> classify -> persist for at most 15 products at a
        # time. Each wave becomes durable before the next one starts, avoiding
        # the old all-200 search-term phase that could lose every minute of work
        # if the small production container restarted.
        for start in range(0, len(evidences), CLASSIFICATION_WAVE_SIZE):
            evidence_wave = evidences[start:start + CLASSIFICATION_WAVE_SIZE]
            if not await _renew_run_lease(
                db, user_id, run_id, lease_owner, phase="generating_search_terms"
            ):
                return
            search_terms_by_product, term_error = await generate_search_terms(
                evidence_wave
            )
            if term_error:
                term_generation_errors.append(type(term_error).__name__)
                if (
                    _is_credit_exhausted_openai_error(term_error)
                    or _is_retryable_openai_error(term_error)
                ):
                    raise term_error

            classification_inputs: list[dict[str, Any]] = []
            metadata_by_product: dict[str, dict[str, Any]] = {}
            for evidence in evidence_wave:
                product_id = evidence["product_id"]
                current_id = _resolve_current_id(
                    str(evidence.get("current_google_category") or ""),
                    by_id,
                    by_path,
                )
                ai_terms = search_terms_by_product.get(product_id) or []
                candidates = _candidate_rows(
                    evidence, ai_terms, taxonomy, current_id
                )
                metadata_by_product[product_id] = {
                    "evidence": evidence,
                    "current_id": current_id,
                    "candidates": candidates,
                    "input_revision": _input_revision(evidence),
                    "limited_evidence": _evidence_limited(evidence),
                    "term_source": (
                        "openai" if ai_terms else "deterministic_fallback"
                    ),
                }
                classification_inputs.append({
                    "product_id": product_id,
                    "facts": evidence,
                    "candidate_categories": [
                        {
                            "id": str(row.get("id")),
                            "name": row.get("name"),
                            "path": row.get("path"),
                        }
                        for row in candidates
                    ],
                })

            classification_chunks = [
                classification_inputs[index:index + CLASSIFICATION_CHUNK_SIZE]
                for index in range(
                    0, len(classification_inputs), CLASSIFICATION_CHUNK_SIZE
                )
            ]
            if not await _renew_run_lease(
                db, user_id, run_id, lease_owner, phase="classifying_products"
            ):
                return
            classification_chunk_results = await _gather_bounded(
                classification_chunks,
                classify_chunk,
                concurrency=CLASSIFICATION_CONCURRENCY,
            )
            wave_records: list[dict[str, Any]] = []
            for chunk, ai_results, chunk_error in classification_chunk_results:
                if chunk_error is not None:
                    # Do not checkpoint a transient provider throttle as a
                    # durable product failure. Requeue the same run so its
                    # saved waves remain intact and the unsaved wave resumes.
                    if (
                        _is_credit_exhausted_openai_error(chunk_error)
                        or _is_retryable_openai_error(chunk_error)
                    ):
                        raise chunk_error
                    for item in chunk:
                        product_id = item["product_id"]
                        meta = metadata_by_product[product_id]
                        evidence = meta["evidence"]
                        wave_records.append({
                            "id": uuid.uuid4().hex,
                            "user_id": user_id,
                            "run_id": run_id,
                            "mezan_product_id": product_id,
                            "salla_product_id": evidence.get("salla_product_id"),
                            "product_name": evidence.get("name"),
                            "main_image": product_by_id.get(product_id, {}).get("main_image"),
                            "classification_input_revision": meta["input_revision"],
                            "classification_source": "openai_pilot",
                            "candidate_retriever_version": CANDIDATE_RETRIEVER_VERSION,
                            "classified_at": _now(),
                            "decision_status": "ai_failed",
                            "apply_status": "not_eligible",
                            "ai_confidence": 0,
                            "ai_reason": f"تعذر تصنيف هذه الدفعة: {type(chunk_error).__name__}",
                            "google_category_id": None,
                            "google_category_path": None,
                            "current_google_category": evidence.get("current_google_category"),
                            "model": _model(),
                        })
                    continue

                for item in chunk:
                    product_id = item["product_id"]
                    meta = metadata_by_product[product_id]
                    evidence = meta["evidence"]
                    result = ai_results.get(product_id) or {}
                    chosen_id = _text(result.get("category_id"))
                    candidate_ids = {str(row.get("id")) for row in meta["candidates"]}
                    if chosen_id and chosen_id not in candidate_ids:
                        chosen_id = ""
                        invalid_reason = "أعاد النموذج تصنيفًا خارج قائمة المرشحين الرسمية؛ تم رفضه."
                    else:
                        invalid_reason = ""
                    confidence = max(0, min(100, int(result.get("confidence") or 0)))
                    if meta["limited_evidence"]:
                        confidence = min(confidence, 79)
                    category = by_id.get(chosen_id) if chosen_id else None
                    current_id = meta["current_id"]
                    status = _decision_status(current_id=current_id, chosen_id=chosen_id or None, confidence=confidence)
                    reason = _text(result.get("reason"))[:MAX_REASON_CHARS] or invalid_reason or "لم يقدم النموذج سببًا صالحًا."
                    if invalid_reason:
                        reason = invalid_reason
                    wave_records.append({
                        "id": uuid.uuid4().hex,
                        "user_id": user_id,
                        "run_id": run_id,
                        "mezan_product_id": product_id,
                        "salla_product_id": evidence.get("salla_product_id"),
                        "product_name": evidence.get("name"),
                        "main_image": product_by_id.get(product_id, {}).get("main_image"),
                        "classification_input_revision": meta["input_revision"],
                        "classification_source": "openai_pilot",
                        "candidate_retriever_version": CANDIDATE_RETRIEVER_VERSION,
                        "classified_at": _now(),
                        "decision_status": status,
                        "apply_status": "not_needed" if status == "no_change" else ("pending" if status == "high_confidence" else "not_eligible"),
                        "ai_confidence": confidence,
                        "ai_reason": reason,
                        "ai_evidence": [_text(value)[:220] for value in (result.get("evidence") or [])[:MAX_EVIDENCE_ITEMS]],
                        "google_category_id": chosen_id or None,
                        "google_category_path": category.get("path") if category else None,
                        "google_category_name": category.get("name") if category else None,
                        "current_google_category": evidence.get("current_google_category"),
                        "current_google_category_id": current_id,
                        "candidate_count": len(meta["candidates"]),
                        "evidence_limited": meta["limited_evidence"],
                        "search_term_source": meta["term_source"],
                        "visual_verification_status": result.get("visual_verification_status"),
                        "visual_verification_attempts": int(result.get("visual_verification_attempts") or 0),
                        "visual_verification_error_code": result.get("visual_verification_error_code"),
                        "model": _model(),
                    })
            await _persist_records(db, wave_records)
            await _checkpoint_run(
                db,
                user_id,
                run_id,
                lease_owner,
                selected_count=len(selected_ids),
                phase="checkpoint_saved",
                coverage=coverage,
            )

        term_generation_error = ",".join(
            sorted(set(term_generation_errors))
        ) or None

        records = await _checkpoint_run(
            db,
            user_id,
            run_id,
            lease_owner,
            selected_count=len(selected_ids),
            phase="finalizing",
            coverage=coverage,
        )
        counters = _run_counters(records, len(selected_ids))
        status = (
            "completed"
            if counters["ai_failed"] == 0 and counters["visual_failed"] == 0
            else "completed_with_errors"
        )
        await db[RUNS].update_one(
            {"user_id": user_id, "run_id": run_id, "lease_owner": lease_owner},
            {
                "$set": {
                    "status": status,
                    "finished_at": _now(),
                    "taxonomy_version": taxonomy_version,
                    "model": _model(),
                    "counters": counters,
                    "progress.phase": "completed",
                    "progress.remaining": 0,
                    "term_generation_error": term_generation_error,
                    "error": None,
                },
                "$unset": {"lease_owner": "", "lease_expires_at": ""},
            },
        )
    except asyncio.CancelledError:
        # A graceful container shutdown must release ownership immediately so
        # the replacement worker can continue the same run without waiting for
        # the lease timeout. A hard crash is still covered by lease expiry.
        await db[RUNS].update_one(
            {"user_id": user_id, "run_id": run_id, "lease_owner": lease_owner},
            {
                "$set": {
                    "status": "queued",
                    "heartbeat_at": _now(),
                    "progress.phase": "interrupted",
                },
                "$unset": {"lease_owner": "", "lease_expires_at": ""},
            },
        )
        raise
    except Exception as exc:
        now = _now()
        set_fields = _run_error_fields(exc, now=now)
        await db[RUNS].update_one(
            {"user_id": user_id, "run_id": run_id, "lease_owner": lease_owner},
            {
                "$set": set_fields,
                "$unset": {"lease_owner": "", "lease_expires_at": ""},
            },
        )
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass


async def _run_payload(db: Any, user_id: str, run: dict[str, Any]) -> dict[str, Any]:
    run_id = str(run.get("run_id") or "")
    items = await db[CLASSIFICATIONS].find(
        {"user_id": user_id, "run_id": run_id}, {"_id": 0}
    ).sort([("ai_confidence", -1), ("product_name", 1)]).to_list(length=MAX_PILOT_LIMIT)
    return {
        "ok": run.get("status") not in {"failed"},
        "run": _serialize(run),
        "items": [_serialize(row) for row in items],
        "writes_to_salla": False,
        "auto_apply_enabled": False,
        "apply_confirmation": APPLY_CONFIRMATION,
    }


def _schedule_pilot_task(
    task_registry: dict[str, asyncio.Task[Any]],
    db: Any,
    user_id: str,
    run_id: str,
    limit: int,
    selection_mode: str,
) -> asyncio.Task[Any]:
    """Run a pilot independently from the HTTP request that awakened it.

    FastAPI ``BackgroundTasks`` are part of the response lifecycle and can be
    cancelled by the hosting layer. This registry keeps one durable in-process
    task per run; Mongo's lease remains the cross-process concurrency guard.
    """
    task_key = f"{user_id}:{run_id}"
    existing = task_registry.get(task_key)
    if existing is not None and not existing.done():
        return existing

    task = asyncio.create_task(
        _execute_pilot(db, user_id, run_id, limit, selection_mode)
    )
    task_registry[task_key] = task

    def remove_completed(done: asyncio.Task[Any]) -> None:
        if task_registry.get(task_key) is done:
            task_registry.pop(task_key, None)
        if not done.cancelled():
            # Retrieve any unexpected BaseException so it cannot become an
            # unobserved-task warning. Normal failures are persisted by the
            # executor itself.
            done.exception()

    task.add_done_callback(remove_completed)
    return task


async def _resume_active_runs_once(
    db: Any,
    task_registry: dict[str, asyncio.Task[Any]],
) -> None:
    runs = await db[RUNS].find(
        {"status": {"$in": list(ACTIVE_RUN_STATUSES)}},
        {"_id": 0},
    ).sort([("created_at", 1)]).to_list(length=100)
    if not runs:
        return
    for run in runs:
        if not run.get("user_id") or not run.get("run_id"):
            continue
        _schedule_pilot_task(
            task_registry,
            db,
            str(run.get("user_id") or ""),
            str(run.get("run_id") or ""),
            int(run.get("requested_limit") or DEFAULT_PILOT_LIMIT),
            str(run.get("selection_mode") or "sample"),
        )


async def _resumable_run_loop(
    db: Any,
    task_registry: dict[str, asyncio.Task[Any]],
) -> None:
    while True:
        try:
            await _resume_active_runs_once(db, task_registry)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A transient database/startup problem must not terminate recovery.
            pass
        await asyncio.sleep(RESUME_SCAN_SECONDS)


def make_product_google_taxonomy_ai_pilot_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(
        prefix="/ai-store-operations/product-intelligence/google-taxonomy",
        tags=["AI Product Manager - Google Taxonomy Pilot"],
    )
    resume_task: asyncio.Task[Any] | None = None
    worker_tasks: dict[str, asyncio.Task[Any]] = {}

    def schedule_pilot(
        user_id: str,
        run_id: str,
        limit: int,
        selection_mode: str,
    ) -> asyncio.Task[Any]:
        return _schedule_pilot_task(
            worker_tasks,
            db,
            user_id,
            run_id,
            limit,
            selection_mode,
        )

    @router.on_event("startup")
    async def start_resumable_run_loop() -> None:
        nonlocal resume_task
        if resume_task is None or resume_task.done():
            from boot_runtime import wait_for_local_readiness

            async def ready_loop() -> None:
                await wait_for_local_readiness()
                await _resumable_run_loop(db, worker_tasks)

            resume_task = asyncio.create_task(
                ready_loop()
            )

    @router.on_event("shutdown")
    async def stop_resumable_run_loop() -> None:
        nonlocal resume_task
        if resume_task is None:
            return
        resume_task.cancel()
        try:
            await resume_task
        except asyncio.CancelledError:
            pass
        resume_task = None
        active_workers = list(worker_tasks.values())
        for task in active_workers:
            task.cancel()
        if active_workers:
            await asyncio.gather(*active_workers, return_exceptions=True)
        worker_tasks.clear()

    @router.post("/pilot")
    async def start_pilot(
        payload: PilotStartIn = Body(default=PilotStartIn()),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        await _ensure_indexes(db)
        user_id = str(user["id"])
        provider = openai_runtime_status()
        if not provider.get("connected"):
            raise HTTPException(status_code=503, detail={"code": "openai_not_configured", "message": "OpenAI غير مهيأ في بيئة الإنتاج."})
        active = await db[RUNS].find_one(
            {"user_id": user_id, "status": {"$in": list(ACTIVE_RUN_STATUSES)}},
            {"_id": 0}, sort=[("created_at", -1)],
        )
        if active:
            active = await _recover_stale_run(db, active)
            if active.get("status") in ACTIVE_RUN_STATUSES and _run_needs_resume(active):
                schedule_pilot(
                    user_id,
                    str(active.get("run_id") or ""),
                    int(active.get("requested_limit") or DEFAULT_PILOT_LIMIT),
                    str(active.get("selection_mode") or "sample"),
                )
                return {**(await _run_payload(db, user_id, active)), "reused": True}

        run_id = uuid.uuid4().hex
        now = _now()
        mode = {
            "sample": "proposal_only_pilot",
            "next_unseen": "proposal_only_next_unseen",
            "retry_review": "proposal_only_retry_review",
        }[payload.selection_mode]
        run = {
            "run_id": run_id,
            "user_id": user_id,
            "status": "queued",
            "requested_limit": payload.limit,
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "heartbeat_at": None,
            "lease_owner": None,
            "lease_expires_at": None,
            "attempt_count": 0,
            "resume_count": 0,
            "selected_product_ids": [],
            "model": _model(),
            "taxonomy_version": None,
            "counters": {
                "selected": 0, "analyzed": 0, "no_change": 0, "high_confidence": 0,
                "review_required": 0, "low_confidence": 0, "ai_failed": 0,
                "missing_data": 0, "visual_checked": 0, "visual_failed": 0,
                "applied": 0,
            },
            "mode": mode,
            "selection_mode": payload.selection_mode,
            "progress": {
                "phase": "queued",
                "saved": 0,
                "remaining": payload.limit,
                "updated_at": now,
            },
            "writes_to_salla": False,
            "error": None,
        }
        await db[RUNS].insert_one(run)
        schedule_pilot(
            user_id,
            run_id,
            payload.limit,
            payload.selection_mode,
        )
        return await _run_payload(db, user_id, run)

    @router.get("/pilot/latest")
    async def latest_pilot(
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        await _ensure_indexes(db)
        user_id = str(user["id"])
        run = await db[RUNS].find_one({"user_id": user_id}, {"_id": 0}, sort=[("created_at", -1)])
        if not run:
            return {"ok": True, "run": None, "items": [], "writes_to_salla": False, "auto_apply_enabled": False, "apply_confirmation": APPLY_CONFIRMATION}
        run = await _recover_stale_run(db, run)
        if run.get("status") in ACTIVE_RUN_STATUSES and _run_needs_resume(run):
            schedule_pilot(
                user_id,
                str(run.get("run_id") or ""),
                int(run.get("requested_limit") or DEFAULT_PILOT_LIMIT),
                str(run.get("selection_mode") or "sample"),
            )
        return await _run_payload(db, user_id, run)

    @router.get("/pilot/{run_id}")
    async def get_pilot(
        run_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        await _ensure_indexes(db)
        user_id = str(user["id"])
        run = await db[RUNS].find_one({"user_id": user_id, "run_id": run_id}, {"_id": 0})
        if not run:
            raise HTTPException(status_code=404, detail={"code": "taxonomy_pilot_not_found"})
        run = await _recover_stale_run(db, run)
        if run.get("status") in ACTIVE_RUN_STATUSES and _run_needs_resume(run):
            schedule_pilot(
                user_id,
                run_id,
                int(run.get("requested_limit") or DEFAULT_PILOT_LIMIT),
                str(run.get("selection_mode") or "sample"),
            )
        return await _run_payload(db, user_id, run)

    @router.post("/pilot/{run_id}/apply-high-confidence")
    async def apply_high_confidence(
        run_id: str,
        payload: PilotApplyIn = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        await _ensure_indexes(db)
        if payload.confirmation != APPLY_CONFIRMATION:
            raise HTTPException(status_code=409, detail={"code": "taxonomy_apply_confirmation_required"})
        user_id = str(user["id"])
        run = await db[RUNS].find_one({"user_id": user_id, "run_id": run_id}, {"_id": 0})
        if not run:
            raise HTTPException(status_code=404, detail={"code": "taxonomy_pilot_not_found"})
        if run.get("status") not in {"completed", "completed_with_errors"}:
            raise HTTPException(status_code=409, detail={"code": "taxonomy_pilot_not_complete"})

        rows = await db[CLASSIFICATIONS].find(
            {"user_id": user_id, "run_id": run_id, "decision_status": "high_confidence", "apply_status": "pending"},
            {"_id": 0},
        ).to_list(length=MAX_PILOT_LIMIT)
        applied = stale = failed = 0
        for row in rows:
            product_id = str(row.get("mezan_product_id") or "")
            product = await db[PRODUCTS].find_one(
                {"user_id": user_id, "$or": [{"mezan_product_id": product_id}, {"id": product_id}]},
                {"_id": 0},
            )
            if not product:
                failed += 1
                await db[CLASSIFICATIONS].update_one({"user_id": user_id, "run_id": run_id, "mezan_product_id": product_id}, {"$set": {"apply_status": "failed_product_missing"}})
                continue
            current_revision = _input_revision(_product_evidence(product))
            if current_revision != row.get("classification_input_revision"):
                stale += 1
                await db[CLASSIFICATIONS].update_one({"user_id": user_id, "run_id": run_id, "mezan_product_id": product_id}, {"$set": {"apply_status": "stale_source_changed"}})
                continue
            category_id = _text(row.get("google_category_id"))
            category_path = _text(row.get("google_category_path"))
            if not category_id or int(row.get("ai_confidence") or 0) < 90:
                failed += 1
                continue

            before = {
                "google_category": product.get("google_category"),
                "google_category_id": product.get("google_category_id"),
                "google_category_path": product.get("google_category_path"),
            }
            now = _now()
            update = {
                "google_category": category_id,
                "google_category_id": category_id,
                "google_category_path": category_path or None,
                "ai_confidence": int(row.get("ai_confidence") or 0),
                "ai_reason": row.get("ai_reason"),
                "classification_source": "openai_pilot_human_approved",
                "classified_at": row.get("classified_at") or now,
                "google_taxonomy_authority": "mezan",
                "salla_sync_status": "mezan_managed",
                "salla_synced_at": None,
                "salla_sync_error": None,
                "salla_sync_reason": "salla_public_api_google_taxonomy_writer_not_supported",
                "updated_at": now,
            }
            await db[PRODUCTS].update_one({"user_id": user_id, "mezan_product_id": product_id}, {"$set": update})
            verify = await db[PRODUCTS].find_one({"user_id": user_id, "mezan_product_id": product_id}, {"_id": 0, "google_category_id": 1, "google_category": 1})
            verified = bool(verify and _text(verify.get("google_category_id") or verify.get("google_category")) == category_id)
            if not verified:
                failed += 1
                await db[CLASSIFICATIONS].update_one({"user_id": user_id, "run_id": run_id, "mezan_product_id": product_id}, {"$set": {"apply_status": "failed_verification"}})
                continue

            action_id = uuid.uuid4().hex
            await db[AI_ACTION_LOG].insert_one({
                "id": action_id,
                "user_id": user_id,
                "actor_user_id": user_id,
                "action": "google_taxonomy_apply_high_confidence",
                "risk": "low",
                "source": "ai_product_manager_google_taxonomy_pilot",
                "run_id": run_id,
                "mezan_product_id": product_id,
                "salla_product_id": row.get("salla_product_id"),
                "before": before,
                "after": {"google_category": category_id, "google_category_id": category_id, "google_category_path": category_path or None},
                "ai_confidence": row.get("ai_confidence"),
                "ai_reason": row.get("ai_reason"),
                "provider_write_reached": False,
                "verified": True,
                "occurred_at": now,
            })
            await db[CLASSIFICATIONS].update_one(
                {"user_id": user_id, "run_id": run_id, "mezan_product_id": product_id},
                {"$set": {"apply_status": "applied", "applied_at": now, "approved_by": user_id, "action_id": action_id}},
            )
            applied += 1

        counters = dict(run.get("counters") or {})
        counters["applied"] = int(counters.get("applied") or 0) + applied
        await db[RUNS].update_one({"user_id": user_id, "run_id": run_id}, {"$set": {"counters": counters, "last_apply_at": _now()}})
        updated_run = await db[RUNS].find_one({"user_id": user_id, "run_id": run_id}, {"_id": 0})
        return {
            **(await _run_payload(db, user_id, updated_run or run)),
            "apply_result": {"eligible": len(rows), "applied": applied, "stale": stale, "failed": failed},
        }

    return router
