"""One-shot patch for authoritative review details, receipt, and item specs."""
from pathlib import Path


ROOT = Path(".")
ROUTES = ROOT / "backend/order_review_routes.py"
MAPPER = ROOT / "backend/order_engine/mapper.py"
REVIEW_TESTS = ROOT / "backend/tests/test_order_review_stage_one.py"
MAPPER_TESTS = ROOT / "backend/tests/test_order_engine_mapper.py"
FRONTEND = ROOT / "frontend/src/pages/OrderReview.jsx"
FRONTEND_TEST = ROOT / "frontend/src/pages/OrderReview.test.jsx"
WORKFLOW = ROOT / ".github/workflows/fulfillment-v2.yml"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    print(f"PATCH {path}: {label} matches={count}", flush=True)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# Backend review route: explicit review-open refresh is the safe authoritative path.
replace_once(
    ROUTES,
    "from salla_integration.service import SallaError, call_salla\n",
    "from salla_integration.service import SallaError, call_salla\n"
    "from salla_integration.sync import resync_single_order\n",
    "import authoritative single-order resync",
)

replace_once(
    ROUTES,
    '''    return {
        "order": order.model_dump(mode="json"),
        "stage": (workflow or {}).get("stage") or "pending_review",
        "revision": int((workflow or {}).get("revision") or 0),
        "items": item_views,
    }


async def _sync_salla_reviewed''',
    '''    return {
        "order": order.model_dump(mode="json"),
        "stage": (workflow or {}).get("stage") or "pending_review",
        "revision": int((workflow or {}).get("revision") or 0),
        "items": item_views,
    }


async def _refresh_review_source_once(db: Any, user_id: str, order_number: str) -> bool:
    """Refresh the order from Salla once when the reviewer explicitly opens it.

    List polling remains light and never opens Salla Order Details. Opening the
    review drawer is an explicit merchant action, so it is the correct boundary
    for retrieving the receipt image and authoritative line-item options/files.
    Failures are non-blocking: the locally cached review still opens.
    """
    try:
        existing = await db.unified_orders.find_one(
            {"user_id": user_id, "order_number": order_number},
            {"_id": 0, "order_review_source_refreshed_at": 1},
        )
        if _text((existing or {}).get("order_review_source_refreshed_at")):
            return False

        result = await resync_single_order(db, user_id, order_number)
        if not isinstance(result, dict) or not result.get("ok") or not result.get("found"):
            return False

        refreshed_at = _now()
        await db.unified_orders.update_one(
            {"user_id": user_id, "order_number": order_number},
            {"$set": {
                "order_review_source_refreshed_at": refreshed_at,
                "order_review_source_refresh_mode": "explicit_review_open",
            }},
        )
        return True
    except Exception:
        return False


async def _sync_salla_reviewed''',
    "authoritative review refresh helper",
)

replace_once(
    ROUTES,
    '''        try:
            order = await get_order(repository, user_id=merchant_id, order_number=order_number)
        except OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "order_not_found"}) from exc
        return await _detail(db, merchant_id, order)''',
    '''        try:
            order = await get_order(repository, user_id=merchant_id, order_number=order_number)
        except OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "order_not_found"}) from exc

        if await _refresh_review_source_once(db, merchant_id, order_number):
            try:
                order = await get_order(repository, user_id=merchant_id, order_number=order_number)
            except OrderNotFoundError:
                pass
        return await _detail(db, merchant_id, order)''',
    "refresh review details on explicit open",
)

# Canonical mapper: preserve Salla file/custom-field shapes and parse receipt media objects.
replace_once(
    MAPPER,
    '''    candidates = [
        item.get("custom_fields"),
        item.get("customizations"),
        item.get("personalization"),
        item.get("attachments"),
    ]''',
    '''    candidates = [
        item.get("custom_fields"),
        item.get("customizations"),
        item.get("personalization"),
        item.get("attachments"),
        item.get("files"),
    ]''',
    "include Salla files as custom fields",
)

replace_once(
    MAPPER,
    '''    receipt_url = _text(
        _first(
            raw_order.get("payment_receipt_url"),
            raw_order.get("receipt_image"),
            payment_raw.get("receipt_url"),
            payment_raw.get("receipt_image"),
            payment_raw.get("attachment_url"),
            payment_raw.get("proof_url"),
            payment_raw.get("transfer_receipt_url"),
        )
    )''',
    '''    receipt_url = (
        _media_url(raw_order.get("payment_receipt_url"))
        or _media_url(raw_order.get("receipt_image"))
        or _media_url(raw_order.get("transfer_receipt"))
        or _media_url(payment_raw.get("receipt_url"))
        or _media_url(payment_raw.get("receipt_image"))
        or _media_url(payment_raw.get("attachment_url"))
        or _media_url(payment_raw.get("proof_url"))
        or _media_url(payment_raw.get("proof"))
        or _media_url(payment_raw.get("transfer_receipt_url"))
        or _media_url(bank_raw.get("receipt_url"))
        or _media_url(bank_raw.get("receipt_image"))
    )''',
    "extract receipt URL from nested media shapes",
)

