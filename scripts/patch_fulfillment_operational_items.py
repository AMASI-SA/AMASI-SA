from pathlib import Path

BACKEND = Path('backend/order_review_routes.py')
FRONTEND = Path('frontend/src/pages/OrderReview.jsx')
SERVICE = Path('frontend/src/services/orderReviewEngine.js')
CONTRACT = Path('backend/tests/test_fulfillment_v2_contract.py')


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one match, got {count}')
    return text.replace(old, new, 1)


backend = BACKEND.read_text(encoding='utf-8')
backend = replace_once(
    backend,
    'import json\nfrom datetime import datetime, timezone',
    'import json\nimport uuid\nfrom datetime import datetime, timezone',
    'backend uuid import',
)
backend = replace_once(
    backend,
    'class CompleteReviewRequest(BaseModel):\n    model_config = ConfigDict(extra="forbid")\n    expected_revision: int = Field(ge=0)\n',
    '''class CompleteReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)


class OperationalItemCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    source_order_item_id: str = Field(min_length=1, max_length=300)
    name: str = Field(min_length=1, max_length=120)
    linked_spec_keys: list[str] = Field(default_factory=list, max_length=30)


class OperationalItemStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    preparation_status: str
''',
    'backend request models',
)
backend = replace_once(
    backend,
    '        "items": item_views,\n    }',
    '''        "items": item_views,
        "operational_items": list((workflow or {}).get("operational_items") or []),
    }''',
    'detail operational items',
)
marker = '    @router.patch("/{order_number}/items/{order_item_id:path}")\n'
insert = '''    @router.post("/{order_number}/operational-items")
    async def create_operational_item(
        order_number: str,
        payload: OperationalItemCreateRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        actor_id = str(reviewer["id"])
        await _ensure_indexes(db)
        try:
            order = await get_order(repository, user_id=user_id, order_number=order_number)
        except OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "order_not_found"}) from exc
        identities = await _review_item_identities(db, user_id, order)
        source_item = next(
            (item for item in identities if item.order_item_id == payload.source_order_item_id),
            None,
        )
        if source_item is None:
            raise HTTPException(status_code=404, detail={"code": "order_item_not_found"})

        workflow = await db[WORKFLOWS].find_one(
            {"user_id": user_id, "order_number": order.order_number}, {"_id": 0}
        )
        if (workflow or {}).get("stage") == "reviewed":
            raise HTTPException(status_code=409, detail={"code": "review_already_completed"})
        revision = int((workflow or {}).get("revision") or 0)
        if revision != payload.expected_revision:
            raise HTTPException(status_code=409, detail={"code": "review_revision_conflict"})

        selected_keys = {_normalized(value) for value in payload.linked_spec_keys if _text(value)}
        linked_specs = []
        seen_specs = set()
        for option in getattr(source_item, "options", None) or []:
            name = _text(getattr(option, "name", None))
            value = _text(getattr(option, "value", None))
            key = _normalized(name)
            if key in selected_keys and key not in seen_specs and value:
                linked_specs.append({"name": name, "value": value, "key": key})
                seen_specs.add(key)
        for name in ("color", "size", "material"):
            value = _text(getattr(source_item, name, None))
            key = _normalized(name)
            if key in selected_keys and key not in seen_specs and value:
                linked_specs.append({"name": name, "value": value, "key": key})
                seen_specs.add(key)
        for field in getattr(source_item, "custom_fields", None) or []:
            if not isinstance(field, dict):
                continue
            name = _text(field.get("name") or field.get("label") or field.get("title") or field.get("question") or field.get("key"))
            key = _normalized(name)
            if key not in selected_keys or key in seen_specs:
                continue
            raw_value = field.get("value") or field.get("answer") or field.get("selected") or field.get("choice") or field.get("text") or field.get("response")
            value = _text(raw_value.get("name") if isinstance(raw_value, dict) else raw_value)
            if value:
                linked_specs.append({"name": name, "value": value, "key": key})
                seen_specs.add(key)

        now = _now()
        operational_items = list((workflow or {}).get("operational_items") or [])
        operational_item = {
            "operational_item_id": f"op:{uuid.uuid4().hex}",
            "item_type": "internal_operational",
            "name": _text(payload.name),
            "quantity": 1,
            "source_order_item_id": source_item.order_item_id,
            "source_product_name": _text(getattr(source_item, "name", None)),
            "linked_specs": linked_specs,
            "preparation_status": "pending",
            "blocks_order_completion": True,
            "supplier_export": False,
            "salla_product": False,
            "financial_item": False,
            "created_at": now,
            "created_by": actor_id,
        }
        operational_items.append(operational_item)
        new_doc = {
            **(workflow or {}),
            "user_id": user_id,
            "order_number": order.order_number,
            "order_id": order.order_id,
            "stage": "pending_review",
            "revision": revision + 1,
            "items": list((workflow or {}).get("items") or []),
            "operational_items": operational_items,
            "updated_at": now,
            "updated_by": actor_id,
        }
        new_doc.pop("_id", None)
        if workflow:
            result = await db[WORKFLOWS].replace_one(
                {"user_id": user_id, "order_number": order.order_number, "revision": revision},
                new_doc,
            )
            if not result.matched_count:
                raise HTTPException(status_code=409, detail={"code": "review_revision_conflict"})
        else:
            new_doc["created_at"] = now
            try:
                await db[WORKFLOWS].insert_one(new_doc)
            except DuplicateKeyError as exc:
                raise HTTPException(status_code=409, detail={"code": "review_revision_conflict"}) from exc
        await db[EVENTS].insert_one({
            "user_id": user_id,
            "order_number": order.order_number,
            "operational_item_id": operational_item["operational_item_id"],
            "event_type": "operational_item_created",
            "occurred_at": now,
            "actor_id": actor_id,
        })
        return await _detail(db, user_id, order)

    @router.patch("/{order_number}/operational-items/{operational_item_id:path}")
    async def update_operational_item_status(
        order_number: str,
        operational_item_id: str,
        payload: OperationalItemStatusRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        actor_id = str(reviewer["id"])
        status_value = _normalized(payload.preparation_status)
        status_map = {
            "pending": "pending",
            "لم يبدأ": "pending",
            "in progress": "in_progress",
            "قيد التجهيز": "in_progress",
            "ready": "ready",
            "جاهز": "ready",
        }
        normalized_status = status_map.get(status_value)
        if not normalized_status:
            raise HTTPException(status_code=422, detail={"code": "invalid_operational_item_status"})
        workflow = await db[WORKFLOWS].find_one(
            {"user_id": user_id, "order_number": order_number}, {"_id": 0}
        )
        revision = int((workflow or {}).get("revision") or 0)
        if revision != payload.expected_revision:
            raise HTTPException(status_code=409, detail={"code": "review_revision_conflict"})
        operational_items = list((workflow or {}).get("operational_items") or [])
        target = next((row for row in operational_items if _text(row.get("operational_item_id")) == operational_item_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail={"code": "operational_item_not_found"})
        target["preparation_status"] = normalized_status
        target["updated_at"] = _now()
        target["updated_by"] = actor_id
        result = await db[WORKFLOWS].update_one(
            {"user_id": user_id, "order_number": order_number, "revision": revision},
            {"$set": {"operational_items": operational_items, "revision": revision + 1, "updated_at": _now(), "updated_by": actor_id}},
        )
        if not result.matched_count:
            raise HTTPException(status_code=409, detail={"code": "review_revision_conflict"})
        order = await get_order(repository, user_id=user_id, order_number=order_number)
        return await _detail(db, user_id, order)

'''
if marker not in backend:
    raise SystemExit('backend endpoint marker missing')
