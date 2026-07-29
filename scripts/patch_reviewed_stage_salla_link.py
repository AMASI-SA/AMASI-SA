from pathlib import Path

BACKEND = Path('backend/order_review_routes.py')
REVIEW = Path('frontend/src/pages/OrderReview.jsx')
CONTRACT = Path('backend/tests/test_fulfillment_v2_contract.py')


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one match, got {count}')
    return text.replace(old, new, 1)


backend = BACKEND.read_text(encoding='utf-8')
backend = replace_once(
    backend,
    'async def _detail(db: Any, user_id: str, order: OrderDTO) -> dict[str, Any]:\n',
    '''async def _salla_admin_url(db: Any, user_id: str, order_number: str) -> str:
    row = await db.unified_orders.find_one(
        {"user_id": user_id, "order_number": order_number},
        {"_id": 0, "raw_by_source.salla_direct.urls.admin": 1, "raw_by_source.salla_direct.urls.customer": 1},
    ) or {}
    raw = ((row.get("raw_by_source") or {}).get("salla_direct") or {})
    urls = raw.get("urls") if isinstance(raw.get("urls"), dict) else {}
    return _text(urls.get("admin"))


async def _detail(db: Any, user_id: str, order: OrderDTO) -> dict[str, Any]:
''',
    'backend admin url helper',
)
backend = replace_once(
    backend,
    '        "order": order.model_dump(mode="json"),\n        "stage": (workflow or {}).get("stage") or "pending_review",',
    '        "order": {**order.model_dump(mode="json"), "salla_admin_url": await _salla_admin_url(db, user_id, order.order_number)},\n        "stage": (workflow or {}).get("stage") or "pending_review",',
    'detail admin url',
)
marker = '    @router.get("/{order_number}")\n'
insert = '''    @router.get("/reviewed")
    async def list_reviewed_reviews(
        limit: int = Query(50, ge=1, le=100),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        merchant_id = _merchant_user_id(reviewer)
        workflows = await db[WORKFLOWS].find(
            {"user_id": merchant_id, "stage": "reviewed"},
            {"_id": 0},
        ).sort("reviewed_at", -1).limit(limit).to_list(limit)
        items = []
        for workflow in workflows:
            order_number = _text(workflow.get("order_number"))
            if not order_number:
                continue
            try:
                order = await get_order(repository, user_id=merchant_id, order_number=order_number)
            except OrderNotFoundError:
                continue
            payload = order.model_dump(mode="json")
            payload.update({
                "stage": "reviewed",
                "revision": int(workflow.get("revision") or 0),
                "reviewed_at": workflow.get("reviewed_at"),
                "items": list(workflow.get("items") or []),
                "operational_items": list(workflow.get("operational_items") or []),
                "salla_admin_url": await _salla_admin_url(db, merchant_id, order_number),
            })
            items.append(payload)
        return {"items": items}

'''
if marker not in backend:
    raise SystemExit('reviewed endpoint marker missing')
backend = backend.replace(marker, insert + marker, 1)
BACKEND.write_text(backend, encoding='utf-8')

review = REVIEW.read_text(encoding='utf-8')
review = replace_once(
    review,
    '    ArrowLeft, CaretLeft, CaretRight, CheckCircle, Clipboard, Eye, EyeSlash,\n    FloppyDisk, MagnifyingGlass, Plus, SpinnerGap, WarningCircle, WhatsappLogo, X,',
    '    ArrowLeft, ArrowSquareOut, CaretLeft, CaretRight, CheckCircle, Clipboard, Eye, EyeSlash,\n    FloppyDisk, MagnifyingGlass, Plus, SpinnerGap, WarningCircle, WhatsappLogo, X,',
    'review admin icon',
)
review = replace_once(
    review,
    '    const shipping = order?.shipping || {};\n    const address = shipping.address || customer.shipping_address || {};',
    '    const shipping = order?.shipping || {};\n    const address = shipping.address || customer.shipping_address || {};\n    const sallaAdminUrl = safeReceiptUrl(order?.salla_admin_url);',
    'review admin url variable',
)
review = replace_once(
    review,
    '                            <button type="button" onClick={() => copy(order.order_number, "رقم الطلب")} className="inline-flex items-center gap-2 rounded-xl border px-3 py-2 font-bold"><Clipboard /> نسخ رقم الطلب</button>',
    '''                            <div className="flex flex-wrap gap-2">
                                {sallaAdminUrl && <a href={sallaAdminUrl} target="_blank" rel="noreferrer" data-testid="order-review-open-in-salla" className="inline-flex items-center gap-2 rounded-xl border border-teal-200 bg-teal-50 px-3 py-2 font-extrabold text-teal-900"><ArrowSquareOut /> فتح الطلب في سلة</a>}
                                <button type="button" onClick={() => copy(order.order_number, "رقم الطلب")} className="inline-flex items-center gap-2 rounded-xl border px-3 py-2 font-bold"><Clipboard /> نسخ رقم الطلب</button>
                            </div>''',
    'review admin button',
)
REVIEW.write_text(review, encoding='utf-8')

contract = CONTRACT.read_text(encoding='utf-8')
contract += '''\n\ndef test_reviewed_stage_and_salla_admin_link_are_active():\n    backend_source = (ROOT / "backend/order_review_routes.py").read_text(encoding="utf-8")\n    review_source = (ROOT / "frontend/src/pages/OrderReview.jsx").read_text(encoding="utf-8")\n    stage_source = (ROOT / "frontend/src/pages/FulfillmentV2.jsx").read_text(encoding="utf-8")\n    reviewed_source = (ROOT / "frontend/src/pages/ReviewedOrders.jsx").read_text(encoding="utf-8")\n    service_source = (ROOT / "frontend/src/services/orderReviewEngine.js").read_text(encoding="utf-8")\n\n    assert '@router.get("/reviewed")' in backend_source\n    assert 'raw_by_source.salla_direct.urls.admin' in backend_source\n    assert 'data-testid="order-review-open-in-salla"' in review_source\n    assert 'activeStage.key === "reviewed"' in stage_source\n    assert 'data-testid="reviewed-orders-stage"' in reviewed_source\n    assert 'listReviewedOrderReviews' in service_source\n'''
CONTRACT.write_text(contract, encoding='utf-8')

print('Reviewed stage and Salla link patch applied.')
