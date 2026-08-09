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

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query
from openai import APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, Field

from ai_provider_status import openai_runtime_status
from product_category_variant_support import _get_google_taxonomy
from product_v2_routes import PRODUCTS

CLASSIFICATIONS = "mezan_product_google_taxonomy_classifications_v2"
RUNS = "mezan_product_google_taxonomy_runs_v2"
AI_ACTION_LOG = "mezan_ai_action_log_v2"

DEFAULT_PILOT_LIMIT = 20
MIN_PILOT_LIMIT = 20
MAX_PILOT_LIMIT = 200
PILOT_STALE_MINUTES = 120
MAX_PRODUCTS_SCANNED = 5000
MAX_CANDIDATES = 24
SEARCH_TERMS_CHUNK_SIZE = 20
CLASSIFICATION_CHUNK_SIZE = 5
SEARCH_TERMS_CONCURRENCY = 3
CLASSIFICATION_CONCURRENCY = 3
MAX_DESCRIPTION_CHARS = 900
MAX_REASON_CHARS = 500
MAX_EVIDENCE_ITEMS = 5

APPLY_CONFIRMATION = "اعتماد تصنيفات Google عالية الثقة في ميزان"


class PilotStartIn(BaseModel):
    limit: int = Field(default=DEFAULT_PILOT_LIMIT, ge=MIN_PILOT_LIMIT, le=MAX_PILOT_LIMIT)
    selection_mode: Literal["sample", "next_unseen"] = "sample"


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
    "تيشيرت": ["قمصان", "تي شيرت"],
    "قميص": ["قمصان"],
    "مريول": ["ملابس", "فساتين"],
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
    for value in [evidence.get("name"), *(evidence.get("salla_categories") or [])]:
        normalized = _normalize_ar(value)
        if normalized:
            terms.append(normalized[:160])
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
    return result[:12]


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
        path_tokens = _tokens(path)
        score = 0.0
        for phrase, phrase_tokens in term_rows:
            if not phrase:
                continue
            if phrase in path:
                score += 12.0 + min(6.0, len(phrase_tokens) * 1.5)
            overlap = len(phrase_tokens.intersection(path_tokens))
            if overlap:
                score += overlap * 3.0
                if phrase_tokens and overlap == len(phrase_tokens):
                    score += 4.0
        if score > 0:
            # Prefer more precise descendants when semantic score is equal.
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


def _openai_client() -> AsyncOpenAI:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=503, detail={"code": "openai_not_configured"})
    return AsyncOpenAI(api_key=api_key, max_retries=1, timeout=35.0)


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


