"""One-shot wiring for the central Order Engine V2 Salla refresh.

The resulting production code stays entirely inside Mezan OS V2. This temporary
script and its bootstrap workflow are removed before merge.
"""
from __future__ import annotations

import re
from pathlib import Path


ROUTES = Path("backend/order_engine/routes.py")
REVIEW = Path("backend/order_review_routes.py")
ORDER_SERVICE = Path("frontend/src/services/orderEngine.js")
ORDER_PAGE = Path("frontend/src/pages/OrderDetailsV2.jsx")
REVIEW_TEST = Path("backend/tests/test_order_review_stage_one.py")
CONTRACT_TEST = Path("backend/tests/test_fulfillment_v2_contract.py")
WORKFLOW = Path(".github/workflows/fulfillment-v2.yml")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def regex_replace_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one regex match, found {count}")
    return updated


def add_path_after_every(text: str, anchor: str, additions: str) -> str:
    if additions.splitlines()[0] in text:
        return text
    count = text.count(anchor)
    if count != 2:
        raise SystemExit(f"workflow anchor {anchor!r}: expected 2 matches, found {count}")
    return text.replace(anchor, anchor + additions)


# 1) Expose the central refresh through Orders V2.
routes = ROUTES.read_text(encoding="utf-8")
if "from .salla_refresh import refresh_order_from_salla" not in routes:
    routes = replace_once(
        routes,
        "from .repository import MongoOrderRepository, OrderRepository\n",
        "from .repository import MongoOrderRepository, OrderRepository\n"
        "from .salla_refresh import refresh_order_from_salla\n",
        "Order Engine refresh import",
    )

if 'summary="Refresh one order from Salla Order Details"' not in routes:
    endpoint = '''    @router.post(
        "/{order_number}/refresh-from-salla",
        summary="Refresh one order from Salla Order Details",
    )
    async def refresh_one_order_from_salla(
        order_number: str,
        force: bool = Query(default=True),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = _require_owner(user)
        result = await refresh_order_from_salla(
            db,
            str(owner["id"]),
            str(order_number),
            force=bool(force),
        )

        if result.get("ok") and result.get("found"):
            return result

        if result.get("ok") and not result.get("found"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    **result,
                    "message": "لم يتم العثور على الطلب في سلة.",
                },
            )

        if result.get("needs_reauth"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    **result,
                    "message": "صلاحية قراءة الطلبات في سلة تحتاج إعادة تفويض المتجر.",
                },
            )

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                **result,
                "message": result.get("message") or "تعذّر تحديث الطلب من سلة.",
            },
        )

'''
    marker = '    @router.post("/{order_number}/read")\n'
    if marker not in routes:
        raise SystemExit("Order Engine refresh endpoint insertion marker not found")
    routes = routes.replace(marker, endpoint + marker, 1)
ROUTES.write_text(routes, encoding="utf-8")


# 2) Make Fulfillment V2 consume the same central refresh service.
review = REVIEW.read_text(encoding="utf-8")
if "from order_engine.salla_refresh import refresh_order_from_salla" not in review:
    review = replace_once(
        review,
        "from order_engine.repository import MongoOrderRepository\n",
        "from order_engine.repository import MongoOrderRepository\n"
        "from order_engine.salla_refresh import refresh_order_from_salla\n",
        "review central refresh import",
    )
review = review.replace("from salla_integration.sync import resync_single_order\n", "")
review = review.replace("REVIEW_SOURCE_REFRESH_VERSION = 2\n", "")

if "async def _refresh_review_source_once" in review:
    review = regex_replace_once(
        review,
        r"\nasync def _refresh_review_source_once\(.*?\n\nasync def _sync_salla_reviewed",
        "\nasync def _sync_salla_reviewed",
        "remove page-specific review refresh",
    )

route_pattern = (
    r'    @router\.get\("/\{order_number\}"\)\n'
    r'    async def get_review_detail\(order_number: str, user: dict = Depends\(current_user\)\) -> dict\[str, Any\]:\n'
    r'.*?'
    r'        return await _detail\(db, merchant_id, order\)\n'
)
route_replacement = '''    @router.get("/{order_number}")
    async def get_review_detail(order_number: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        merchant_id = _merchant_user_id(reviewer)

        # One central V2 refresh boundary. It reads delivery facts from Salla
        # Order Details and line items from List Order Items, never Shipments or
        # Qoyod. Failure is non-blocking so the durable local snapshot remains
        # available to the reviewer.
        await refresh_order_from_salla(
            db,
            merchant_id,
            order_number,
            force=False,
            minimum_fresh_seconds=120,
        )

        try:
            order = await get_order(repository, user_id=merchant_id, order_number=order_number)
        except OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "order_not_found"}) from exc
        return await _detail(db, merchant_id, order)
'''
review = regex_replace_once(
    review,
    route_pattern,
    route_replacement,
    "review detail central refresh route",
)
REVIEW.write_text(review, encoding="utf-8")


