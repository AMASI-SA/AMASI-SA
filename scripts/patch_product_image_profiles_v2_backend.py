from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
backend = ROOT / "backend/product_v2_details_routes.py"
service = ROOT / "frontend/src/services/mezanProductsV2.js"

text = backend.read_text(encoding="utf-8")
svc = service.read_text(encoding="utf-8")

if 'IMAGE_PROFILES = "mezan_product_image_profiles_v2"' not in text:
    text = text.replace(
        'DETAIL_LOG = "mezan_product_detail_log_v2"\n',
        'DETAIL_LOG = "mezan_product_detail_log_v2"\nIMAGE_PROFILES = "mezan_product_image_profiles_v2"\nIMAGE_RULE_AUDIT = "mezan_product_image_rule_audit_v2"\n',
    )

if 'def _normalize_image_rules' not in text:
    marker = '\n\nasync def ensure_detail_indexes(db: Any) -> None:\n'
    helper = '''\n\ndef _normalize_image_rules(payload: Any, product: dict[str, Any]) -> list[dict[str, Any]]:\n    rows = payload if isinstance(payload, list) else []\n    valid_images = {_text(row.get("url")) for row in (product.get("images") or []) if isinstance(row, dict) and _text(row.get("url"))}\n    valid_images.add(_text(product.get("main_image")))\n    valid_images.discard("")\n    option_map = {str(option.get("id")): {str(value.get("id")) for value in (option.get("values") or []) if isinstance(value, dict)} for option in (product.get("options") or []) if isinstance(option, dict) and option.get("id") is not None}\n    result = []\n    signatures = set()\n    for index, row in enumerate(rows):\n        if not isinstance(row, dict):\n            continue\n        image_url = _text(row.get("image_url"))\n        if image_url not in valid_images:\n            raise HTTPException(status_code=422, detail={"code": "product_image_rule_invalid_image", "index": index})\n        conditions = []\n        for condition in (row.get("conditions") or []):\n            if not isinstance(condition, dict):\n                continue\n            option_id = _text(condition.get("option_id"))\n            value_id = _text(condition.get("value_id"))\n            if option_id not in option_map or value_id not in option_map[option_id]:\n                raise HTTPException(status_code=422, detail={"code": "product_image_rule_invalid_option", "index": index})\n            conditions.append({"option_id": option_id, "value_id": value_id, "option_name": _text(condition.get("option_name")) or None, "value_name": _text(condition.get("value_name")) or None})\n        conditions.sort(key=lambda item: (item["option_id"], item["value_id"]))\n        signature = tuple((item["option_id"], item["value_id"]) for item in conditions)\n        if not signature:\n            raise HTTPException(status_code=422, detail={"code": "product_image_rule_conditions_required", "index": index})\n        if signature in signatures:\n            raise HTTPException(status_code=409, detail={"code": "product_image_rule_duplicate_conditions", "index": index})\n        signatures.add(signature)\n        result.append({"id": _text(row.get("id")) or uuid.uuid4().hex, "image_url": image_url, "conditions": conditions, "enabled": row.get("enabled") is not False, "specificity": len(conditions)})\n    return result\n'''
    if marker not in text:
        raise SystemExit("Backend insertion point not found")
    text = text.replace(marker, helper + marker)

old_index = 'await db[COST_PROFILES].create_index([("user_id", ASCENDING), ("salla_product_id", ASCENDING)], unique=True, name="uq_product_cost_profile_v2")'
new_index = old_index + '\n    await db[IMAGE_PROFILES].create_index([("user_id", ASCENDING), ("salla_product_id", ASCENDING)], unique=True, name="uq_product_image_profile_v2")'
if 'uq_product_image_profile_v2' not in text:
    text = text.replace(old_index, new_index)

if '@router.get("/{product_id}/image-profile")' not in text:
    marker = '    @router.get("/{product_id}/costs")\n'
    routes = '''    @router.get("/{product_id}/image-profile")\n    async def get_product_image_profile(product_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:\n        await ensure_detail_indexes(db)\n        user_id = str(user["id"])\n        product = await db[PRODUCTS].find_one({"user_id": user_id, "$or": [{"id": product_id}, {"mezan_product_id": product_id}, {"salla_product_id": product_id}]}, {"_id": 0, "salla_product_id": 1, "main_image": 1, "images": 1, "options": 1})\n        if not product:\n            raise HTTPException(status_code=404, detail={"code": "product_v2_not_found"})\n        profile = await db[IMAGE_PROFILES].find_one({"user_id": user_id, "salla_product_id": str(product["salla_product_id"])}, {"_id": 0}) or {}\n        return {"salla_product_id": str(product["salla_product_id"]), "mezan_default_image_url": profile.get("mezan_default_image_url"), "rules": profile.get("rules") or [], "images": product.get("images") or [], "main_image": product.get("main_image"), "options": product.get("options") or []}\n\n    @router.put("/{product_id}/image-profile")\n    async def save_product_image_profile(product_id: str, payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:\n        await ensure_detail_indexes(db)\n        user_id = str(user["id"])\n        product = await db[PRODUCTS].find_one({"user_id": user_id, "$or": [{"id": product_id}, {"mezan_product_id": product_id}, {"salla_product_id": product_id}]}, {"_id": 0, "salla_product_id": 1, "main_image": 1, "images": 1, "options": 1})\n        if not product:\n            raise HTTPException(status_code=404, detail={"code": "product_v2_not_found"})\n        valid_images = {_text(row.get("url")) for row in (product.get("images") or []) if isinstance(row, dict)}\n        valid_images.add(_text(product.get("main_image")))\n        valid_images.discard("")\n        default_url = _text(payload.get("mezan_default_image_url")) or None\n        if default_url and default_url not in valid_images:\n            raise HTTPException(status_code=422, detail={"code": "product_image_default_invalid"})\n        rules = _normalize_image_rules(payload.get("rules"), product)\n        now = _now()\n        await db[IMAGE_PROFILES].update_one({"user_id": user_id, "salla_product_id": str(product["salla_product_id"])}, {"$set": {"user_id": user_id, "salla_product_id": str(product["salla_product_id"]), "mezan_default_image_url": default_url, "rules": rules, "updated_by": user_id, "updated_at": now}, "$setOnInsert": {"created_at": now}}, upsert=True)\n        await db[IMAGE_RULE_AUDIT].insert_one({"id": uuid.uuid4().hex, "user_id": user_id, "salla_product_id": str(product["salla_product_id"]), "event_type": "image_profile_saved", "rules_count": len(rules), "occurred_at": now})\n        return {"ok": True, "mezan_default_image_url": default_url, "rules": rules}\n\n'''
    if marker not in text:
        raise SystemExit("Route insertion point not found")
    text = text.replace(marker, routes + marker)

if 'getProductImageProfile' not in svc:
    svc += '\nexport async function getProductImageProfile(productId) { return (await api.get(`/products-v2/${encodeURIComponent(productId)}/image-profile`)).data; }\nexport async function saveProductImageProfile(productId, payload) { return (await api.put(`/products-v2/${encodeURIComponent(productId)}/image-profile`, payload)).data; }\n'

backend.write_text(text, encoding="utf-8")
service.write_text(svc, encoding="utf-8")
print("Product image profiles V2 backend patch applied.")
