import asyncio

import product_google_taxonomy_ai_calibration as calibration
import product_google_taxonomy_ai_pilot as pilot


def _evidence(name):
    return {"name": name, "description": "", "salla_categories": [], "options": []}


def test_car_hanging_adds_vehicle_context_and_rejects_jewelry_path():
    evidence = _evidence("تعليقة سيارة بالاسم مع تاج")
    terms = calibration.contextual_search_terms(evidence)
    assert any("سيارات" in term or "السياره" in pilot._normalize_ar(term) for term in terms)
    cap, note = calibration.confidence_cap(
        evidence,
        "ملابس وإكسسوارات > حلي > قلادات ودلايات",
    )
    assert cap == 49
    assert "السيارة" in note


def test_full_necklace_cannot_auto_approve_charms_and_pendants():
    evidence = _evidence("سلسال أنيق أطفال بالاسم")
    cap, note = calibration.confidence_cap(
        evidence,
        "ملابس وإكسسوارات > حلي > قلادات ودلايات",
    )
    assert cap == 69
    assert "قلادة كاملة" in note or "سلسال" in note


def test_hair_brooch_cannot_auto_approve_clothing_pin_branch():
    evidence = _evidence("بروش على الشعر تصميم حسب الطلب")
    cap, note = calibration.confidence_cap(
        evidence,
        "ملابس وإكسسوارات > حلي > بروشات ودبابيس ملابس",
    )
    assert cap == 69
    assert "الشعر" in note


def test_daqla_cannot_route_to_christening_communion_apparel():
    evidence = _evidence("دقله اطفال تصميم الحرف العربي")
    terms = calibration.contextual_search_terms(evidence)
    assert "ملابس تقليدية" in terms
    cap, note = calibration.confidence_cap(
        evidence,
        "ملابس وإكسسوارات > ملابس > ملابس الاحتفالات > أزياء التعميد والمناولة",
    )
    assert cap == 49
    assert "الدقلة" in note


def test_normal_bracelet_keeps_model_confidence_uncapped():
    cap, note = calibration.confidence_cap(
        _evidence("اسواره تصميم حسب الطلب"),
        "ملابس وإكسسوارات > حلي > أساور المعصم",
    )
    assert cap is None
    assert note is None


def test_ai_result_confidence_is_deterministically_capped(monkeypatch):
    async def fake_original(client, rows):
        return {
            "p1": {
                "product_id": "p1",
                "category_id": "192",
                "confidence": 96,
                "reason": "اختيار النموذج",
                "evidence": ["سلسال"],
            }
        }

    monkeypatch.setattr(pilot, "_ai_classify_chunk_original", fake_original, raising=False)
    rows = [{
        "product_id": "p1",
        "facts": _evidence("سلسال أنيق أطفال بالاسم"),
        "candidate_categories": [{
            "id": "192",
            "name": "قلادات ودلايات",
            "path": "ملابس وإكسسوارات > حلي > قلادات ودلايات",
        }],
    }]
    result = asyncio.run(calibration.calibrated_ai_classify_chunk(object(), rows))["p1"]
    assert result["confidence"] == 69
    assert "سلسال" in result["reason"]