# Frontend: show custom fields like Salla and render the receipt image.
replace_once(
    FRONTEND,
    '''function uniqueProductSpecs(item) {
    const specs = [];
    const seen = new Set();
    const add = (name, value) => {
        const cleanName = String(name || "").trim();
        const cleanValue = String(value ?? "").trim();
        if (!cleanName || !cleanValue) return;
        const key = canonicalSpecName(cleanName);
        if (!key || seen.has(key)) return;
        seen.add(key);
        specs.push({ name: cleanName, value: cleanValue, key });
    };

    (Array.isArray(item.options) ? item.options : []).forEach((option) => add(option?.name, option?.value));
    add("اللون", item.color);
    add("المقاس", item.size);
    return specs;
}
''',
    '''function visibleSpecValue(value, depth = 0) {
    if (depth > 6 || value === null || value === undefined || value === "") return "";
    if (typeof value === "boolean") return value ? "نعم" : "لا";
    if (typeof value === "string" || typeof value === "number") return String(value).trim();
    if (Array.isArray(value)) {
        return value.map((entry) => visibleSpecValue(entry, depth + 1)).filter(Boolean).join(" / ");
    }
    if (typeof value === "object") {
        for (const key of ["value", "answer", "selected", "choice", "text", "name", "label", "title", "option_value", "response"]) {
            const visible = visibleSpecValue(value[key], depth + 1);
            if (visible) return visible;
        }
        if (["url", "file_url", "download_url", "src"].some((key) => visibleSpecValue(value[key], depth + 1))) {
            return "مرفق";
        }
    }
    return "";
}

const CUSTOM_FIELD_META_KEYS = new Set([
    "id", "type", "required", "created_at", "updated_at",
    "name", "label", "title", "question", "key", "option",
    "value", "answer", "selected", "choice", "text", "values",
    "option_value", "response", "file", "attachment", "url",
]);

export function reviewProductSpecs(item) {
    const specs = [];
    const seen = new Set();
    const add = (name, value) => {
        const cleanName = String(name || "").trim();
        const cleanValue = visibleSpecValue(value);
        if (!cleanName || !cleanValue) return;
        const key = canonicalSpecName(cleanName);
        if (!key || seen.has(key)) return;
        seen.add(key);
        specs.push({ name: cleanName, value: cleanValue, key });
    };

    (Array.isArray(item.options) ? item.options : []).forEach((option) => add(option?.name, option?.value));
    (Array.isArray(item.custom_fields) ? item.custom_fields : []).forEach((field) => {
        if (!field || typeof field !== "object" || Array.isArray(field)) return;
        const label = field.name || field.label || field.title || field.question || field.key || field.option;
        const value = field.value ?? field.answer ?? field.selected ?? field.choice ?? field.text
            ?? field.values ?? field.option_value ?? field.response ?? field.file ?? field.attachment ?? field.url;
        if (label && visibleSpecValue(value)) {
            add(label, value);
            return;
        }
        Object.entries(field).forEach(([key, rawValue]) => {
            if (!CUSTOM_FIELD_META_KEYS.has(key)) add(key, rawValue);
        });
    });
    add("اللون", item.color);
    add("المقاس", item.size);
    add("الخامة", item.material);
    return specs;
}

function safeReceiptUrl(value) {
    try {
        const url = new URL(String(value || "").trim());
        return ["http:", "https:"].includes(url.protocol) ? url.toString() : "";
    } catch {
        return "";
    }
}

export function PaymentReceiptCard({ receiptUrl }) {
    const url = safeReceiptUrl(receiptUrl);
    if (!url) return null;
    const isPdf = /\\.pdf(?:$|[?#])/i.test(url);
    return (
        <div data-testid="order-review-payment-receipt" className="overflow-hidden rounded-xl border border-amber-200 bg-amber-50">
            <div className="border-b border-amber-200 px-3 py-2 text-sm font-extrabold text-amber-950">صورة إيصال التحويل</div>
            {!isPdf && (
                <a href={url} target="_blank" rel="noreferrer" className="block bg-white p-3">
                    <img src={url} alt="إيصال التحويل البنكي" loading="lazy" className="mx-auto max-h-72 w-full rounded-lg object-contain" />
                </a>
            )}
            <a href={url} target="_blank" rel="noreferrer" className="flex items-center justify-center gap-1.5 px-3 py-2.5 text-sm font-extrabold text-amber-900 hover:bg-amber-100">
                <Eye size={17} /> فتح الإيصال بالحجم الكامل
            </a>
        </div>
    );
}
''',
    "product specs and receipt helpers",
)

