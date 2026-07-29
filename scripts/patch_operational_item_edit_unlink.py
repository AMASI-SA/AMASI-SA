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
    '''class OperationalItemStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    preparation_status: str
''',
    '''class OperationalItemStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    preparation_status: Optional[str] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
''',
    'status request model',
)
backend = replace_once(
    backend,
    '''        status_value = _normalized(payload.preparation_status)
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
''',
    '''        normalized_status = None
        if payload.preparation_status is not None:
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
        if payload.preparation_status is None and payload.name is None:
            raise HTTPException(status_code=422, detail={"code": "operational_item_update_required"})
''',
    'optional update fields',
)
backend = replace_once(
    backend,
    '''        target["preparation_status"] = normalized_status
        target["updated_at"] = _now()
''',
    '''        if normalized_status:
            target["preparation_status"] = normalized_status
        if payload.name is not None:
            target["name"] = _text(payload.name)
        target["updated_at"] = _now()
''',
    'apply optional update',
)
marker = '    @router.patch("/{order_number}/items/{order_item_id:path}")\n'
unlink = '''    @router.delete("/{order_number}/operational-items/{operational_item_id:path}")
    async def unlink_operational_item(
        order_number: str,
        operational_item_id: str,
        expected_revision: int = Query(ge=0),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        actor_id = str(reviewer["id"])
        workflow = await db[WORKFLOWS].find_one(
            {"user_id": user_id, "order_number": order_number}, {"_id": 0}
        )
        if not workflow:
            raise HTTPException(status_code=404, detail={"code": "review_workflow_not_found"})
        if workflow.get("stage") == "reviewed":
            raise HTTPException(status_code=409, detail={"code": "review_already_completed"})
        revision = int(workflow.get("revision") or 0)
        if revision != expected_revision:
            raise HTTPException(status_code=409, detail={"code": "review_revision_conflict"})

        operational_items = list(workflow.get("operational_items") or [])
        target = next((row for row in operational_items if _text(row.get("operational_item_id")) == operational_item_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail={"code": "operational_item_not_found"})
        remaining = [row for row in operational_items if _text(row.get("operational_item_id")) != operational_item_id]

        states = _state_map(workflow)
        source_item_id = _text(target.get("source_order_item_id"))
        source_state = states.get(source_item_id)
        if source_state:
            remaining_ids = [
                value for value in (source_state.get("moved_to_operational_item_ids") or [])
                if _text(value) != operational_item_id
            ]
            source_state["moved_to_operational_item_ids"] = remaining_ids
            still_excluded = {
                _normalized(spec.get("key"))
                for row in remaining
                if _text(row.get("source_order_item_id")) == source_item_id
                for spec in (row.get("linked_specs") or [])
                if isinstance(spec, dict) and _text(spec.get("key"))
            }
            source_state["supplier_export_excluded_spec_keys"] = sorted(still_excluded)
            source_state["revision"] = int(source_state.get("revision") or 0) + 1
            source_state["updated_at"] = _now()
            source_state["updated_by"] = actor_id
            states[source_item_id] = source_state

        result = await db[WORKFLOWS].update_one(
            {"user_id": user_id, "order_number": order_number, "revision": revision},
            {"$set": {
                "operational_items": remaining,
                "items": list(states.values()),
                "revision": revision + 1,
                "updated_at": _now(),
                "updated_by": actor_id,
            }},
        )
        if not result.matched_count:
            raise HTTPException(status_code=409, detail={"code": "review_revision_conflict"})
        await db[EVENTS].insert_one({
            "user_id": user_id,
            "order_number": order_number,
            "operational_item_id": operational_item_id,
            "event_type": "operational_item_unlinked",
            "returned_spec_keys": sorted(_normalized(spec.get("key")) for spec in target.get("linked_specs") or [] if isinstance(spec, dict)),
            "occurred_at": _now(),
            "actor_id": actor_id,
        })
        order = await get_order(repository, user_id=user_id, order_number=order_number)
        return await _detail(db, user_id, order)

'''
if marker not in backend:
    raise SystemExit('unlink endpoint marker missing')
backend = backend.replace(marker, unlink + marker, 1)
BACKEND.write_text(backend, encoding='utf-8')

service = SERVICE.read_text(encoding='utf-8')
service += '''\n\nexport async function unlinkOrderReviewOperationalItem(orderNumber, operationalItemId, expectedRevision) {\n    try {\n        const { data } = await api.delete(\n            `/order-reviews-v1/${encodeURIComponent(orderNumber)}/operational-items/${encodeURIComponent(operationalItemId)}`,\n            { params: { expected_revision: expectedRevision } },\n        );\n        return data;\n    } catch (error) {\n        throw new Error(message(error, "تعذّر إلغاء ربط المنتج التشغيلي."));\n    }\n}\n'''
SERVICE.write_text(service, encoding='utf-8')

