import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import product_google_taxonomy_ai_pilot as pilot_module
from product_google_taxonomy_ai_pilot import (
    CLASSIFICATION_CONCURRENCY,
    CLASSIFICATION_WAVE_SIZE,
    OPENAI_REQUEST_SPACING_SECONDS,
    OPENAI_RETRY_BASE_SECONDS,
    PILOT_PRODUCT_PROJECTION,
    PILOT_SELECTION_PROJECTION,
    PilotStartIn,
    _call_openai_with_backoff,
    _candidate_rows,
    _claim_run_lease,
    _decision_status,
    _fallback_search_terms,
    _gather_bounded,
    _input_revision,
    _is_retryable_openai_error,
    _persist_records,
    _product_evidence,
    _retryable_product_ids,
    _run_needs_resume,
    _run_counters,
    _schedule_pilot_task,
    _selected_lookup_values,
    _select_pilot_products,
    _select_unseen_products,
)


def _evidence(name, *, categories=None, current=""):
    return {
        "product_id": f"p-{name}",
        "salla_product_id": "1",
        "name": name,
        "description": "",
        "short_description": "",
        "salla_categories": categories or [],
        "options": [],
        "product_type": "product",
        "brand": "",
        "sku": "",
        "gtin": "",
        "mpn": "",
        "current_google_category": current,
        "has_image": True,
    }


_RECOVERY_TAXONOMY = [
    {"id": "5598", "name": "المعاطف والسترات", "path": "ملابس وإكسسوارات > ملابس > ملابس خارجية > المعاطف والسترات", "depth": 3},
    {"id": "5388", "name": "ملابس الاحتفالات والملابس التقليدية", "path": "ملابس وإكسسوارات > ملابس > ملابس الاحتفالات والملابس التقليدية", "depth": 2},
    {"id": "6463", "name": "أطقم مجوهرات", "path": "ملابس وإكسسوارات > حُلي > أطقم مجوهرات", "depth": 2},
    {"id": "2789", "name": "معطرات هواء للمركبات", "path": "المركبات وقطع الغيار > إكسسوارات العربات وقطع غيارها > صيانة المركبات والعناية بها وتزيينها > ديكور المركبات > معطرات هواء للمركبات", "depth": 4},
    {"id": "100", "name": "حقائب ظهر", "path": "أمتعة وحقائب > حقائب ظهر", "depth": 1},
    {"id": "193", "name": "أزرار الأكمام", "path": "ملابس وإكسسوارات > إكسسوارات الملابس > أزرار الأكمام", "depth": 2},
    {"id": "212", "name": "قمصان وبلوزات", "path": "ملابس وإكسسوارات > ملابس > قمصان وبلوزات", "depth": 2},
    {"id": "177", "name": "الأوشحة والشالات", "path": "ملابس وإكسسوارات > إكسسوارات الملابس > الأوشحة والشالات", "depth": 2},
    {"id": "982", "name": "أقلام حبر", "path": "المستلزمات المكتبية > أدوات مكتب > أدوات الكتابة والرسم > أقلام الرصاص والحبر الجاف > أقلام حبر", "depth": 4},
    {"id": "201", "name": "ساعات يد", "path": "ملابس وإكسسوارات > حُلي > ساعات يد", "depth": 2},
    {"id": "2580", "name": "البيجامات", "path": "ملابس وإكسسوارات > ملابس > ملابس نوم وملابس مريحة > البيجامات", "depth": 3},
    {"id": "182", "name": "ملابس الرضع والأطفال الصغار", "path": "ملابس وإكسسوارات > ملابس > ملابس الرضع والأطفال الصغار", "depth": 2},
    {"id": "2169", "name": "الأكواب", "path": "الحديقة والمنزل > المطبخ وتناول الطعام > أدوات المائدة > أدوات تناول الشراب > الأكواب", "depth": 4},
    {"id": "1259", "name": "حيوانات محشوة", "path": "ألعاب أطفال > ألعاب الطفل > دمى وشخصيات ومجموعات لعب > حيوانات محشوة", "depth": 3},
]


def test_abaya_alias_retrieves_traditional_clothing_candidate():
    taxonomy = [
        {"id": "5388", "name": "ملابس الاحتفالات والملابس التقليدية", "path": "ملابس وإكسسوارات > ملابس > ملابس الاحتفالات والملابس التقليدية", "depth": 2},
        {"id": "2271", "name": "فساتين", "path": "ملابس وإكسسوارات > ملابس > فساتين", "depth": 2},
    ]
    rows = _candidate_rows(_evidence("عباية صيفية مطرزة"), [], taxonomy, None)
    assert rows
    assert rows[0]["id"] == "5388"