replace_once(
    FRONTEND,
    "    const specs = uniqueProductSpecs(item);",
    "    const specs = reviewProductSpecs(item);",
    "use full review product specs",
)

replace_once(
    FRONTEND,
    '''    const payment = order?.payment || {};
    const shipping = order?.shipping || {};''',
    '''    const payment = order?.payment || {};
    const paymentMethod = paymentText(order).toLowerCase();
    const isBankTransfer = paymentMethod === "bank" || paymentMethod.includes("bank transfer")
        || paymentMethod.includes("تحويل بنكي") || paymentMethod.includes("حوالة بنكية");
    const shipping = order?.shipping || {};''',
    "derive bank-transfer state",
)

replace_once(
    FRONTEND,
    '''                                <div className="grid gap-3 sm:grid-cols-2">
                                    <Field label="طريقة الدفع" value={paymentText(order)} />
                                    <Field label="إجمالي الطلب" value={money(order.totals?.total, order.totals?.currency)} dir="ltr" />
                                    <Field label="المبلغ المدفوع" value={money(payment.paid_amount, order.totals?.currency)} dir="ltr" />
                                    <Field label="المبلغ المتبقي" value={money(payment.remaining_amount, order.totals?.currency)} dir="ltr" />
                                </div>''',
    '''                                <div className="grid gap-3 sm:grid-cols-2">
                                    <Field label="طريقة الدفع" value={paymentText(order)} />
                                    <Field label="إجمالي الطلب" value={money(order.totals?.total, order.totals?.currency)} dir="ltr" />
                                    <Field label="المبلغ المدفوع" value={money(payment.paid_amount, order.totals?.currency)} dir="ltr" />
                                    <Field label="المبلغ المتبقي" value={money(payment.remaining_amount, order.totals?.currency)} dir="ltr" />
                                    {payment.receiving_bank_name && <Field label="البنك المستلم" value={payment.receiving_bank_name} />}
                                    {payment.receipt_url ? (
                                        <div className="sm:col-span-2"><PaymentReceiptCard receiptUrl={payment.receipt_url} /></div>
                                    ) : isBankTransfer ? (
                                        <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm font-bold text-amber-900 sm:col-span-2">
                                            لا توجد صورة إيصال محفوظة في بيانات سلة لهذا الطلب.
                                        </div>
                                    ) : null}
                                </div>''',
    "render bank and transfer receipt",
)

# Backend unit tests.
replace_once(
    REVIEW_TESTS,
    '''    _review_item_identities,
    _reviewed_status_id,''',
    '''    _refresh_review_source_once,
    _review_item_identities,
    _reviewed_status_id,''',
    "import refresh helper in tests",
)

review_tests = REVIEW_TESTS.read_text(encoding="utf-8")
review_test_name = "test_review_open_refreshes_authoritative_salla_details_only_once"
if review_test_name not in review_tests:
    review_tests += r'''


class _ReviewRefreshCollection:
    def __init__(self):
        self.row = {"user_id": "owner-1", "order_number": "274724433"}

    async def find_one(self, _query, _projection=None):
        return dict(self.row)

    async def update_one(self, _selector, update):
        self.row.update(update.get("$set") or {})
        return None


class _ReviewRefreshDB:
    def __init__(self):
        self.unified_orders = _ReviewRefreshCollection()


@pytest.mark.asyncio
async def test_review_open_refreshes_authoritative_salla_details_only_once():
    db = _ReviewRefreshDB()
    result = {"ok": True, "found": True}

    with patch("order_review_routes.resync_single_order", new=AsyncMock(return_value=result)) as refresh:
        assert await _refresh_review_source_once(db, "owner-1", "274724433") is True
        assert await _refresh_review_source_once(db, "owner-1", "274724433") is False

    refresh.assert_awaited_once_with(db, "owner-1", "274724433")
    assert db.unified_orders.row["order_review_source_refresh_mode"] == "explicit_review_open"
'''
    REVIEW_TESTS.write_text(review_tests, encoding="utf-8")