backend = backend.replace(marker, insert + marker, 1)
backend = replace_once(
    backend,
    '            "items": frozen_items,\n            "updated_at": now,',
    '            "items": frozen_items,\n            "operational_items": list((workflow or {}).get("operational_items") or []),\n            "updated_at": now,',
    'freeze operational items',
)
BACKEND.write_text(backend, encoding='utf-8')

service = SERVICE.read_text(encoding='utf-8')
service += '''\n\nexport async function createOrderReviewOperationalItem(orderNumber, payload) {\n    try {\n        const { data } = await api.post(\n            `/order-reviews-v1/${encodeURIComponent(orderNumber)}/operational-items`,\n            payload,\n        );\n        return data;\n    } catch (error) {\n        throw new Error(message(error, "تعذّر إضافة المنتج التشغيلي."));\n    }\n}\n\nexport async function updateOrderReviewOperationalItemStatus(orderNumber, operationalItemId, payload) {\n    try {\n        const { data } = await api.patch(\n            `/order-reviews-v1/${encodeURIComponent(orderNumber)}/operational-items/${encodeURIComponent(operationalItemId)}`,\n            payload,\n        );\n        return data;\n    } catch (error) {\n        throw new Error(message(error, "تعذّر تحديث حالة المنتج التشغيلي."));\n    }\n}\n'''
SERVICE.write_text(service, encoding='utf-8')

