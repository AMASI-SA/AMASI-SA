"""High-confidence visual contradiction gate for Google taxonomy AI proposals.

This layer does not classify products by image and never writes products or Salla.
It only verifies that a >=90% text-driven proposal is visually consistent with the
public product image before it remains eligible for batch approval.
"""
from __future__ import annotations

import json
from typing import Any

import product_google_taxonomy_ai_calibration as calibration
import product_google_taxonomy_ai_pilot as pilot

HIGH_CONFIDENCE_MIN = 90
VISUAL_REVIEW_CAP = 89
VISUAL_VERIFY_MAX_OUTPUT_TOKENS = 500

VISUAL_VERIFY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["consistent", "conflict", "unclear"],
        },
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "observed_product_type": {"type": "string"},
        "reason": {"type": "string"},
        "evidence": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 4,
        },
    },
    "required": [
        "verdict",
        "confidence",
        "observed_product_type",
        "reason",
        "evidence",
    ],
}


def _candidate_for_result(
    result: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    chosen_id = str(result.get("category_id") or "")
    if not chosen_id:
        return None
    return next(
        (row for row in candidates if str(row.get("id") or "") == chosen_id),
        None,
    )


def _should_verify(
    result: dict[str, Any],
    evidence: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> bool:
    if not evidence.get("main_image_url"):
        return False
    if evidence.get("current_google_category"):
        # Existing categories are already protected by the pilot's review/no-change
        # policy; do not spend image calls on them here.
        return False
    candidate = _candidate_for_result(result, candidates)
    if not candidate:
        return False
    try:
        confidence = int(result.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    return confidence >= HIGH_CONFIDENCE_MIN


async def _visual_consistency_check_one(
    client: Any,
    *,
    evidence: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any] | None:
    image_url = str(evidence.get("main_image_url") or "").strip()
    if not image_url:
        return None

    safe_facts = {
        key: evidence.get(key)
        for key in (
            "name",
            "description",
            "short_description",
            "salla_categories",
            "options",
            "product_type",
            "brand",
        )
    }
    proposed = {
        "id": str(candidate.get("id") or ""),
        "name": candidate.get("name"),
        "path": candidate.get("path"),
    }

    response = await client.responses.create(
        model=pilot._model(),
        instructions=(
            "أنت حاجز تحقق بصري قبل الاعتماد داخل Mezan. لا تعيد تصنيف المنتج ولا تقترح "
            "فئة بديلة. قارن فقط العنصر الرئيسي المعروض للبيع في الصورة مع Google Product "
            "Category المقترحة. تجاهل الخلفية والموديل والديكور والعناصر الثانوية. verdict=consistent "
            "إذا كانت الصورة تدعم نوع المنتج المقترح بوضوح، verdict=conflict إذا أظهرت نوع منتج "
            "مختلفاً بوضوح، وverdict=unclear إذا لم تكفِ الصورة للحسم. لا تستنتج مادة أو ماركة "
            "أو استخداماً غير ظاهر."
        ),
        input=[{
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": json.dumps(
                        {"facts": safe_facts, "proposed_category": proposed},
                        ensure_ascii=False,
                    ),
                },
                {"type": "input_image", "image_url": image_url},
            ],
        }],
        max_output_tokens=VISUAL_VERIFY_MAX_OUTPUT_TOKENS,
        text={
            "format": {
                "type": "json_schema",
                "name": "google_taxonomy_high_confidence_visual_gate",
                "strict": True,
                "schema": VISUAL_VERIFY_SCHEMA,
            }
        },
    )

    payload = json.loads(response.output_text)
    verdict = str(payload.get("verdict") or "").strip()
    if verdict not in {"consistent", "conflict", "unclear"}:
        return None
    payload["verdict"] = verdict
    try:
        payload["confidence"] = max(
            0,
            min(100, int(payload.get("confidence") or 0)),
        )
    except (TypeError, ValueError):
        payload["confidence"] = 0
    return payload


