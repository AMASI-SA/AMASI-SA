from pathlib import Path

BACKEND = Path("backend/order_review_routes.py")
FRONTEND = Path("frontend/src/pages/OrderReview.jsx")
CONTRACT = Path("backend/tests/test_fulfillment_v2_contract.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, got {count}")
    return text.replace(old, new, 1)


backend = BACKEND.read_text(encoding="utf-8")
backend = replace_once(
    backend,
    '            "preparation_status": "pending",\n',
    '            "preparation_status": "in_progress",\n',
    "operational item default status",
)
backend = replace_once(
    backend,
    '        operational_items.append(operational_item)\n        new_doc = {\n',
    '''        operational_items.append(operational_item)

        # Mark linked option/custom-field keys on the source product as moved to
        # an internal operational item. Supplier/preparation exports must omit
        # these keys from the source product because their content is now owned
        # by the operational card (for example, gift-card text).
        states = _state_map(workflow)
        source_state = states.get(
            source_item.order_item_id,
            {"order_item_id": source_item.order_item_id, "review_status": "pending_review", "revision": 0},
        )
        existing_excluded = {
            _normalized(value)
            for value in source_state.get("supplier_export_excluded_spec_keys") or []
            if _text(value)
        }
        existing_excluded.update(seen_specs)
        source_state["supplier_export_excluded_spec_keys"] = sorted(existing_excluded)
        source_state["moved_to_operational_item_ids"] = list(dict.fromkeys([
            *(source_state.get("moved_to_operational_item_ids") or []),
            operational_item["operational_item_id"],
        ]))
        source_state["revision"] = int(source_state.get("revision") or 0) + 1
        source_state["updated_at"] = now
        source_state["updated_by"] = actor_id
        states[source_item.order_item_id] = source_state

        new_doc = {
''',
    "source export exclusions",
)
backend = replace_once(
    backend,
    '            "items": list((workflow or {}).get("items") or []),\n',
    '            "items": list(states.values()),\n',
    "persist source export exclusions",
)
BACKEND.write_text(backend, encoding="utf-8")

frontend = FRONTEND.read_text(encoding="utf-8")
frontend = replace_once(
    frontend,
    '    const statusLabel = { pending: "لم يبدأ", in_progress: "قيد التجهيز", ready: "جاهز" }[item.preparation_status] || "لم يبدأ";',
    '    const statusLabel = { pending: "لم يبدأ", in_progress: "قيد التجهيز", ready: "جاهز" }[item.preparation_status] || "قيد التجهيز";',
    "frontend default status label",
)
FRONTEND.write_text(frontend, encoding="utf-8")

contract = CONTRACT.read_text(encoding="utf-8")
contract += '''\n\ndef test_operational_item_starts_in_progress_and_moves_specs_out_of_supplier_export():\n    backend_source = (ROOT / "backend/order_review_routes.py").read_text(encoding="utf-8")\n    frontend_source = (ROOT / "frontend/src/pages/OrderReview.jsx").read_text(encoding="utf-8")\n\n    assert '"preparation_status": "in_progress"' in backend_source\n    assert 'supplier_export_excluded_spec_keys' in backend_source\n    assert 'moved_to_operational_item_ids' in backend_source\n    assert 'existing_excluded.update(seen_specs)' in backend_source\n    assert '|| "قيد التجهيز"' in frontend_source\n'''
CONTRACT.write_text(contract, encoding="utf-8")

print("Operational item default/export exclusions patch applied.")
