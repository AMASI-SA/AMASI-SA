"""rev38.1 — Single-SKU Product Fix (mada canary prep, order 270513107).

Scope-locked tool for binding ONE Salla SKU to its real قيود product:

  • plan  (GET)  — READ-ONLY: current mapping + external snapshot +
    LIVE قيود search by SKU (GET only) + create-payload preview.
  • adopt (POST) — DB-ONLY write: upserts qoyod_products_mapping via
    the existing audited `adopt_qoyod_product`. Refuses when zero or
    ≥2 real قيود matches. NEVER POSTs to قيود.

NO invoice send, no reprocess, no run-now, no canary arming here.
If the product does not exist in قيود at all, this tool only RETURNS
the exact create payload the pipeline's product resolver would POST —
actual creation happens inside the approved canary send (all gates +
budget) or manually by the operator in قيود UI followed by adopt.
"""
from __future__ import annotations

from integrations.qoyod.dry_rca_report import (
    _find_real_product_in_external, _find_real_product_mapping,
)
from integrations.qoyod.product_resolver import (
    _build_product_payload, adopt_qoyod_product,
)


def _is_real_id(v) -> bool:
    s = str(v or "")
    return bool(s) and not s.upper().startswith(("DRY:", "PREVIEW:"))


async def _order_item_for_sku(db, user_id: str, sku: str) -> dict | None:
    row = await db.integration_inbox.find_one(
        {"user_id": user_id, "canonical_payload.items.sku": sku},
        {"_id": 0, "canonical_payload.items": 1,
         "salla_order_number": 1},
        sort=[("received_at", -1)])
    if not row:
        return None
    for it in (row.get("canonical_payload") or {}).get("items") or []:
        if (it.get("sku") or "").strip() == sku:
            return {**it, "_order_number": row.get("salla_order_number")}
    return None


async def build_product_fix_plan(
    db, *, user_id: str, sku: str, api_client,
) -> dict:
    """READ-ONLY plan. `api_client` is used for GET search only."""
    sku = sku.strip()
    mapping = await _find_real_product_mapping(db, user_id, sku)
    external = await _find_real_product_in_external(db, user_id, sku)

    live_matches: list[dict] = []
    live_search_error = None
    try:
        # find_all_products_by_sku swallows QoyodAPIError and returns
        # [] — indistinguishable from "product truly absent". Probe
        # auth first so a 401/500 becomes `search_failed`, never a
        # false "create_needed".
        await api_client.me()
        rows = await api_client.find_all_products_by_sku(sku, limit=10)
        for p in rows or []:
            live_matches.append({
                "qoyod_product_id": str(p.get("id") or ""),
                "name":             p.get("name_ar") or p.get("name"),
                "sku":              p.get("sku"),
            })
    except Exception as exc:
        live_search_error = f"{type(exc).__name__}: {exc}"

    item = await _order_item_for_sku(db, user_id, sku)
    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}
    create_payload_preview = (
        _build_product_payload(item, settings) if item else None)

    if mapping:
        action, detail = "none", (
            f"SKU مربوط مسبقاً بمنتج قيود حقيقي "
            f"(#{mapping['qoyod_product_id']}) — لا حاجة لأي إجراء")
        adopt_candidate = None
    elif len(live_matches) == 1:
        adopt_candidate = live_matches[0]
        action = "adopt"
        detail = (f"وُجد منتج واحد مطابق في قيود "
                  f"(#{adopt_candidate['qoyod_product_id']} — "
                  f"{adopt_candidate['name']}) — اربطه عبر adopt")
    elif len(live_matches) >= 2:
        adopt_candidate = None
        action = "ambiguous"
        detail = (f"يوجد {len(live_matches)} منتجات بنفس SKU في قيود — "
                  "الربط مرفوض حتى تنظيف التكرار يدوياً في قيود")
    elif external:
        adopt_candidate = {
            "qoyod_product_id": str(external["qoyod_product_id"]),
            "name": external.get("name"), "sku": sku}
        action = "adopt"
        detail = (f"وُجد في لقطة المزامنة الأولية "
                  f"(#{external['qoyod_product_id']}) — اربطه عبر adopt")
    elif live_search_error:
        adopt_candidate = None
        action = "search_failed"
        detail = ("تعذر البحث المباشر في قيود — "
                  f"{live_search_error}")
    else:
        adopt_candidate = None
        action = "create_needed"
        detail = ("المنتج غير موجود في قيود إطلاقاً — الإنشاء سيتم "
                  "داخل الإرسال المعتمد (بكل البوابات والميزانية) أو "
                  "أنشئه يدوياً في قيود ثم أعد الخطة واربطه")

    return {
        "ok": True, "sku": sku,
        "current_mapping": mapping,
        "external_snapshot_match": external,
        "qoyod_live_matches": live_matches,
        "qoyod_live_search_error": live_search_error,
        "order_item": item,
        "create_payload_preview": create_payload_preview,
        "recommended_action": action,
        "adopt_candidate": adopt_candidate,
        "detail": detail,
        "read_only": True,
        "qoyod_calls": "GET search only — no writes",
    }


async def execute_product_fix_adopt(
    db, *, user_id: str, sku: str, api_client,
    confirm_token: str, actor: str,
) -> dict:
    """DB-ONLY adopt. Requires confirm_token == 'ADOPT-{sku}'."""
    sku = sku.strip()
    if confirm_token != f"ADOPT-{sku}":
        return {"ok": False, "reason": "bad_confirm_token",
                "expected_token": f"ADOPT-{sku}"}
    plan = await build_product_fix_plan(
        db, user_id=user_id, sku=sku, api_client=api_client)
    if plan["recommended_action"] == "none":
        return {"ok": True, "already_mapped": True,
                "mapping": plan["current_mapping"], "plan": plan}
    if plan["recommended_action"] != "adopt":
        return {"ok": False,
                "reason": f"adopt_refused_{plan['recommended_action']}",
                "plan": plan}
    cand = plan["adopt_candidate"]
    result = await adopt_qoyod_product(
        db, user_id=user_id, sku=sku,
        qoyod_product_id=cand["qoyod_product_id"],
        qoyod_product_name=cand.get("name"),
        note="rev38.1 single-SKU fix (mada canary prep)",
        actor=actor)
    return {"ok": bool(result.get("ok")),
            "adopted_qoyod_product_id": cand["qoyod_product_id"],
            "adopt_result": result,
            "db_write_only": True, "no_qoyod_writes": True}