# 3) Expose the refresh action to the Orders V2 frontend.
service = ORDER_SERVICE.read_text(encoding="utf-8")
service_replacement = '''export async function refreshOrderFromSalla(orderNumber, { force = true } = {}) {
    const normalized = String(orderNumber || "").trim();
    if (!normalized) throw new Error("رقم الطلب مطلوب.");
    if (isPreviewDemoEnvironment()) {
        return {
            ok: true,
            found: true,
            updated: false,
            skipped: true,
            source: "preview",
        };
    }
    try {
        const { data } = await api.post(
            `/orders-v2/${encodeURIComponent(normalized)}/refresh-from-salla`,
            null,
            { params: { force: Boolean(force) } },
        );
        return data;
    } catch (error) {
        throw new Error(errorMessage(error, "تعذّر تحديث الطلب من سلة."));
    }
}

// Compatibility alias for callers that used the earlier name.
export async function openOrderFromSalla(orderNumber) {
    return refreshOrderFromSalla(orderNumber, { force: true });
}


export async function markOrderRead'''
service = regex_replace_once(
    service,
    r"export async function openOrderFromSalla\(orderNumber\) \{.*?\n\}\n\n\nexport async function markOrderRead",
    service_replacement,
    "Order Engine frontend refresh service",
)
ORDER_SERVICE.write_text(service, encoding="utf-8")


# 4) Add an explicit owner action to Orders V2 details.
page = ORDER_PAGE.read_text(encoding="utf-8")
if "    ArrowsClockwise,\n" not in page:
    page = replace_once(
        page,
        "    ArrowRight,\n",
        "    ArrowRight,\n    ArrowsClockwise,\n",
        "Order Details refresh icon",
    )
if "    refreshOrderFromSalla,\n" not in page:
    page = replace_once(
        page,
        "    markOrderRead,\n",
        "    markOrderRead,\n    refreshOrderFromSalla,\n",
        "Order Details refresh service import",
    )

if "const [refreshingFromSalla, setRefreshingFromSalla]" not in page:
    page = replace_once(
        page,
        "    const [returnEngineOpen, setReturnEngineOpen] = useState(false);\n",
        "    const [returnEngineOpen, setReturnEngineOpen] = useState(false);\n"
        "    const [refreshingFromSalla, setRefreshingFromSalla] = useState(false);\n"
        "    const [refreshFromSallaError, setRefreshFromSallaError] = useState(\"\");\n"
        "    const [refreshFromSallaMessage, setRefreshFromSallaMessage] = useState(\"\");\n",
        "Order Details refresh state",
    )

if "async function updateOrderFromSalla" not in page:
    handler = '''    async function updateOrderFromSalla() {
        if (!openedOrderNumber || refreshingFromSalla) return;
        setRefreshingFromSalla(true);
        setRefreshFromSallaError("");
        setRefreshFromSallaMessage("");
        try {
            const result = await refreshOrderFromSalla(openedOrderNumber, { force: true });
            await Promise.all([reloadOrder(), reloadItems()]);
            setRefreshFromSallaMessage(
                result?.address_found
                    ? "تم تحديث الطلب والعنوان من سلة."
                    : "تم تحديث الطلب من سلة؛ لم ترجع سلة عنوان توصيل في بيانات الطلب.",
            );
        } catch (refreshError) {
            setRefreshFromSallaError(refreshError?.message || "تعذّر تحديث الطلب من سلة.");
        } finally {
            setRefreshingFromSalla(false);
        }
    }

'''
    marker = '    if (loading) return <div className="flex min-h-[60vh] items-center justify-center">'
    if marker not in page:
        raise SystemExit("Order Details refresh handler insertion marker not found")
    page = page.replace(marker, handler + marker, 1)

old_header = (
    '                <div className="text-left"><div className="num text-2xl font-extrabold text-slate-950">'
    '{formatMoney(total, currency)}</div><div className="mt-1 text-xs font-bold text-slate-400">'
    'عملة الطلب: {currency}</div></div>\n'
)
new_header = '''                <div className="flex flex-col items-start gap-3 lg:items-end">
                    <div className="text-left"><div className="num text-2xl font-extrabold text-slate-950">{formatMoney(total, currency)}</div><div className="mt-1 text-xs font-bold text-slate-400">عملة الطلب: {currency}</div></div>
                    <button
                        type="button"
                        onClick={updateOrderFromSalla}
                        disabled={refreshingFromSalla}
                        data-testid="order-v2-refresh-from-salla"
                        className="inline-flex items-center justify-center gap-2 rounded-xl border border-violet-200 bg-violet-50 px-4 py-2.5 text-sm font-extrabold text-violet-800 transition hover:bg-violet-100 disabled:cursor-wait disabled:opacity-60"
                    >
                        <ArrowsClockwise size={19} weight="bold" className={refreshingFromSalla ? "animate-spin" : ""} />
                        {refreshingFromSalla ? "جاري التحديث…" : "تحديث من سلة"}
                    </button>
                    {refreshFromSallaMessage && <div className="max-w-sm text-right text-xs font-bold text-emerald-700">{refreshFromSallaMessage}</div>}
                    {refreshFromSallaError && <div className="max-w-sm text-right text-xs font-bold text-rose-700">{refreshFromSallaError}</div>}
                </div>
'''
if 'data-testid="order-v2-refresh-from-salla"' not in page:
    page = replace_once(page, old_header, new_header, "Order Details refresh button")