def _apply_visual_review_gate(
    result: dict[str, Any],
    check: dict[str, Any],
) -> None:
    verdict = str(check.get("verdict") or "")
    if verdict == "consistent":
        evidence_rows = [
            pilot._text(value)[:220]
            for value in (result.get("evidence") or [])
            if pilot._text(value)
        ]
        if "الصورة متسقة مع التصنيف المقترح" not in evidence_rows:
            evidence_rows.append("الصورة متسقة مع التصنيف المقترح")
        result["evidence"] = evidence_rows[: pilot.MAX_EVIDENCE_ITEMS]
        return

    try:
        current = int(result.get("confidence") or 0)
    except (TypeError, ValueError):
        current = 0
    result["confidence"] = min(current, VISUAL_REVIEW_CAP)

    visual_reason = pilot._text(check.get("reason"))
    observed = pilot._text(check.get("observed_product_type"))
    prefix = (
        "مراجعة بصرية مطلوبة: الصورة تتعارض مع التصنيف المقترح."
        if verdict == "conflict"
        else "مراجعة بصرية مطلوبة: الصورة لا تحسم التصنيف المقترح."
    )
    details = " ".join(value for value in (observed, visual_reason) if value)
    original_reason = pilot._text(result.get("reason"))
    result["reason"] = " ".join(
        value for value in (prefix, details, original_reason) if value
    )[: pilot.MAX_REASON_CHARS]

    evidence_rows = [
        pilot._text(value)[:220]
        for value in (result.get("evidence") or [])
        if pilot._text(value)
    ]
    for value in check.get("evidence") or []:
        text = pilot._text(value)[:220]
        if text and text not in evidence_rows:
            evidence_rows.append(text)
    marker = (
        "الصورة خالفت التصنيف النصي"
        if verdict == "conflict"
        else "الصورة لم تحسم التصنيف النصي"
    )
    if marker not in evidence_rows:
        evidence_rows.append(marker)
    result["evidence"] = evidence_rows[: pilot.MAX_EVIDENCE_ITEMS]


async def calibrated_ai_classify_chunk_with_visual_gate(
    client: Any,
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    results = await calibration._calibrated_ai_classify_chunk_original(
        client,
        rows,
    )
    by_product = {
        str(row.get("product_id") or ""): row
        for row in rows
    }

    for product_id, result in results.items():
        source = by_product.get(str(product_id)) or {}
        evidence = (
            source.get("facts")
            if isinstance(source.get("facts"), dict)
            else {}
        )
        candidates = (
            source.get("candidate_categories")
            if isinstance(source.get("candidate_categories"), list)
            else []
        )
        if not _should_verify(result, evidence, candidates):
            continue

        candidate = _candidate_for_result(result, candidates)
        if not candidate:
            continue
        try:
            check = await _visual_consistency_check_one(
                client,
                evidence=evidence,
                candidate=candidate,
            )
        except Exception:
            # A visual verification outage must never turn a proposal into an
            # automatic approval. Fail closed to the human-review band.
            check = {
                "verdict": "unclear",
                "confidence": 0,
                "observed_product_type": "",
                "reason": "تعذر إكمال التحقق البصري لهذه النتيجة.",
                "evidence": [],
            }
        if check:
            _apply_visual_review_gate(result, check)

    return results


def make_product_google_taxonomy_ai_pilot_router(db: Any, current_user: Any):
    """Install the visual gate above the calibrated governed pilot router."""
    if not hasattr(calibration, "_calibrated_ai_classify_chunk_original"):
        calibration._calibrated_ai_classify_chunk_original = (
            calibration.calibrated_ai_classify_chunk
        )
        calibration.calibrated_ai_classify_chunk = (
            calibrated_ai_classify_chunk_with_visual_gate
        )
    return calibration.make_product_google_taxonomy_ai_pilot_router(
        db,
        current_user,
    )