def test_necklace_alias_retrieves_necklaces_candidate():
    taxonomy = [
        {"id": "196", "name": "قلادات", "path": "ملابس وإكسسوارات > حلي > قلادات", "depth": 2},
        {"id": "200", "name": "خواتم", "path": "ملابس وإكسسوارات > حلي > خواتم", "depth": 2},
    ]
    rows = _candidate_rows(_evidence("سلسال القطرة مرصع بالزركون"), [], taxonomy, None)
    assert rows[0]["id"] == "196"


def test_ai_search_terms_are_combined_with_deterministic_aliases():
    terms = _fallback_search_terms(_evidence("سلسال بالاسم"))
    assert any("قلادات" in term for term in terms)


@pytest.mark.parametrize(
    ("name", "description", "expected_id"),
    [
        ("جاكيت شتوي", "", "5598"),
        ("طقم بناتي بالاسم", "", "6463"),
        ("فواحة - leather", "", "2789"),
        ("شنطة مدرسيه بالاسم", "", "100"),
        ("كبك اطفال بالاسم", "", "193"),
        ("هودي أسود كاجوال", "", "212"),
        ("وشاح تخرج تطريز", "", "177"),
        ("قلم رجالي انيق بالاسم", "", "982"),
        ("ساعة كاسيو LTP", "", "201"),
        ("D1", "بجامة قابلة للتطريز", "2580"),
        ("افرول رمضان مواليد", "", "182"),
        ("كوب شعار", "", "2169"),
        ("مجسم لابوبو", "", "1259"),
    ],
)
def test_sparse_catalog_products_receive_relevant_official_candidates(
    name,
    description,
    expected_id,
):
    evidence = {**_evidence(name), "description": description}
    candidate_ids = {
        str(row["id"])
        for row in _candidate_rows(evidence, [], _RECOVERY_TAXONOMY, None)
    }
    assert expected_id in candidate_ids


def test_existing_category_never_batch_overwritten_when_ai_disagrees():
    assert _decision_status(current_id="5388", chosen_id="2271", confidence=99) == "review_required_existing_category"
    assert _decision_status(current_id="5388", chosen_id="5388", confidence=95) == "no_change"


def test_confidence_policy_matches_pilot_gate():
    assert _decision_status(current_id=None, chosen_id="196", confidence=90) == "high_confidence"
    assert _decision_status(current_id=None, chosen_id="196", confidence=89) == "review_required"
    assert _decision_status(current_id=None, chosen_id="196", confidence=69) == "low_confidence"
    assert _decision_status(current_id=None, chosen_id=None, confidence=99) == "low_confidence"


def test_classification_revision_changes_when_relevant_product_fact_changes():
    first = _evidence("سلسال فضي")
    second = {**first, "name": "سلسال ذهبي"}
    assert _input_revision(first) != _input_revision(second)


def test_product_evidence_is_allowlisted_and_does_not_leak_customer_fields():
    product = {
        "mezan_product_id": "mpv2_1",
        "salla_product_id": "1",
        "name": "عباية",
        "description": "وصف المنتج",
        "categories": [{"id": "10", "name": "عبايات"}],
        "raw_salla": {
            "customer_email": "secret@example.com",
            "customer_phone": "0500000000",
            "gtin": "123",
            "mpn": "ABC",
        },
    }
    result = _product_evidence(product)
    assert "customer_email" not in result
    assert "customer_phone" not in result
    assert result["gtin"] == "123"
    assert result["mpn"] == "ABC"


def test_pilot_selection_keeps_diversity_and_some_existing_categories():
    products = []
    for index in range(18):
        products.append({
            "mezan_product_id": f"missing-{index}",
            "name": f"منتج {index}",
            "product_type": "product",
            "categories": [{"name": f"قسم {index % 4}"}],
        })
    for index in range(4):
        products.append({
            "mezan_product_id": f"existing-{index}",
            "name": f"مصنف {index}",
            "product_type": "product",
            "google_category": "5388",
            "categories": [{"name": f"قسم موجود {index}"}],
        })
    selected = _select_pilot_products(products, 20)
    assert len(selected) == 20
    assert any(row.get("google_category") for row in selected)
    keys = {row["categories"][0]["name"] for row in selected if row.get("categories")}
    assert len(keys) >= 4