async def _ai_search_terms(client: AsyncOpenAI, evidences: list[dict[str, Any]]) -> dict[str, list[str]]:
    response = await client.responses.create(
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
    response = await client.responses.create(
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
        "applied": 0,
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


async def _recover_stale_run(db: Any, run: dict[str, Any]) -> dict[str, Any]:
    if run.get("status") not in {"queued", "running"}:
        return run
    started = run.get("started_at") or run.get("created_at")
    if isinstance(started, str):
        try:
            started = datetime.fromisoformat(started.replace("Z", "+00:00"))
        except ValueError:
            started = None
    if isinstance(started, datetime):
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if started > _now() - timedelta(minutes=PILOT_STALE_MINUTES):
            return run
    await db[RUNS].update_one(
        {"user_id": run.get("user_id"), "run_id": run.get("run_id"), "status": {"$in": ["queued", "running"]}},
        {"$set": {"status": "failed", "finished_at": _now(), "error": "pilot_stale_after_runtime_interruption"}},
    )
    return {**run, "status": "failed", "finished_at": _now(), "error": "pilot_stale_after_runtime_interruption"}


async def _execute_pilot(
    db: Any,
    user_id: str,
    run_id: str,
    limit: int,
    selection_mode: str = "sample",
) -> None:
    await db[RUNS].update_one(
        {"user_id": user_id, "run_id": run_id},
        {"$set": {"status": "running", "started_at": _now()}},
    )
    client: AsyncOpenAI | None = None
    try:
        taxonomy_version, taxonomy = await _get_google_taxonomy()
        by_id, by_path = _taxonomy_maps(taxonomy)
        products = await db[PRODUCTS].find(
            {"user_id": user_id, "archived": {"$ne": True}},
        ).sort([("details_loaded", -1), ("updated_at", -1), ("name", 1)]).limit(MAX_PRODUCTS_SCANNED).to_list(length=MAX_PRODUCTS_SCANNED)
        coverage: dict[str, int] | None = None
        if selection_mode == "next_unseen":
            seen_values = await db[CLASSIFICATIONS].distinct(
                "mezan_product_id",
                {"user_id": user_id},
            )
            seen_ids = {str(value) for value in seen_values if value not in (None, "")}
            active_ids = {
                str(row.get("mezan_product_id") or row.get("id") or "")
                for row in products
            }
            seen_before = len(active_ids.intersection(seen_ids))
            selected = _select_unseen_products(products, seen_ids, limit)
            coverage = {
                "total_products": len(products),
                "seen_before": seen_before,
                "selected_now": len(selected),
                "seen_after": min(len(products), seen_before + len(selected)),
                "remaining_after": max(0, len(products) - seen_before - len(selected)),
            }
            await db[RUNS].update_one(
                {"user_id": user_id, "run_id": run_id},
                {"$set": {"coverage": coverage}},
            )
        else:
            selected = _select_pilot_products(products, limit)

        if not selected:
            if selection_mode == "next_unseen":
                await db[RUNS].update_one(
                    {"user_id": user_id, "run_id": run_id},
                    {"$set": {
                        "status": "completed",
                        "finished_at": _now(),
                        "taxonomy_version": taxonomy_version,
                        "counters": _run_counters([], 0),
                        "coverage": coverage,
                        "error": None,
                    }},
                )
                return
            raise RuntimeError("no_products_available_for_pilot")

        evidences: list[dict[str, Any]] = []
        product_by_id: dict[str, dict[str, Any]] = {}
        missing_records: list[dict[str, Any]] = []
        for product in selected:
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

        client = _openai_client()
        search_terms_by_product: dict[str, list[str]] = {}
        term_generation_errors: list[str] = []
        evidence_chunks = [
            evidences[start:start + SEARCH_TERMS_CHUNK_SIZE]
            for start in range(0, len(evidences), SEARCH_TERMS_CHUNK_SIZE)
        ]

        async def generate_search_terms(
            evidence_chunk: list[dict[str, Any]],
        ) -> tuple[dict[str, list[str]], str | None]:
            try:
                return await _ai_search_terms(client, evidence_chunk), None
            except Exception as exc:  # per-chunk fallback keeps the rollout progressing
                return {}, type(exc).__name__

        term_chunk_results = await _gather_bounded(
            evidence_chunks,
            generate_search_terms,
            concurrency=SEARCH_TERMS_CONCURRENCY,
        )
        for chunk_terms, chunk_error in term_chunk_results:
            search_terms_by_product.update(chunk_terms)
            if chunk_error:
                term_generation_errors.append(chunk_error)
        term_generation_error = ",".join(sorted(set(term_generation_errors))) or None

        classification_inputs: list[dict[str, Any]] = []
        metadata_by_product: dict[str, dict[str, Any]] = {}
        for evidence in evidences:
            product_id = evidence["product_id"]
            current_id = _resolve_current_id(str(evidence.get("current_google_category") or ""), by_id, by_path)
            ai_terms = search_terms_by_product.get(product_id) or []
            candidates = _candidate_rows(evidence, ai_terms, taxonomy, current_id)
            metadata_by_product[product_id] = {
                "evidence": evidence,
                "current_id": current_id,
                "candidates": candidates,
                "input_revision": _input_revision(evidence),
                "limited_evidence": _evidence_limited(evidence),
                "term_source": "openai" if ai_terms else "deterministic_fallback",
            }
            classification_inputs.append({
                "product_id": product_id,
                "facts": evidence,
                "candidate_categories": [
                    {"id": str(row.get("id")), "name": row.get("name"), "path": row.get("path")}
                    for row in candidates
                ],
            })

        records: list[dict[str, Any]] = [*missing_records]
        classification_chunks = [
            classification_inputs[start:start + CLASSIFICATION_CHUNK_SIZE]
            for start in range(0, len(classification_inputs), CLASSIFICATION_CHUNK_SIZE)
        ]

        async def classify_chunk(
            chunk: list[dict[str, Any]],
        ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], Exception | None]:
            try:
                return chunk, await _ai_classify_chunk(client, chunk), None
            except (APITimeoutError, Exception) as exc:  # per-chunk partial failure isolation
                return chunk, {}, exc

        classification_chunk_results = await _gather_bounded(
            classification_chunks,
            classify_chunk,
            concurrency=CLASSIFICATION_CONCURRENCY,
        )
        for chunk, ai_results, chunk_error in classification_chunk_results:
            if chunk_error is not None:
                for item in chunk:
                    product_id = item["product_id"]
                    meta = metadata_by_product[product_id]
                    evidence = meta["evidence"]
                    records.append({
                        "id": uuid.uuid4().hex,
                        "user_id": user_id,
                        "run_id": run_id,
                        "mezan_product_id": product_id,
                        "salla_product_id": evidence.get("salla_product_id"),
                        "product_name": evidence.get("name"),
                        "main_image": product_by_id[product_id].get("main_image"),
                        "classification_input_revision": meta["input_revision"],
                        "classification_source": "openai_pilot",
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
                records.append({
                    "id": uuid.uuid4().hex,
                    "user_id": user_id,
                    "run_id": run_id,
                    "mezan_product_id": product_id,
                    "salla_product_id": evidence.get("salla_product_id"),
                    "product_name": evidence.get("name"),
                    "main_image": product_by_id[product_id].get("main_image"),
                    "classification_input_revision": meta["input_revision"],
                    "classification_source": "openai_pilot",
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

        if records:
            await db[CLASSIFICATIONS].insert_many(records, ordered=False)

        counters = _run_counters(records, len(selected))
        status = (
            "completed"
            if counters["ai_failed"] == 0 and counters["visual_failed"] == 0
            else "completed_with_errors"
        )
        await db[RUNS].update_one(
            {"user_id": user_id, "run_id": run_id},
            {"$set": {
                "status": status,
                "finished_at": _now(),
                "taxonomy_version": taxonomy_version,
                "model": _model(),
                "counters": counters,
                "term_generation_error": term_generation_error,
                "error": None,
            }},
        )
    except Exception as exc:
        await db[RUNS].update_one(
            {"user_id": user_id, "run_id": run_id},
            {"$set": {"status": "failed", "finished_at": _now(), "error": f"{type(exc).__name__}: {str(exc)[:300]}"}},
        )
    finally:
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


def make_product_google_taxonomy_ai_pilot_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(
        prefix="/ai-store-operations/product-intelligence/google-taxonomy",
        tags=["AI Product Manager - Google Taxonomy Pilot"],
    )

    @router.post("/pilot")
    async def start_pilot(
        background_tasks: BackgroundTasks,
        payload: PilotStartIn = Body(default=PilotStartIn()),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        await _ensure_indexes(db)
        user_id = str(user["id"])
        provider = openai_runtime_status()
        if not provider.get("connected"):
            raise HTTPException(status_code=503, detail={"code": "openai_not_configured", "message": "OpenAI غير مهيأ في بيئة الإنتاج."})
        active = await db[RUNS].find_one(
            {"user_id": user_id, "status": {"$in": ["queued", "running"]}},
            {"_id": 0}, sort=[("created_at", -1)],
        )
        if active:
            active = await _recover_stale_run(db, active)
            if active.get("status") in {"queued", "running"}:
                return {**(await _run_payload(db, user_id, active)), "reused": True}

        run_id = uuid.uuid4().hex
        now = _now()
        run = {
            "run_id": run_id,
            "user_id": user_id,
            "status": "queued",
            "requested_limit": payload.limit,
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "model": _model(),
            "taxonomy_version": None,
            "counters": {
                "selected": 0, "analyzed": 0, "no_change": 0, "high_confidence": 0,
                "review_required": 0, "low_confidence": 0, "ai_failed": 0,
                "missing_data": 0, "visual_checked": 0, "visual_failed": 0,
                "applied": 0,
            },
            "mode": "proposal_only_pilot" if payload.selection_mode == "sample" else "proposal_only_next_unseen",
            "selection_mode": payload.selection_mode,
            "writes_to_salla": False,
            "error": None,
        }
        await db[RUNS].insert_one(run)
        background_tasks.add_task(
            _execute_pilot,
            db,
            user_id,
            run_id,
            payload.limit,
            payload.selection_mode,
        )
        return await _run_payload(db, user_id, run)

    @router.get("/pilot/latest")
    async def latest_pilot(user: dict = Depends(current_user)) -> dict[str, Any]:
        await _ensure_indexes(db)
        user_id = str(user["id"])
        run = await db[RUNS].find_one({"user_id": user_id}, {"_id": 0}, sort=[("created_at", -1)])
        if not run:
            return {"ok": True, "run": None, "items": [], "writes_to_salla": False, "auto_apply_enabled": False, "apply_confirmation": APPLY_CONFIRMATION}
        run = await _recover_stale_run(db, run)
        return await _run_payload(db, user_id, run)

    @router.get("/pilot/{run_id}")
    async def get_pilot(run_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        await _ensure_indexes(db)
        user_id = str(user["id"])
        run = await db[RUNS].find_one({"user_id": user_id, "run_id": run_id}, {"_id": 0})
        if not run:
            raise HTTPException(status_code=404, detail={"code": "taxonomy_pilot_not_found"})
        run = await _recover_stale_run(db, run)
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
