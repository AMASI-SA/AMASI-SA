import asyncio
import json

import product_google_taxonomy_ai_calibration as calibration
import product_google_taxonomy_ai_pilot as pilot
import product_google_taxonomy_ai_visual_gate as visual_gate


def _evidence(name, *, image_url=""):
    row = {"name": name, "description": "", "salla_categories": [], "options": []}
    if image_url:
        row["main_image_url"] = image_url
    return row


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


def test_jewelry_bracelet_rejects_clothing_wristband_branch():
    evidence = _evidence("اسوارة بلمعة زركون")
    terms = calibration.contextual_search_terms(evidence)
    assert any("حلي" in term for term in terms)
    cap, note = calibration.confidence_cap(
        evidence,
        "ملابس وإكسسوارات > إكسسوارات الملابس > أساور المعصم",
    )
    assert cap == 49
    assert "Wristband" in note


def test_explicit_silicone_wristband_is_not_forced_to_jewelry():
    evidence = _evidence("سوار معصم سيليكون للفعاليات")
    cap, note = calibration.confidence_cap(
        evidence,
        "ملابس وإكسسوارات > إكسسوارات الملابس > أساور المعصم",
    )
    assert cap is None
    assert note is None
    assert not any("حلي اساور" == pilot._normalize_ar(term) for term in calibration.contextual_search_terms(evidence))


def test_bundle_is_never_auto_approved_from_one_category():
    cap, note = calibration.confidence_cap(
        _evidence("طقم رجالي 6 قطع بالاسم"),
        "ملابس وإكسسوارات > حلي > أساور",
    )
    assert cap == 79
    assert "متعدد القطع" in note


def test_phone_case_and_doll_add_ambiguity_resolving_terms():
    phone_terms = calibration.contextual_search_terms(_evidence("كفر زهور الجوري"))
    doll_terms = calibration.contextual_search_terms(_evidence("دمية ميرومي اللطيفة"))
    assert any("هواتف" in term for term in phone_terms)
    assert any("دمى" in term for term in doll_terms)


def test_product_evidence_exposes_only_public_http_image_url(monkeypatch):
    monkeypatch.setattr(
        pilot,
        "_product_evidence_original",
        lambda product: {"name": product.get("name"), "has_image": True},
        raising=False,
    )
    evidence = calibration.calibrated_product_evidence({
        "name": "كفر هاتف",
        "main_image": "https://cdn.example.com/product.jpg",
    })
    assert evidence["main_image_url"] == "https://cdn.example.com/product.jpg"


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


class _VisionResponse:
    def __init__(self, payload):
        self.output_text = json.dumps(payload, ensure_ascii=False)


class _VisionResponses:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return _VisionResponse(self.payload)


class _VisionClient:
    def __init__(self, payload):
        self.responses = _VisionResponses(payload)


def test_low_confidence_product_can_use_image_as_second_opinion(monkeypatch):
    async def fake_original(client, rows):
        return {
            "p1": {
                "product_id": "p1",
                "category_id": "",
                "confidence": 40,
                "reason": "النص غير كاف",
                "evidence": [],
            }
        }

    monkeypatch.setattr(pilot, "_ai_classify_chunk_original", fake_original, raising=False)
    client = _VisionClient({
        "category_id": "555",
        "confidence": 92,
        "reason": "الصورة تظهر حافظة هاتف بوضوح",
        "evidence": ["شكل الحافظة وفتحة الكاميرا"],
    })
    rows = [{
        "product_id": "p1",
        "facts": _evidence("كفر زهور الجوري", image_url="https://cdn.example.com/case.jpg"),
        "candidate_categories": [
            {"id": "555", "name": "حافظات هاتف", "path": "إلكترونيات > ملحقات > حافظات هاتف"},
            {"id": "999", "name": "إطارات", "path": "مركبات > إطارات"},
        ],
    }]
    result = asyncio.run(calibration.calibrated_ai_classify_chunk(client, rows))["p1"]
    assert result["category_id"] == "555"
    assert result["confidence"] == 92
    assert result["reason"].startswith("تحقق بصري:")
    assert client.responses.calls
    content = client.responses.calls[0]["input"][0]["content"]
    assert any(row.get("type") == "input_image" for row in content)