mapper_tests = MAPPER_TESTS.read_text(encoding="utf-8")
mapper_test_name = "test_maps_nested_transfer_receipt_and_files_custom_field"
if mapper_test_name not in mapper_tests:
    mapper_tests += r'''


def test_maps_nested_transfer_receipt_and_files_custom_field(salla_order_payload):
    salla_order_payload["receipt_image"] = {
        "url": "https://cdn.salla.sa/example/receipt-274724433.jpg",
    }
    salla_order_payload["items"][0]["files"] = [
        {
            "name": "هل تريد إضافة كرت اهداء",
            "value": {"name": "لا"},
        }
    ]

    order = map_salla_order(salla_order_payload)

    assert order.payment.receipt_url == (
        "https://cdn.salla.sa/example/receipt-274724433.jpg"
    )
    assert order.items[0].custom_fields[-1] == {
        "name": "هل تريد إضافة كرت اهداء",
        "value": {"name": "لا"},
    }
'''
    MAPPER_TESTS.write_text(mapper_tests, encoding="utf-8")

# Focused frontend unit tests.
FRONTEND_TEST.write_text(r'''import { renderToStaticMarkup } from "react-dom/server";

jest.mock("../services/orderReviewEngine", () => ({
    completeOrderReview: jest.fn(),
    getOrderReview: jest.fn(),
    listPendingOrderReviews: jest.fn(),
    updateOrderReviewItem: jest.fn(),
}));

jest.mock("sonner", () => ({
    toast: { success: jest.fn(), error: jest.fn(), warning: jest.fn() },
}));

import { PaymentReceiptCard, reviewProductSpecs } from "./OrderReview";


test("review product specs include Salla custom fields and nested visible values", () => {
    const specs = reviewProductSpecs({
        options: [],
        custom_fields: [
            { name: "هل تريد إضافة كرت اهداء", value: { name: "لا" } },
            { "الاسم المطلوب": "سارة" },
        ],
        color: null,
        size: null,
        material: null,
    });

    expect(specs).toEqual(expect.arrayContaining([
        expect.objectContaining({ name: "هل تريد إضافة كرت اهداء", value: "لا" }),
        expect.objectContaining({ name: "الاسم المطلوب", value: "سارة" }),
    ]));
});


test("bank transfer receipt renders a clickable image and full-size link", () => {
    const url = "https://cdn.salla.sa/example/receipt-274724433.jpg";
    const markup = renderToStaticMarkup(<PaymentReceiptCard receiptUrl={url} />);

    expect(markup).toContain('data-testid="order-review-payment-receipt"');
    expect(markup).toContain(`src="${url}"`);
    expect(markup).toContain("فتح الإيصال بالحجم الكامل");
});


test("unsafe receipt URLs are not rendered", () => {
    expect(renderToStaticMarkup(<PaymentReceiptCard receiptUrl="javascript:alert(1)" />)).toBe("");
});
''', encoding="utf-8")

# CI coverage for the new backend and frontend contracts.
workflow = WORKFLOW.read_text(encoding="utf-8")
for marker, replacement in (
    (
        '      - "backend/order_review_routes.py"\n',
        '      - "backend/order_review_routes.py"\n      - "backend/order_engine/mapper.py"\n',
    ),
    (
        '      - "backend/tests/test_order_review_stage_one.py"\n',
        '      - "backend/tests/test_order_review_stage_one.py"\n      - "backend/tests/test_order_engine_mapper.py"\n',
    ),
    (
        '      - "frontend/src/pages/OrderReview.jsx"\n',
        '      - "frontend/src/pages/OrderReview.jsx"\n      - "frontend/src/pages/OrderReview.test.jsx"\n',
    ),
):
    # The same path block appears under pull_request and push; replace both.
    if workflow.count(marker) != 2:
        raise SystemExit(f"workflow marker count mismatch for {marker!r}: {workflow.count(marker)}")
    workflow = workflow.replace(marker, replacement)

workflow = workflow.replace(
    '''          tests/test_order_review_stage_one.py
          tests/test_fulfillment_v2_contract.py''',
    '''          tests/test_order_review_stage_one.py
          tests/test_order_engine_mapper.py
          tests/test_fulfillment_v2_contract.py''',
    1,
)
workflow = workflow.replace(
    'yarn test --watchAll=false --runInBand --runTestsByPath src/pages/FulfillmentV2.test.jsx > /tmp/fulfillment-v2-jest.log 2>&1',
    'yarn test --watchAll=false --runInBand --runTestsByPath src/pages/FulfillmentV2.test.jsx src/pages/OrderReview.test.jsx > /tmp/fulfillment-v2-jest.log 2>&1',
    1,
)
WORKFLOW.write_text(workflow, encoding="utf-8")

print("Authoritative review details patch applied.", flush=True)