def test_full_rollout_accepts_200_products_in_next_unseen_mode():
    payload = PilotStartIn(limit=200, selection_mode="next_unseen")
    assert payload.limit == 200
    assert payload.selection_mode == "next_unseen"


def test_retry_review_mode_is_explicit_and_never_retries_approved_rows():
    payload = PilotStartIn(limit=200, selection_mode="retry_review")
    assert payload.selection_mode == "retry_review"
    assert _retryable_product_ids(
        [
            {"mezan_product_id": "review", "decision_status": "review_required", "apply_status": "pending"},
            {"mezan_product_id": "low", "decision_status": "low_confidence", "apply_status": "pending"},
            {"mezan_product_id": "approved", "decision_status": "high_confidence", "apply_status": "applied"},
            {"mezan_product_id": "already-applied", "decision_status": "review_required", "apply_status": "applied"},
            {"mezan_product_id": "review", "decision_status": "review_required", "apply_status": "pending"},
        ],
        200,
    ) == ["review", "low"]


def test_rollout_catalog_scan_is_lightweight_and_work_waves_are_bounded():
    assert CLASSIFICATION_WAVE_SIZE == 15
    assert PILOT_SELECTION_PROJECTION["categories"] == {"$slice": 1}
    assert "description" not in PILOT_SELECTION_PROJECTION
    assert "options" not in PILOT_SELECTION_PROJECTION
    assert "raw_salla" not in PILOT_PRODUCT_PROJECTION
    assert "raw_salla.description" in PILOT_PRODUCT_PROJECTION
    assert PILOT_PRODUCT_PROJECTION["images"] == {"$slice": 1}


def test_selected_product_lookup_supports_string_and_legacy_numeric_ids():
    assert _selected_lookup_values(["mpv2_1", "123"]) == [
        "mpv2_1", "123", 123,
    ]


def test_full_rollout_skips_products_already_seen_in_prior_runs():
    products = [
        {
            "mezan_product_id": f"product-{index}",
            "name": f"منتج {index}",
            "product_type": "product",
            "categories": [{"name": f"قسم {index % 3}"}],
        }
        for index in range(12)
    ]
    selected = _select_unseen_products(
        products,
        {"product-0", "product-1", "product-2", "product-3"},
        6,
    )
    selected_ids = {row["mezan_product_id"] for row in selected}
    assert len(selected) == 6
    assert not selected_ids.intersection({"product-0", "product-1", "product-2", "product-3"})


@pytest.mark.asyncio
async def test_rollout_chunk_concurrency_is_bounded_and_keeps_input_order():
    active = 0
    max_active = 0

    async def worker(value):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return value * 2

    results = await _gather_bounded(list(range(8)), worker, concurrency=3)

    assert results == [value * 2 for value in range(8)]
    assert max_active == 3


@pytest.mark.asyncio
async def test_provider_backoff_honors_retry_after_then_exponential_delay():
    class Response:
        def __init__(self, retry_after=None):
            self.headers = (
                {"retry-after": str(retry_after)}
                if retry_after is not None
                else {}
            )

    class TransientProviderError(Exception):
        status_code = 429

        def __init__(self, retry_after=None):
            super().__init__("rate limited")
            self.response = Response(retry_after)

    attempts = 0
    sleeps = []

    async def operation():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TransientProviderError(7)
        if attempts == 2:
            raise TransientProviderError()
        return "ok"

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    result = await _call_openai_with_backoff(operation, sleep=fake_sleep)

    assert result == "ok"
    assert attempts == 3
    assert sleeps == [
        7.0,
        OPENAI_RETRY_BASE_SECONDS * 2,
        OPENAI_REQUEST_SPACING_SECONDS,
    ]


def test_hard_quota_error_is_not_retried():
    class HardQuotaError(Exception):
        status_code = 429
        body = {"error": {"code": "insufficient_quota"}}

    assert _is_retryable_openai_error(HardQuotaError()) is False


def test_taxonomy_provider_calls_are_sequential_but_checkpoints_remain_bounded():
    assert CLASSIFICATION_CONCURRENCY == 1
    assert CLASSIFICATION_WAVE_SIZE == 15