def test_vision_cannot_escape_candidate_list(monkeypatch):
    async def fake_original(client, rows):
        return {
            "p1": {
                "product_id": "p1",
                "category_id": "",
                "confidence": 30,
                "reason": "غير واضح",
                "evidence": [],
            }
        }

    monkeypatch.setattr(pilot, "_ai_classify_chunk_original", fake_original, raising=False)
    client = _VisionClient({
        "category_id": "outside",
        "confidence": 99,
        "reason": "محاولة خارج المرشحين",
        "evidence": [],
    })
    rows = [{
        "product_id": "p1",
        "facts": _evidence("دمية", image_url="https://cdn.example.com/doll.jpg"),
        "candidate_categories": [{"id": "123", "name": "دمى", "path": "ألعاب > دمى"}],
    }]
    result = asyncio.run(calibration.calibrated_ai_classify_chunk(client, rows))["p1"]
    assert result["category_id"] == ""
    assert result["confidence"] == 30


def test_high_confidence_visual_conflict_forces_human_review(monkeypatch):
    async def fake_calibrated(client, rows):
        return {
            "p1": {
                "product_id": "p1",
                "category_id": "201",
                "confidence": 95,
                "reason": "الاسم يقول ساعة بناتي",
                "evidence": ["ساعة بناتي"],
            }
        }

    monkeypatch.setattr(
        calibration,
        "_calibrated_ai_classify_chunk_original",
        fake_calibrated,
        raising=False,
    )
    client = _VisionClient({
        "verdict": "conflict",
        "confidence": 98,
        "observed_product_type": "قطعة معلقة تشبه قلادة وليست ساعة معصم",
        "reason": "الصورة لا تظهر سوار معصم أو هيكل ساعة يد.",
        "evidence": ["القطعة معلقة بخيط حول الرقبة"],
    })
    rows = [{
        "product_id": "p1",
        "facts": _evidence("ساعة بناتي", image_url="https://cdn.example.com/watch.jpg"),
        "candidate_categories": [{
            "id": "201",
            "name": "ساعات يد",
            "path": "ملابس وإكسسوارات > حلي > ساعات يد",
        }],
    }]
    result = asyncio.run(
        visual_gate.calibrated_ai_classify_chunk_with_visual_gate(client, rows)
    )["p1"]
    assert result["category_id"] == "201"
    assert result["confidence"] == 89
    assert "مراجعة بصرية مطلوبة" in result["reason"]
    assert "الصورة خالفت التصنيف النصي" in result["evidence"]
    assert client.responses.calls


def test_high_confidence_visual_consistency_keeps_auto_approval_band(monkeypatch):
    async def fake_calibrated(client, rows):
        return {
            "p1": {
                "product_id": "p1",
                "category_id": "191",
                "confidence": 95,
                "reason": "اسوارة زينة",
                "evidence": ["اسوارة"],
            }
        }

    monkeypatch.setattr(
        calibration,
        "_calibrated_ai_classify_chunk_original",
        fake_calibrated,
        raising=False,
    )
    client = _VisionClient({
        "verdict": "consistent",
        "confidence": 98,
        "observed_product_type": "اسوارة حلي",
        "reason": "الصورة تظهر اسوارة زينة حول المعصم.",
        "evidence": ["حلقة معدنية مزخرفة"],
    })
    rows = [{
        "product_id": "p1",
        "facts": _evidence("اسوارة بلمعة زركون", image_url="https://cdn.example.com/bracelet.jpg"),
        "candidate_categories": [{
            "id": "191",
            "name": "أساور",
            "path": "ملابس وإكسسوارات > حلي > أساور",
        }],
    }]
    result = asyncio.run(
        visual_gate.calibrated_ai_classify_chunk_with_visual_gate(client, rows)
    )["p1"]
    assert result["confidence"] == 95
    assert "الصورة متسقة مع التصنيف المقترح" in result["evidence"]


def test_existing_google_category_skips_high_confidence_visual_spend(monkeypatch):
    async def fake_calibrated(client, rows):
        return {
            "p1": {
                "product_id": "p1",
                "category_id": "5388",
                "confidence": 95,
                "reason": "تصنيف قائم",
                "evidence": [],
            }
        }

    monkeypatch.setattr(
        calibration,
        "_calibrated_ai_classify_chunk_original",
        fake_calibrated,
        raising=False,
    )
    client = _VisionClient({
        "verdict": "conflict",
        "confidence": 99,
        "observed_product_type": "",
        "reason": "",
        "evidence": [],
    })
    evidence = _evidence("عباية", image_url="https://cdn.example.com/abaya.jpg")
    evidence["current_google_category"] = "5388"
    rows = [{
        "product_id": "p1",
        "facts": evidence,
        "candidate_categories": [{
            "id": "5388",
            "name": "ملابس تقليدية",
            "path": "ملابس وإكسسوارات > ملابس > ملابس تقليدية",
        }],
    }]
    result = asyncio.run(
        visual_gate.calibrated_ai_classify_chunk_with_visual_gate(client, rows)
    )["p1"]
    assert result["confidence"] == 95
    assert client.responses.calls == []