frontend = FRONTEND.read_text(encoding='utf-8')
frontend = replace_once(
    frontend,
    '    ArrowLeft, CaretLeft, CaretRight, CheckCircle, Clipboard, Eye, EyeSlash,\n    FloppyDisk, MagnifyingGlass, SpinnerGap, WarningCircle, WhatsappLogo, X,',
    '    ArrowLeft, CaretLeft, CaretRight, CheckCircle, Clipboard, Eye, EyeSlash,\n    FloppyDisk, MagnifyingGlass, Plus, SpinnerGap, WarningCircle, WhatsappLogo, X,',
    'frontend plus icon',
)
frontend = replace_once(
    frontend,
    '    completeOrderReview,\n    getOrderReview,\n    listPendingOrderReviews,\n    updateOrderReviewItem,',
    '    completeOrderReview,\n    createOrderReviewOperationalItem,\n    getOrderReview,\n    listPendingOrderReviews,\n    updateOrderReviewItem,\n    updateOrderReviewOperationalItemStatus,',
    'frontend service imports',
)
frontend = replace_once(
    frontend,
    'function ProductReviewCard({ item, workflowRevision, orderNumber, onChanged }) {',
    'function ProductReviewCard({ item, workflowRevision, orderNumber, onChanged, onCreateOperationalItem }) {',
    'product card props',
)
frontend = replace_once(
    frontend,
    '    const gallery = Array.from(new Set((item.gallery || []).filter(Boolean)));',
    '    const gallery = Array.from(new Set((item.gallery || []).filter(Boolean))).filter((url) => url !== item.selected_image_url);',
    'dedupe selected image thumbnail',
)
frontend = replace_once(
    frontend,
    '                <div className="flex flex-wrap gap-2">',
    '''                <div className="flex flex-wrap gap-2">
                    <button
                        type="button"
                        onClick={() => onCreateOperationalItem(item, specs)}
                        className="inline-flex items-center gap-1.5 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs font-extrabold text-amber-900 hover:bg-amber-100"
                    >
                        <Plus size={15} /> إضافة منتج تشغيلي
                    </button>''',
    'operational button',
)
operational_component = '''\nfunction OperationalItemCard({ item, workflowRevision, orderNumber, onChanged }) {\n    const [busy, setBusy] = useState(false);\n    const statusLabel = { pending: "لم يبدأ", in_progress: "قيد التجهيز", ready: "جاهز" }[item.preparation_status] || "لم يبدأ";\n    const setStatus = async (preparationStatus) => {\n        setBusy(true);\n        try {\n            const next = await updateOrderReviewOperationalItemStatus(orderNumber, item.operational_item_id, {\n                expected_revision: workflowRevision,\n                preparation_status: preparationStatus,\n            });\n            onChanged(next);\n            toast.success("تم تحديث حالة المنتج التشغيلي.");\n        } catch (error) {\n            toast.error(error.message);\n        } finally {\n            setBusy(false);\n        }\n    };\n    return (\n        <article className="overflow-hidden rounded-2xl border-2 border-dashed border-amber-300 bg-amber-50/60 shadow-sm" data-testid="order-review-operational-item">\n            <div className="p-4">\n                <div className="flex items-start justify-between gap-3">\n                    <div>\n                        <div className="text-xs font-extrabold text-amber-700">منتج تشغيلي داخلي</div>\n                        <h3 className="mt-1 text-lg font-extrabold text-slate-900">{item.name}</h3>\n                        <div className="mt-1 text-xs text-slate-500">مرتبط بـ: {item.source_product_name || "منتج الطلب"}</div>\n                    </div>\n                    <span className="rounded-full bg-white px-3 py-1 text-xs font-extrabold text-amber-800">{statusLabel}</span>\n                </div>\n                <div className="mt-4 grid gap-2">\n                    {(item.linked_specs || []).map((spec) => (\n                        <div key={`${spec.key}:${spec.value}`} className="rounded-xl bg-white px-3 py-2 text-sm">\n                            <span className="font-bold text-violet-700">{spec.name}: </span>\n                            <span className="whitespace-pre-wrap break-words font-extrabold text-slate-900">{spec.value}</span>\n                        </div>\n                    ))}\n                </div>\n                <div className="mt-4 rounded-xl bg-white/80 p-3 text-xs font-bold leading-6 text-slate-600">\n                    يظهر داخل ميزان فقط، لا يُرسل إلى سلة أو قيود ولا يدخل ملف المورد. ويمنع اكتمال التجهيز حتى يصبح جاهزًا.\n                </div>\n                <div className="mt-3 flex flex-wrap gap-2">\n                    <button disabled={busy || item.preparation_status === "in_progress"} onClick={() => setStatus("in_progress")} className="rounded-lg border border-amber-300 bg-white px-3 py-2 text-xs font-extrabold text-amber-900 disabled:opacity-50">قيد التجهيز</button>\n                    <button disabled={busy || item.preparation_status === "ready"} onClick={() => setStatus("ready")} className="rounded-lg bg-emerald-600 px-3 py-2 text-xs font-extrabold text-white disabled:opacity-50">جاهز</button>\n                </div>\n            </div>\n        </article>\n    );\n}\n\n'''
frontend = replace_once(frontend, '\nfunction ReviewDrawer({ orderNumber, onClose, onCompleted }) {', '\n' + operational_component + 'function ReviewDrawer({ orderNumber, onClose, onCompleted }) {', 'operational component')
frontend = replace_once(
    frontend,
    '    const [completing, setCompleting] = useState(false);',
    '''    const [completing, setCompleting] = useState(false);
    const [operationalDialog, setOperationalDialog] = useState(null);
    const [operationalName, setOperationalName] = useState("كرت إهداء");
    const [linkedSpecKeys, setLinkedSpecKeys] = useState([]);
    const [creatingOperational, setCreatingOperational] = useState(false);''',
    'drawer operational state',
)
frontend = replace_once(
    frontend,
    '    const finish = async () => {',
    '''    const openOperationalDialog = (item, specs) => {
        setOperationalDialog({ item, specs });
        setOperationalName("كرت إهداء");
        setLinkedSpecKeys(specs.map((spec) => spec.key));
    };

    const createOperational = async () => {
        if (!operationalDialog || !operationalName.trim()) return;
        setCreatingOperational(true);
        try {
            const next = await createOrderReviewOperationalItem(orderNumber, {
                expected_revision: detail.revision,
                source_order_item_id: operationalDialog.item.order_item_id,
                name: operationalName.trim(),
                linked_spec_keys: linkedSpecKeys,
            });
            setDetail(next);
            setOperationalDialog(null);
            toast.success("تمت إضافة المنتج التشغيلي وربط بياناته.");
        } catch (error) {
            toast.error(error.message);
        } finally {
            setCreatingOperational(false);
        }
    };

    const finish = async () => {''',
    'drawer operational handlers',
)
frontend = replace_once(
    frontend,
    '<ProductReviewCard key={item.order_item_id} item={item} workflowRevision={detail.revision} orderNumber={orderNumber} onChanged={setDetail} />',
    '<ProductReviewCard key={item.order_item_id} item={item} workflowRevision={detail.revision} orderNumber={orderNumber} onChanged={setDetail} onCreateOperationalItem={openOperationalDialog} />',
    'product card wiring',
)
frontend = replace_once(
    frontend,
    '                            </div>\n                        </section>\n\n                        <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4">',
    '''                            </div>
                        </section>

                        {(detail.operational_items || []).length > 0 && (
                            <section>
                                <div className="mb-3 flex items-center justify-between"><h3 className="text-xl font-extrabold">المنتجات التشغيلية الداخلية</h3><span className="rounded-full bg-amber-100 px-3 py-1 text-sm font-bold text-amber-900">{detail.operational_items.length} منتج</span></div>
                                <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
                                    {detail.operational_items.map((item) => (
                                        <OperationalItemCard key={item.operational_item_id} item={item} workflowRevision={detail.revision} orderNumber={orderNumber} onChanged={setDetail} />
                                    ))}
                                </div>
                            </section>
                        )}

                        <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4">''',
    'operational section',
)
frontend = replace_once(
    frontend,
    '                )}\n            </section>\n        </div>',
    '''                )}
                {operationalDialog && (
                    <div className="fixed inset-0 z-[95] flex items-center justify-center bg-slate-950/55 p-4" dir="rtl">
                        <div className="w-full max-w-xl rounded-2xl bg-white p-5 shadow-2xl">
                            <div className="flex items-center justify-between gap-3"><h3 className="text-xl font-extrabold">إضافة منتج تشغيلي</h3><button type="button" onClick={() => setOperationalDialog(null)} className="rounded-lg border p-2"><X /></button></div>
                            <p className="mt-2 text-sm text-slate-500">أنشئ منتجًا داخليًا واربط به الخيارات أو النصوص المطلوبة من المنتج الأصلي.</p>
                            <label className="mt-4 block"><span className="mb-1 block text-sm font-extrabold">اسم المنتج الداخلي</span><input value={operationalName} onChange={(event) => setOperationalName(event.target.value)} maxLength={120} className="w-full rounded-xl border border-slate-200 p-3 outline-none focus:border-amber-500" /></label>
                            <div className="mt-4"><div className="mb-2 text-sm font-extrabold">البيانات التي تنتقل إليه</div><div className="grid gap-2">{operationalDialog.specs.map((spec) => (<label key={spec.key} className="flex items-start gap-3 rounded-xl bg-violet-50 p-3"><input type="checkbox" checked={linkedSpecKeys.includes(spec.key)} onChange={(event) => setLinkedSpecKeys((current) => event.target.checked ? [...new Set([...current, spec.key])] : current.filter((key) => key !== spec.key))} className="mt-1" /><span><b className="text-violet-700">{spec.name}:</b> <span className="whitespace-pre-wrap break-words font-bold">{spec.value}</span></span></label>))}</div></div>
                            <div className="mt-5 flex justify-end gap-2"><button type="button" onClick={() => setOperationalDialog(null)} className="rounded-xl border px-4 py-2 font-bold">إلغاء</button><button type="button" disabled={creatingOperational || !operationalName.trim()} onClick={createOperational} className="inline-flex items-center gap-2 rounded-xl bg-amber-600 px-4 py-2 font-extrabold text-white disabled:opacity-50">{creatingOperational ? <SpinnerGap className="animate-spin" /> : <Plus />} إضافة وربط</button></div>
                        </div>
                    </div>
                )}
            </section>
        </div>''',
    'operational dialog',
)
FRONTEND.write_text(frontend, encoding='utf-8')

contract = CONTRACT.read_text(encoding='utf-8')
contract += '''\n\ndef test_review_supports_internal_operational_items_without_supplier_export():\n    backend_source = (ROOT / "backend/order_review_routes.py").read_text(encoding="utf-8")\n    frontend_source = (ROOT / "frontend/src/pages/OrderReview.jsx").read_text(encoding="utf-8")\n    service_source = (ROOT / "frontend/src/services/orderReviewEngine.js").read_text(encoding="utf-8")\n\n    assert '"/{order_number}/operational-items"' in backend_source\n    assert '"item_type": "internal_operational"' in backend_source\n    assert '"supplier_export": False' in backend_source\n    assert '"financial_item": False' in backend_source\n    assert '"salla_product": False' in backend_source\n    assert '"blocks_order_completion": True' in backend_source\n    assert 'createOrderReviewOperationalItem' in service_source\n    assert 'data-testid="order-review-operational-item"' in frontend_source\n    assert 'إضافة منتج تشغيلي' in frontend_source\n    assert '.filter((url) => url !== item.selected_image_url)' in frontend_source\n'''
CONTRACT.write_text(contract, encoding='utf-8')

print('Fulfillment operational items patch applied.')