frontend = FRONTEND.read_text(encoding='utf-8')
frontend = replace_once(
    frontend,
    '    updateOrderReviewItem,\n    updateOrderReviewOperationalItemStatus,',
    '    unlinkOrderReviewOperationalItem,\n    updateOrderReviewItem,\n    updateOrderReviewOperationalItemStatus,',
    'frontend unlink import',
)
frontend = replace_once(
    frontend,
    '''    const setStatus = async (preparationStatus) => {
''',
    '''    const setStatus = async (preparationStatus) => {
''',
    'status marker',
)
frontend = replace_once(
    frontend,
    '''    return (
        <article className="overflow-hidden rounded-2xl border-2 border-dashed border-amber-300 bg-amber-50/60 shadow-sm" data-testid="order-review-operational-item">''',
    '''    const rename = async () => {
        const nextName = window.prompt("اسم المنتج التشغيلي", item.name || "");
        if (!nextName || nextName.trim() === item.name) return;
        setBusy(true);
        try {
            const next = await updateOrderReviewOperationalItemStatus(orderNumber, item.operational_item_id, {
                expected_revision: workflowRevision,
                name: nextName.trim(),
            });
            onChanged(next);
            toast.success("تم تعديل اسم المنتج التشغيلي.");
        } catch (error) {
            toast.error(error.message);
        } finally {
            setBusy(false);
        }
    };
    const unlink = async () => {
        if (!window.confirm("إلغاء الربط وإرجاع الحقول إلى المنتج الأصلي؟")) return;
        setBusy(true);
        try {
            const next = await unlinkOrderReviewOperationalItem(orderNumber, item.operational_item_id, workflowRevision);
            onChanged(next);
            toast.success("تم إلغاء الربط وإرجاع الحقول إلى المنتج الأصلي.");
        } catch (error) {
            toast.error(error.message);
        } finally {
            setBusy(false);
        }
    };
    return (
        <article className="overflow-hidden rounded-2xl border-2 border-dashed border-amber-300 bg-amber-50/60 shadow-sm" data-testid="order-review-operational-item">''',
    'operational actions',
)
frontend = replace_once(
    frontend,
    '''                    <button disabled={busy || item.preparation_status === "ready"} onClick={() => setStatus("ready")} className="rounded-lg bg-emerald-600 px-3 py-2 text-xs font-extrabold text-white disabled:opacity-50">جاهز</button>
''',
    '''                    <button disabled={busy || item.preparation_status === "ready"} onClick={() => setStatus("ready")} className="rounded-lg bg-emerald-600 px-3 py-2 text-xs font-extrabold text-white disabled:opacity-50">جاهز</button>
                    <button disabled={busy} onClick={rename} className="rounded-lg border border-violet-300 bg-white px-3 py-2 text-xs font-extrabold text-violet-800 disabled:opacity-50">تعديل الاسم</button>
                    <button disabled={busy} onClick={unlink} data-testid="order-review-operational-item-unlink" className="rounded-lg border border-rose-300 bg-white px-3 py-2 text-xs font-extrabold text-rose-700 disabled:opacity-50">إلغاء الربط وإرجاع القيم</button>
''',
    'operational buttons',
)
FRONTEND.write_text(frontend, encoding='utf-8')

contract = CONTRACT.read_text(encoding='utf-8')
contract += '''\n\ndef test_operational_item_can_be_renamed_or_unlinked_before_review_completion():\n    backend_source = (ROOT / "backend/order_review_routes.py").read_text(encoding="utf-8")\n    frontend_source = (ROOT / "frontend/src/pages/OrderReview.jsx").read_text(encoding="utf-8")\n    service_source = (ROOT / "frontend/src/services/orderReviewEngine.js").read_text(encoding="utf-8")\n    assert '@router.delete("/{order_number}/operational-items/{operational_item_id:path}")' in backend_source\n    assert 'supplier_export_excluded_spec_keys' in backend_source\n    assert 'operational_item_unlinked' in backend_source\n    assert 'unlinkOrderReviewOperationalItem' in service_source\n    assert 'order-review-operational-item-unlink' in frontend_source\n    assert 'إلغاء الربط وإرجاع القيم' in frontend_source\n'''
CONTRACT.write_text(contract, encoding='utf-8')
print('Operational item edit/unlink patch applied.')
