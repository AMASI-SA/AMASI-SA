"""Arabic/Saudi-market calibration guards for the Google taxonomy AI pilot.

This layer is intentionally deterministic around high-confidence decisions. It
adjusts candidate retrieval, caps confidence for explicit semantic conflicts,
and may use the public product image as a second opinion only for unresolved
low-confidence products. It never writes products and never calls Salla.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import product_google_taxonomy_ai_pilot as pilot


CAR_WORDS = {"سياره", "سيارات", "للسياره", "للسيارات"}
PENDANT_WORDS = {"تعليقه", "دلايه", "دلايات", "تعليقات"}
NECKLACE_WORDS = {"سلسال", "سلاسل", "قلاده", "قلادات"}
HAIR_WORDS = {"شعر", "الشعر", "للشعر"}
BROOCH_WORDS = {"بروش", "بروشات"}
DAQLA_WORDS = {"دقله", "دقلة", "الدقله", "الدقلة"}
BRACELET_WORDS = {"اسواره", "اسوره", "اساور", "سوار"}
WRISTBAND_CUES = {
    "معصم", "المعصم", "سيليكون", "قماش", "رياضي", "رياضيه", "فعاليات",
    "دخول", "تعريف", "مستشفى", "مهرجان", "تذاكر",
}
PHONE_CASE_WORDS = {"كفر", "جراب", "حافظه", "حافظة"}
DOLL_WORDS = {"دميه", "دمية", "دمى", "لعبه", "لعبة"}
SCHOOL_PINAFORE_WORDS = {"مريول", "المريول", "مراييل", "المراييل"}
SCHOOL_WORDS = {
    "مدرسي", "مدرسيه", "مدرسية", "المدرسي", "المدرسيه", "المدرسية",
    "مدرسه", "مدرسة", "المدرسه", "المدرسة", "طالبات",
}
ROSARY_WORDS = {
    "سبحه", "سبحة", "السبحه", "السبحة",
    "مسبحه", "مسبحة", "المسبحه", "المسبحة",
}
WALL_ART_WORDS = {"لوحه", "لوحة", "لوحات"}
WALL_ART_CUES = {
    "جداري", "جداريه", "جدارية", "حائطي", "حائطيه", "حائطية",
    "ديكور", "مطبوع", "مطبوعات", "تصميم", "عصري", "ثلاثي", "ثلاثيه", "ثلاثية",
}
BLANK_CANVAS_CUES = {
    "فارغ", "فارغه", "فارغة", "للرسم", "رسم", "كانفس", "كانفاس", "قماش",
}
DRESS_WORDS = {"فستان", "فساتين"}
INFANT_DRESS_CUES = {
    "رضيع", "رضيعه", "رضيعة", "رضع", "الرضع", "مولود", "مواليد",
    "بيبي", "baby", "toddler", "حديثي", "حديثه", "حديثة", "ولاده", "ولادة",
}

VISION_CONFIDENCE_TRIGGER = 69
VISION_MAX_OUTPUT_TOKENS = 700

VISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "category_id": {"type": "string"},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "reason": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
    },
    "required": ["category_id", "confidence", "reason", "evidence"],
}


def _name_tokens(evidence: dict[str, Any]) -> set[str]:
    return set(pilot._normalize_ar(evidence.get("name")).split())


def _has_any(tokens: set[str], words: set[str]) -> bool:
    return bool(tokens.intersection({pilot._normalize_ar(word) for word in words}))


def _is_car_hanging(evidence: dict[str, Any]) -> bool:
    tokens = _name_tokens(evidence)
    return _has_any(tokens, CAR_WORDS) and _has_any(tokens, PENDANT_WORDS)


def _is_full_necklace(evidence: dict[str, Any]) -> bool:
    tokens = _name_tokens(evidence)
    return _has_any(tokens, NECKLACE_WORDS) and not _has_any(tokens, PENDANT_WORDS)


def _is_hair_brooch(evidence: dict[str, Any]) -> bool:
    tokens = _name_tokens(evidence)
    return _has_any(tokens, HAIR_WORDS) and _has_any(tokens, BROOCH_WORDS)


def _is_daqla(evidence: dict[str, Any]) -> bool:
    return _has_any(_name_tokens(evidence), DAQLA_WORDS)


def _is_jewelry_bracelet(evidence: dict[str, Any]) -> bool:
    tokens = _name_tokens(evidence)
    return _has_any(tokens, BRACELET_WORDS) and not _has_any(tokens, WRISTBAND_CUES)


def _is_phone_case(evidence: dict[str, Any]) -> bool:
    tokens = _name_tokens(evidence)
    return _has_any(tokens, PHONE_CASE_WORDS) and not _has_any(tokens, CAR_WORDS)


def _is_doll(evidence: dict[str, Any]) -> bool:
    return _has_any(_name_tokens(evidence), DOLL_WORDS)


def _is_school_pinafore(evidence: dict[str, Any]) -> bool:
    tokens = _name_tokens(evidence)
    return _has_any(tokens, SCHOOL_PINAFORE_WORDS) and _has_any(tokens, SCHOOL_WORDS)


def _is_rosary(evidence: dict[str, Any]) -> bool:
    return _has_any(_name_tokens(evidence), ROSARY_WORDS)


def _is_finished_wall_art(evidence: dict[str, Any]) -> bool:
    tokens = _name_tokens(evidence)
    return (
        _has_any(tokens, WALL_ART_WORDS)
        and _has_any(tokens, WALL_ART_CUES)
        and not _has_any(tokens, BLANK_CANVAS_CUES)
    )


def _is_non_infant_dress(evidence: dict[str, Any]) -> bool:
    tokens = _name_tokens(evidence)
    return _has_any(tokens, DRESS_WORDS) and not _has_any(tokens, INFANT_DRESS_CUES)


def _is_ambiguous_bundle(evidence: dict[str, Any]) -> bool:
    name = pilot._normalize_ar(evidence.get("name"))
    return "طقم" in name.split() and ("قطع" in name.split() or bool(re.search(r"\b\d+\b", name)))


def _public_image_url(product: dict[str, Any]) -> str:
    def extract(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            for key in ("url", "original", "original_url", "image", "src"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
        return ""

    candidate = extract(product.get("main_image"))
    if not candidate:
        for row in (product.get("images") or [])[:3]:
            candidate = extract(row)
            if candidate:
                break
    if candidate.startswith("https://") or candidate.startswith("http://"):
        return candidate[:1600]
    return ""


def calibrated_product_evidence(product: dict[str, Any]) -> dict[str, Any]:
    evidence = pilot._product_evidence_original(product)
    image_url = _public_image_url(product)
    if image_url:
        evidence["main_image_url"] = image_url
    return evidence


def calibrated_input_revision(evidence: dict[str, Any]) -> str:
    relevant = {
        key: evidence.get(key)
        for key in (
            "name", "description", "short_description", "salla_categories", "options",
            "product_type", "brand", "sku", "gtin", "mpn", "current_google_category",
            "main_image_url",
        )
    }
    payload = json.dumps(relevant, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def contextual_search_terms(evidence: dict[str, Any]) -> list[str]:
    """Return strong contextual terms that disambiguate common Arabic products."""
    terms: list[str] = []
    if _is_car_hanging(evidence):
        terms.extend([
            "اكسسوارات داخلية للسيارات",
            "زينة سيارات",
            "تعليقات وزينة داخل السيارة",
        ])
    if _is_full_necklace(evidence):
        terms.extend(["قلادات", "سلاسل رقبة", "قلادة كاملة"])
    if _is_hair_brooch(evidence):
        terms.extend(["اكسسوارات شعر", "مشابك شعر", "دبابيس شعر"])
    if _is_daqla(evidence):
        terms.extend(["ملابس تقليدية", "ملابس أطفال تقليدية", "ملابس مناسبات تقليدية"])
    if _is_jewelry_bracelet(evidence):
        terms.extend(["حلي اساور", "اساور مجوهرات", "اساور زينة"])
    if _is_phone_case(evidence):
        terms.extend(["حافظات الهواتف المحمولة", "جرابات هواتف", "حافظات اجهزة محمولة"])
    if _is_doll(evidence):
        terms.extend(["دمى محشوة", "العاب دمى", "العاب محشوة"])
    if _is_school_pinafore(evidence):
        terms.extend(["ملابس مدرسية", "زي مدرسي للبنات", "مريول مدرسي"])
    if _is_rosary(evidence):
        terms.extend(["سبح ومسابح", "سبحة خرز", "مسبحة"])
    if _is_finished_wall_art(evidence):
        terms.extend(["لوحات جدارية", "أعمال فنية ومطبوعات", "ديكور جداري"])
    if _is_non_infant_dress(evidence):
        terms.extend(["فساتين", "ملابس وفساتين"])
    return terms


def _path_incompatible(evidence: dict[str, Any], path: Any) -> bool:
    normalized = pilot._normalize_ar(path)
    if not normalized:
        return False
    if _is_car_hanging(evidence):
        if any(term in normalized for term in ("حلي", "قلادات", "دلايات", "مجوهرات")):
            return True
    if _is_full_necklace(evidence):
        if "قلادات ودلايات" in normalized or "دلايات" in normalized:
            return True
    if _is_hair_brooch(evidence):
        if "بروشات ودبابيس ملابس" in normalized or "دبابيس ملابس" in normalized:
            return True
    if _is_daqla(evidence):
        if "التعميد" in normalized or "المناوله" in normalized:
            return True
    if _is_jewelry_bracelet(evidence):
        if "اساور المعصم" in normalized or (
            "اكسسوارات الملابس" in normalized and "اساور" in normalized
        ):
            return True
    if _is_school_pinafore(evidence):
        if "فساتين" in normalized and (
            "الرضع" in normalized or "الاطفال الصغار" in normalized
        ):
            return True
    if _is_rosary(evidence):
        if "مشجعي كره القدم" in normalized:
            return True
    if _is_finished_wall_art(evidence):
        if "الرسم" in normalized and any(
            term in normalized for term in ("خامات", "مستلزمات", "لوحات رسم", "كانفس", "كانفاس")
        ):
            return True
    if _is_non_infant_dress(evidence):
        if "فساتين" in normalized and (
            "الرضع" in normalized or "الاطفال الصغار" in normalized
        ):
            return True
    return False


def calibrated_candidate_rows(
    evidence: dict[str, Any],
    ai_terms: list[str],
    taxonomy: list[dict[str, Any]],
    current_id: str | None,
) -> list[dict[str, Any]]:
    """Run the normal retriever, then remove contextually impossible branches."""
    contextual = contextual_search_terms(evidence)
    rows = pilot._candidate_rows_original(
        evidence,
        [*contextual, *ai_terms],
        taxonomy,
        current_id,
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        row_id = str(row.get("id") or "")
        if current_id and row_id == current_id:
            result.append(row)
            continue
        if _path_incompatible(evidence, row.get("path")):
            continue
        result.append(row)
    return result[: pilot.MAX_CANDIDATES]


def confidence_cap(evidence: dict[str, Any], chosen_path: Any) -> tuple[int | None, str | None]:
    """Return a confidence ceiling when explicit wording conflicts or stays ambiguous."""
    if _is_ambiguous_bundle(evidence):
        return 79, "المنتج طقم متعدد القطع؛ يحتاج مراجعة بشرية لتحديد المنتج الرئيسي في التصنيف."
    if not chosen_path:
        return None, None
    if _path_incompatible(evidence, chosen_path):
        if _is_car_hanging(evidence):
            return 49, "سياق السيارة يتعارض مع تصنيف الحلي/الدلايات."
        if _is_full_necklace(evidence):
            return 69, "اسم المنتج يدل على سلسال/قلادة كاملة وليس دلاية منفصلة."
        if _is_hair_brooch(evidence):
            return 69, "ذكر الشعر يجعل تصنيف بروشات الملابس غير آمن للاعتماد التلقائي."
        if _is_daqla(evidence):
            return 49, "الدقلة المحلية لا تدعم تصنيف أزياء التعميد/المناولة."
        if _is_jewelry_bracelet(evidence):
            return 49, "اسم المنتج يدل على حُلي/سوار زينة وليس Wristband من إكسسوارات الملابس."
        if _is_school_pinafore(evidence):
            return 49, "المريول المدرسي زي لطالبات المدرسة وليس فستاناً للرضع أو الأطفال الصغار."
        if _is_rosary(evidence):
            return 49, "شعار النادي لا يغيّر نوع المنتج؛ السبحة ليست إكسسواراً لمشجعي كرة القدم."
        if _is_finished_wall_art(evidence):
            return 49, "اللوحة الجدارية الجاهزة عمل فني للعرض وليست خامة أو لوحة فارغة للرسم."
        if _is_non_infant_dress(evidence):
            return 49, "الفستان غير الموصوف للرضع لا ينتمي إلى فساتين الرضع والأطفال الصغار."
    return None, None


def _apply_cap(result: dict[str, Any], evidence: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
    chosen_id = str(result.get("category_id") or "")
    chosen = next((row for row in candidates if str(row.get("id") or "") == chosen_id), None)
    cap, note = confidence_cap(evidence, (chosen or {}).get("path"))
    if cap is None:
        return
    try:
        current = int(result.get("confidence") or 0)
    except (TypeError, ValueError):
        current = 0
    result["confidence"] = min(current, cap)
    if note:
        reason = pilot._text(result.get("reason"))
        result["reason"] = f"{note} {reason}"[: pilot.MAX_REASON_CHARS]


def _needs_vision(result: dict[str, Any], evidence: dict[str, Any], candidates: list[dict[str, Any]]) -> bool:
    if not evidence.get("main_image_url") or not candidates:
        return False
    chosen_id = str(result.get("category_id") or "")
    try:
        confidence = int(result.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    return not chosen_id or confidence <= VISION_CONFIDENCE_TRIGGER


async def _vision_rescue_one(
    client: Any,
    *,
    evidence: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    image_url = str(evidence.get("main_image_url") or "").strip()
    if not image_url:
        return None
    safe_facts = {
        key: evidence.get(key)
        for key in (
            "name", "description", "short_description", "salla_categories",
            "options", "product_type", "brand",
        )
    }
    candidate_payload = [
        {"id": str(row.get("id") or ""), "name": row.get("name"), "path": row.get("path")}
        for row in candidates
    ]
    response = await client.responses.create(
        model=pilot._model(),
        instructions=(
            "أنت طبقة تحقق بصري لتصنيف Google Product Category داخل Mezan. استخدم الصورة فقط "
            "لحسم الغموض في حقائق المنتج النصية. اختر category_id حرفياً من candidate_categories. "
            "إذا لم تحسم الصورة نوع المنتج أو كان المنتج طقماً متعدد الأنواع، أعد category_id فارغاً "
            "وثقة أقل من 70. لا تستنتج ماركة أو مادة أو استخداماً غير ظاهر."
        ),
        input=[{
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": json.dumps(
                        {"facts": safe_facts, "candidate_categories": candidate_payload},
                        ensure_ascii=False,
                    ),
                },
                {"type": "input_image", "image_url": image_url},
            ],
        }],
        max_output_tokens=VISION_MAX_OUTPUT_TOKENS,
        text={
            "format": {
                "type": "json_schema",
                "name": "google_taxonomy_vision_rescue",
                "strict": True,
                "schema": VISION_SCHEMA,
            }
        },
    )
    payload = json.loads(response.output_text)
    chosen_id = str(payload.get("category_id") or "")
    candidate_ids = {str(row.get("id") or "") for row in candidates}
    if chosen_id and chosen_id not in candidate_ids:
        return None
    payload["category_id"] = chosen_id
    try:
        payload["confidence"] = max(0, min(100, int(payload.get("confidence") or 0)))
    except (TypeError, ValueError):
        payload["confidence"] = 0
    return payload


async def calibrated_ai_classify_chunk(client: Any, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    results = await pilot._ai_classify_chunk_original(client, rows)
    by_product = {str(row.get("product_id") or ""): row for row in rows}
    for product_id, result in results.items():
        source = by_product.get(str(product_id)) or {}
        evidence = source.get("facts") if isinstance(source.get("facts"), dict) else {}
        candidates = source.get("candidate_categories") if isinstance(source.get("candidate_categories"), list) else []
        _apply_cap(result, evidence, candidates)

        if _needs_vision(result, evidence, candidates):
            try:
                rescued = await _vision_rescue_one(client, evidence=evidence, candidates=candidates)
            except Exception:
                rescued = None
            if rescued:
                _apply_cap(rescued, evidence, candidates)
                current_confidence = int(result.get("confidence") or 0)
                rescued_confidence = int(rescued.get("confidence") or 0)
                if rescued_confidence > current_confidence or not str(result.get("category_id") or ""):
                    reason = pilot._text(rescued.get("reason"))
                    result.update(rescued)
                    result["reason"] = f"تحقق بصري: {reason}"[: pilot.MAX_REASON_CHARS]
                    evidence_rows = [pilot._text(value)[:220] for value in (rescued.get("evidence") or [])]
                    if "الصورة دعمت التصنيف" not in evidence_rows:
                        evidence_rows.append("الصورة دعمت التصنيف")
                    result["evidence"] = evidence_rows[: pilot.MAX_EVIDENCE_ITEMS]
    return results


def make_product_google_taxonomy_ai_pilot_router(db: Any, current_user: Any):
    """Install calibration once, then return the original governed pilot router."""
    if not hasattr(pilot, "_product_evidence_original"):
        pilot._product_evidence_original = pilot._product_evidence
        pilot._product_evidence = calibrated_product_evidence
    if not hasattr(pilot, "_input_revision_original"):
        pilot._input_revision_original = pilot._input_revision
        pilot._input_revision = calibrated_input_revision
    if not hasattr(pilot, "_candidate_rows_original"):
        pilot._candidate_rows_original = pilot._candidate_rows
        pilot._candidate_rows = calibrated_candidate_rows
    if not hasattr(pilot, "_ai_classify_chunk_original"):
        pilot._ai_classify_chunk_original = pilot._ai_classify_chunk
        pilot._ai_classify_chunk = calibrated_ai_classify_chunk
    return pilot.make_product_google_taxonomy_ai_pilot_router(db, current_user)