@pytest.mark.asyncio
async def test_worker_registry_detaches_from_request_and_deduplicates_run(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def fake_execute(db, user_id, run_id, limit, selection_mode):
        calls.append((db, user_id, run_id, limit, selection_mode))
        started.set()
        await release.wait()

    monkeypatch.setattr(pilot_module, "_execute_pilot", fake_execute)
    registry = {}
    db = object()

    first = _schedule_pilot_task(
        registry, db, "user-1", "run-1", 200, "next_unseen"
    )
    second = _schedule_pilot_task(
        registry, db, "user-1", "run-1", 200, "next_unseen"
    )

    await asyncio.wait_for(started.wait(), timeout=1)
    assert first is second
    assert len(calls) == 1
    assert not first.done()
    assert registry["user-1:run-1"] is first

    release.set()
    await first
    await asyncio.sleep(0)
    assert registry == {}


def test_visual_failures_are_counted_separately_from_ai_failures():
    counters = _run_counters([
        {
            "decision_status": "review_required",
            "visual_verification_status": "failed",
        },
        {
            "decision_status": "high_confidence",
            "visual_verification_status": "consistent",
        },
        {"decision_status": "ai_failed"},
    ], 3)
    assert counters["visual_checked"] == 2
    assert counters["visual_failed"] == 1
    assert counters["ai_failed"] == 1


def test_interrupted_run_without_lease_is_immediately_resumable():
    assert _run_needs_resume({"status": "running"}) is True


def test_live_lease_prevents_duplicate_worker_and_expired_lease_recovers():
    now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    assert _run_needs_resume({
        "status": "running",
        "lease_expires_at": now + timedelta(seconds=30),
    }, now=now) is False
    assert _run_needs_resume({
        "status": "running",
        "lease_expires_at": now - timedelta(seconds=1),
    }, now=now) is True
    assert _run_needs_resume({
        "status": "completed",
        "lease_expires_at": now - timedelta(seconds=1),
    }, now=now) is False


def test_run_counters_preserve_durable_apply_progress():
    counters = _run_counters([
        {"decision_status": "high_confidence", "apply_status": "applied"},
        {"decision_status": "review_required", "apply_status": "not_eligible"},
    ], 2)
    assert counters["applied"] == 1


@pytest.mark.asyncio
async def test_partial_checkpoint_is_idempotent_and_never_overwrites_saved_result():
    class Collection:
        def __init__(self):
            self.rows = {}

        async def update_one(self, query, update, upsert=False):
            key = (query["user_id"], query["run_id"], query["mezan_product_id"])
            if key not in self.rows and upsert:
                self.rows[key] = dict(update["$setOnInsert"])

    class Db:
        def __init__(self):
            self.collection = Collection()

        def __getitem__(self, _name):
            return self.collection

    db = Db()
    first = {
        "user_id": "user-1",
        "run_id": "run-1",
        "mezan_product_id": "product-1",
        "decision_status": "high_confidence",
        "ai_confidence": 94,
    }
    duplicate_after_restart = {**first, "decision_status": "ai_failed", "ai_confidence": 0}

    await _persist_records(db, [first])
    await _persist_records(db, [duplicate_after_restart])

    saved = next(iter(db.collection.rows.values()))
    assert len(db.collection.rows) == 1
    assert saved["decision_status"] == "high_confidence"
    assert saved["ai_confidence"] == 94


@pytest.mark.asyncio
async def test_atomic_lease_allows_only_one_worker_for_a_run():
    class Result:
        def __init__(self, modified):
            self.modified_count = modified

    class Runs:
        def __init__(self):
            self.doc = {
                "user_id": "user-1",
                "run_id": "run-1",
                "status": "queued",
                "lease_owner": None,
                "lease_expires_at": None,
                "started_at": None,
            }

        async def find_one(self, query, _projection):
            return dict(self.doc) if query["run_id"] == self.doc["run_id"] else None

        async def update_one(self, query, update):
            now = query["$or"][2]["lease_expires_at"]["$lte"]
            expires = self.doc.get("lease_expires_at")
            claimable = self.doc.get("lease_owner") is None or (
                isinstance(expires, datetime) and expires <= now
            )
            if not claimable:
                return Result(0)
            self.doc.update(update["$set"])
            for key, value in update.get("$inc", {}).items():
                self.doc[key] = int(self.doc.get(key) or 0) + value
            return Result(1)

    class Db:
        def __init__(self):
            self.runs = Runs()

        def __getitem__(self, _name):
            return self.runs

    db = Db()
    first = await _claim_run_lease(db, "user-1", "run-1", "worker-a")
    second = await _claim_run_lease(db, "user-1", "run-1", "worker-b")

    assert first is not None
    assert second is None
    assert db.runs.doc["lease_owner"] == "worker-a"
