"""Arabic/Saudi-market calibration guards for the Google taxonomy AI pilot.

This layer is intentionally deterministic.  It adjusts candidate retrieval and
caps model confidence when explicit product wording conflicts with the chosen
Google taxonomy path.  It never writes products and never calls Salla.
"""
from __future__ import annotations

from typing import Any

import product_google_taxonomy_ai_pilot as pilot


CAR_WORDS = {"سياره", "سيارات", "للسياره", "للسيارات"}
PENDANT_WORDS = {"تعليقه", "دلايه", "دلايات", "تعليقات"}
NECKLACE_WORDS = {"سلسال", "سلاسل", "قلاده", "قلادات"}
HAIR_WORDS = {"شعر", "الشعر", "للشعر"}
BROOCH_WORDS = {"بروش", "بروشات"}
DAQLA_WORDS = {"دقله", "دقلة", "الدقله", "الدقلة"}


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
    return terms


def _path_incompatible(evidence: dict[str, Any], path: Any) -> bool:
    normalized = pilot._normalize_ar(path)
    if not normalized:
        return False
    if _is_car_hanging(evidence):
        if any(term in normalized for term in ("حلي", "قلادات", "دلايات", "مجوهرات")):
            return True
    if _is_full_necklace(evidence):
        # Google ID 192 is the charms/pendants branch in the Arabic taxonomy;
        # a product explicitly sold as a full necklace should not be auto-routed
        # there unless the product itself says pendant/charm.
        if "قلادات ودلايات" in normalized or "دلايات" in normalized:
            return True
    if _is_hair_brooch(evidence):
        if "بروشات ودبابيس ملابس" in normalized or "دبابيس ملابس" in normalized:
            return True
    if _is_daqla(evidence):
        if "التعميد" in normalized or "المناوله" in normalized:
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
    # Existing provider/current category is never silently hidden from review.
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
    """Return a confidence ceiling when an explicit semantic conflict remains."""
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
    return None, None


async def calibrated_ai_classify_chunk(client: Any, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    results = await pilot._ai_classify_chunk_original(client, rows)
    by_product = {str(row.get("product_id") or ""): row for row in rows}
    for product_id, result in results.items():
        source = by_product.get(str(product_id)) or {}
        evidence = source.get("facts") if isinstance(source.get("facts"), dict) else {}
        chosen_id = str(result.get("category_id") or "")
        chosen = next(
            (
                row for row in (source.get("candidate_categories") or [])
                if str(row.get("id") or "") == chosen_id
            ),
            None,
        )
        cap, note = confidence_cap(evidence, (chosen or {}).get("path"))
        if cap is not None:
            try:
                current = int(result.get("confidence") or 0)
            except (TypeError, ValueError):
                current = 0
            result["confidence"] = min(current, cap)
            if note:
                reason = pilot._text(result.get("reason"))
                result["reason"] = f"{note} {reason}"[: pilot.MAX_REASON_CHARS]
    return results


def make_product_google_taxonomy_ai_pilot_router(db: Any, current_user: Any):
    """Install calibration once, then return the original governed pilot router."""
    if not hasattr(pilot, "_candidate_rows_original"):
        pilot._candidate_rows_original = pilot._candidate_rows
        pilot._candidate_rows = calibrated_candidate_rows
    if not hasattr(pilot, "_ai_classify_chunk_original"):
        pilot._ai_classify_chunk_original = pilot._ai_classify_chunk
        pilot._ai_classify_chunk = calibrated_ai_classify_chunk
    return pilot.make_product_google_taxonomy_ai_pilot_router(db, current_user)