ORDER_PAGE.write_text(page, encoding="utf-8")


# 5) Replace page-specific refresh tests with central V2 contracts.
review_test = REVIEW_TEST.read_text(encoding="utf-8")
if "import inspect\n" not in review_test:
    review_test = replace_once(
        review_test,
        "from datetime import datetime, timezone\n",
        "from datetime import datetime, timezone\nimport inspect\n",
        "review test inspect import",
    )
review_test = review_test.replace("    REVIEW_SOURCE_REFRESH_VERSION,\n", "")
review_test = review_test.replace("    _refresh_review_source_once,\n", "")
if "    make_order_review_router,\n" not in review_test:
    review_test = replace_once(
        review_test,
        "    build_image_preference_identity,\n",
        "    build_image_preference_identity,\n    make_order_review_router,\n",
        "review router test import",
    )

if "class _ReviewRefreshCollection:" in review_test:
    review_test = regex_replace_once(
        review_test,
        r"\nclass _ReviewRefreshCollection:.*?(?=\n\n@pytest\.mark\.asyncio\nasync def test_empty_current_shipments_preserve_embedded_delivery_context)",
        '''

def test_review_detail_uses_central_orders_v2_refresh():
    source = inspect.getsource(make_order_review_router)

    assert "refresh_order_from_salla(" in source
    assert "minimum_fresh_seconds=120" in source
    assert "resync_single_order" not in source
    assert "_refresh_review_source_once" not in source
''',
        "replace review page-specific refresh test",
    )
REVIEW_TEST.write_text(review_test, encoding="utf-8")

contract = CONTRACT_TEST.read_text(encoding="utf-8")
if "test_salla_refresh_is_centralized_in_orders_v2" not in contract:
    contract += '''


def test_salla_refresh_is_centralized_in_orders_v2():
    review_source = (ROOT / "backend/order_review_routes.py").read_text(encoding="utf-8")
    routes_source = (ROOT / "backend/order_engine/routes.py").read_text(encoding="utf-8")
    refresh_source = (ROOT / "backend/order_engine/salla_refresh.py").read_text(encoding="utf-8")
    service_source = (ROOT / "frontend/src/services/orderEngine.js").read_text(encoding="utf-8")
    details_source = (ROOT / "frontend/src/pages/OrderDetailsV2.jsx").read_text(encoding="utf-8")

    assert "from order_engine.salla_refresh import refresh_order_from_salla" in review_source
    assert "resync_single_order" not in review_source
    assert "_refresh_review_source_once" not in review_source
    assert '"/{order_number}/refresh-from-salla"' in routes_source
    assert 'f"/orders/{internal_id}"' in refresh_source
    assert '"/shipments"' not in refresh_source
    assert "no_shipments_api_calls" in refresh_source
    assert "refreshOrderFromSalla" in service_source
    assert 'data-testid="order-v2-refresh-from-salla"' in details_source
'''
CONTRACT_TEST.write_text(contract, encoding="utf-8")


# 6) Ensure CI owns every central-refresh file and test.
workflow = WORKFLOW.read_text(encoding="utf-8")
workflow = add_path_after_every(
    workflow,
    '      - "backend/order_engine/repository.py"\n',
    '      - "backend/order_engine/routes.py"\n'
    '      - "backend/order_engine/salla_refresh.py"\n',
)
workflow = add_path_after_every(
    workflow,
    '      - "backend/tests/test_order_engine_repository.py"\n',
    '      - "backend/tests/test_order_engine_salla_refresh.py"\n',
)
workflow = add_path_after_every(
    workflow,
    '      - "frontend/src/pages/OrderReview.test.jsx"\n',
    '      - "frontend/src/pages/OrderDetailsV2.jsx"\n'
    '      - "frontend/src/services/orderEngine.js"\n',
)
if "          tests/test_order_engine_salla_refresh.py\n" not in workflow:
    workflow = replace_once(
        workflow,
        "          tests/test_order_engine_repository.py\n",
        "          tests/test_order_engine_repository.py\n"
        "          tests/test_order_engine_salla_refresh.py\n",
        "Fulfillment V2 central refresh test",
    )
WORKFLOW.write_text(workflow, encoding="utf-8")

print("Orders V2 central Salla refresh wiring applied.")
