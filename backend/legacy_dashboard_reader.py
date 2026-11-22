"""Canonical dashboard calculation without application bootstrap or background jobs.

The HTTP adapter and demand worker inject their own database. Formulae and source
read boundaries remain shared; demand jobs disable self-heal and settings writes.
"""
from copy import deepcopy as _dashboard_deepcopy
from datetime import datetime, timezone
import logging
from typing import Optional

from pydantic import BaseModel
from auth import ensure_user_settings, DEFAULT_PAYMENT_METHODS, DEFAULT_SHIPPING_COMPANIES
from balances import compute_balances
from dashboard_cpu_budget import CPUWorkBudget, PARSER_BUDGET_MS
from excel_parser import match_settings
from expenses_routes import compute_operating_expenses_for_range
from order_currency import (SALLA_RAW_CURRENCY_PROJECTION, hydrate_order_currency_fields_cooperative,
                            order_total_sar, summarize_orders_sar)
from orders_db import orders_to_parsed_cooperative
from salla_marketing_attribution import SALLA_RAW_ATTRIBUTION_PROJECTION, attach_projected_salla_attribution
from settlements_routes import aggregate_settlements_by_provider, classify_14d_window

logger = logging.getLogger("mezan.dashboard")

class NetSalesConfig(BaseModel):
    """Phase 3 — controls which line items get subtracted from total_sales
    when computing the "صافي المبيعات" (net sales) KPI shown in dashboard.

    Each flag is independent so the merchant can model their accounting
    preference (e.g. some count VAT as part of revenue, others don't).
    Defaults reflect the most common Salla seller workflow."""
    deduct_payment_fees: bool = True       # عمولات بوابات الدفع
    deduct_shipping: bool = True           # تكاليف الشحن (الفورية فقط)
    deduct_deferred_shipping: bool = False  # تكاليف الشحن الآجلة
    deduct_ads: bool = True                # تكاليف الإعلانات اليومية
    deduct_product_costs: bool = True      # تكاليف المنتجات
    deduct_vat: bool = False               # ضريبة القيمة المضافة
    deduct_daily_expenses: bool = False    # مصاريف يومية أخرى
    deduct_operating_expenses: bool = True  # المصروفات التشغيلية (رواتب + إيجارات + يومية أخرى)

DEFAULT_NET_SALES_CONFIG = NetSalesConfig().model_dump()

DEFAULT_ELECTRONIC_NET_EXCLUDED_STATUSES: list[str] = [
    "ملغ",          # ملغي / ملغية / تم الإلغاء
    "مسترد",        # مسترد / تم الاسترداد
    "مرتجع",        # مرتجع / تم الإرجاع
    "فشل",          # فشل الدفع
    "مرفوض",        # مرفوض من البوابة
    "بانتظار الدفع",  # لم يتم استلام المبلغ بعد
    "cancel",       # cancelled / canceled
    "refund",       # refunded
    "fail",         # failed
    "reject",       # rejected
    "pending payment",
]

DEFAULT_SHIPPING_APPROVED = ["تم التوصيل", "delivered", "completed", "تم الاستلام"]

DEFAULT_COD_APPROVED = ["تم التوصيل", "delivered", "completed"]

def _parse_date_or(s: Optional[str], fallback):
    """Parse YYYY-MM-DD to a date object, falling back when invalid/empty."""
    if not s:
        return fallback
    try:
        return datetime.strptime(str(s).strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return fallback

def _is_excluded_for_electronic_net(status: str, excluded_terms: list[str]) -> bool:
    """Return True when the order status matches ANY of the excluded terms.

    Matching is case-insensitive substring — captures "ملغ" → "تم إلغاء الطلب",
    "fail" → "Payment Failed", etc. Empty status is NOT excluded (kept for
    backwards compatibility with orders that lack a status field).
    """
    if not status:
        return False
    s = status.strip().lower()
    for t in excluded_terms:
        if t and t.strip().lower() in s:
            return True
    return False

async def _spend_by_date_from_ledger(
    db, uid: str, providers: tuple, start: str, end: str,
) -> dict[str, float]:
    """Return {date_iso: total_spend} aggregated from ad_account_ledger
    for the given user, ad_provider aliases, and date window.

    Joins `ad_account_ledger.counterparty_id` → `counterparties.id` to
    filter by `ad_provider`. Returns ONE row per calendar date with
    the sum across all accounts of that provider for that user.
    """
    if not providers:
        return {}
    # Resolve counterparty ids matching any of these provider aliases.
    cps = await db.counterparties.find(
        {"user_id": uid, "kind": "ad_account",
         "ad_provider": {"$in": list(providers)}},
        {"_id": 0, "id": 1},
    ).to_list(200)
    cp_ids = [c["id"] for c in cps]
    if not cp_ids:
        return {}
    pipeline = [
        {"$match": {
            "user_id": uid,
            "counterparty_id": {"$in": cp_ids},
            "type": "spend",
            "date": {"$gte": start, "$lte": end},
        }},
        {"$group": {
            "_id": "$date",
            "spend": {"$sum": "$amount"},
        }},
    ]
    out: dict[str, float] = {}
    async for row in db.ad_account_ledger.aggregate(pipeline):
        out[row["_id"]] = float(row.get("spend") or 0)
    return out


async def dashboard(user=None, from_date=None, to_date=None, payment_methods=None,
                    shipping_companies=None, include_legacy_analyses=True, allow_self_heal=True,
                    report_statuses=None, *, database=None, owner_id=None, persist_settings=True, fee_context=None):
    """Shared dashboard calculation for the signed-in account or an authorized tenant report."""
    source_db = database
    if source_db is None:
        raise ValueError("dashboard_database_required")
    if owner_id is None:
        owner_id = user["id"]
    pm_list = [s.strip() for s in (payment_methods or "").split(",") if s.strip()]
    ship_list = [s.strip() for s in (shipping_companies or "").split(",") if s.strip()]

    def _matches_any(value: str, allowed: list[str]) -> bool:
        if not allowed:
            return True
        v = (value or "").strip().lower()
        for a in allowed:
            a_lc = a.strip().lower()
            if a_lc and (a_lc == v or a_lc in v or v in a_lc):
                return True
        return False

    settings = (await ensure_user_settings(source_db, owner_id) if persist_settings
                else await ensure_user_settings(source_db, owner_id, persist_defaults=False))
    from dashboard_payment_fees import (
        BASIS as PAYMENT_FEE_BASIS, FEE_SOURCE_PROJECTION, attach_fee_source_evidence,
        dashboard_fee_breakdown, dashboard_payment_method, load_dashboard_fee_context, enforce_dashboard_fee_completeness,
        merge_statement_estimate_metadata,
    )

    # ── Unified orders aggregation (THE source of truth) ─────────────────────
    orders_q = {"user_id": owner_id}
    if from_date or to_date:
        orders_q["order_date"] = {}
        if from_date:
            orders_q["order_date"]["$gte"] = from_date
        if to_date:
            orders_q["order_date"]["$lte"] = to_date
    # User opt-in: exclude orders whose date was inferred (Make.com webhook
    # without created_at). When enabled, only authoritatively-dated orders
    # are counted in dashboard KPIs.
    if settings.get("hide_inferred_date_orders"):
        orders_q["order_date_inferred"] = {"$ne": True}

    all_orders = await source_db.unified_orders.find(
        orders_q, {"_id": 0, "raw_by_source": 0}
    ).to_list(100000)
    if all_orders:
        raw_projection = dict(SALLA_RAW_CURRENCY_PROJECTION)
        raw_projection.update(SALLA_RAW_ATTRIBUTION_PROJECTION)
        raw_projection.update(FEE_SOURCE_PROJECTION)
        projected_rows = await source_db.unified_orders.find(
            orders_q,
            raw_projection,
        ).to_list(100000)
        await hydrate_order_currency_fields_cooperative(
            all_orders, projected_rows, checkpoint=CPUWorkBudget(PARSER_BUDGET_MS).checkpoint,
        )
        attach_projected_salla_attribution(all_orders, projected_rows)
        attach_fee_source_evidence(all_orders, projected_rows)

    # Iteration 31: data_source self-heal. Past orders whose data_source
    # was demoted to "excel" by Excel re-imports (pre-iteration-31 bug)
    # are re-promoted to "make" whenever their data_sources[] history
    # contains any Make write. This corrects historical bucketing
    # WITHOUT requiring a manual recompute or migration script.
    ds_promoted = 0
    if allow_self_heal:
        for o in all_orders:
            if o.get("data_source") == "excel":
                history = o.get("data_sources") or []
                if any((s or {}).get("source") == "make" for s in history):
                    o["data_source"] = "make"
                    ds_promoted += 1
                    # Persist for next request so we don't repeat the work.
                    try:
                        await source_db.unified_orders.update_one(
                            {"user_id": owner_id, "order_number": o["order_number"]},
                            {"$set": {"data_source": "make"}},
                        )
                    except Exception:
                        pass  # never fail dashboard on heal
        if ds_promoted:
            logger.info("Dashboard: promoted %d orders excel→make", ds_promoted)

    # Iteration 27: lazy self-heal. Any order in the filtered range
    # whose `total_product_cost` is still null/missing → re-run
    # attach_cost_to_order_doc. This guarantees the Dashboard always
    # reflects the latest cost data, even for orders that arrived
    # before a cost was added (or on environments without the
    # iteration-26 auto-recompute hooks). Idempotent + only touches
    # stale rows so it's effectively free when data is healthy.
    if allow_self_heal:
        try:
            from product_costs import attach_cost_to_order_doc as _attach_pc
            stale_indexes: list[int] = []
            for i, o in enumerate(all_orders):
                if o.get("total_product_cost") is None:
                    stale_indexes.append(i)
            for i in stale_indexes[:500]:  # cap heal-per-request for safety
                o = all_orders[i]
                patch = await _attach_pc(source_db, owner_id, o)
                await source_db.unified_orders.update_one(
                    {"user_id": owner_id, "order_number": o["order_number"]},
                    {"$set": patch},
                )
                o.update(patch)  # refresh in-memory copy so totals use new values
        except Exception as _exc:
            logger.warning("Dashboard cost self-heal skipped: %s", _exc)

    if pm_list or ship_list:
        all_orders = [
            o for o in all_orders
            if _matches_any(o.get("payment_method", ""), pm_list)
            and _matches_any(o.get("shipping_company", ""), ship_list)
        ]

    # Apply user-configured "report_included_statuses" filter:
    # if non-empty, only orders whose order_status matches any of the configured
    # statuses (case-insensitive partial match) are counted in dashboard KPIs.
    included_statuses = settings.get("report_included_statuses") or []
    # Iter-207c — Snapshot the Salla-reference universe BEFORE the
    # status filter so the UI can render a transparency badge:
    #   "+X طلب معلَّق/ملغى بقيمة Y ر.س"
    salla_ref_orders_count = len(all_orders)
    salla_ref_currency = summarize_orders_sar(all_orders)
    salla_ref_gross = salla_ref_currency["total_sar"]
    if report_statuses is not None:
        from sold_financial_report import validate_report_statuses
        from sold_products_report_v2 import order_matches_status
        report_statuses = validate_report_statuses(report_statuses)
        all_orders = [o for o in all_orders if order_matches_status(
            o, report_statuses.split(","), included_statuses)]
    elif included_statuses:
        all_orders = [
            o for o in all_orders
            if _matches_any(o.get("order_status", ""), included_statuses)
        ]

    # Keep the selected order registry intact. Only a current, bound zero-paid
    # proof with a completed historical-money/cost check may leave money KPIs.
    from datetime import time as _cohort_time, timedelta as _cohort_delta
    from awaiting_payment_eligibility import (
        AWAITING_PAYMENT_RAW_PROJECTION, attach_financial_cohort_eligibility,
        financial_cohort_eligibility,
    )
    from awaiting_payment_history import defer_financial_cohort_history
    from awaiting_payment_cohort import split_financial_cohort
    cohort_cutoff = (datetime.combine(datetime.fromisoformat(to_date).date(), _cohort_time.max,
                                     tzinfo=timezone(_cohort_delta(hours=3)))
                     if to_date else datetime.now(timezone.utc))
    candidate_numbers = []
    for order in all_orders:
        candidate = financial_cohort_eligibility(order, {}, owner=owner_id)
        if candidate["review_required"] or candidate["eligible"] is False:
            candidate_numbers.append(order.get("order_number"))
    cohort_proof_rows = []
    if candidate_numbers:
        # Separate projection: whole payment fee proof and nested zero-paid
        # fields cannot coexist in one Mongo inclusion projection.
        cohort_proof_rows = await source_db.unified_orders.find(
            {**orders_q, "order_number": {"$in": candidate_numbers}},
            AWAITING_PAYMENT_RAW_PROJECTION,
        ).to_list(100000)
    # Native-only placeholders remain visible as disagreement/review. They do
    # not acquire a full exclusion proof merely because fee routing read them.
    candidate_number_set = set(candidate_numbers)
    for order in all_orders:
        if order.get("order_number") in candidate_number_set:
            continue
        native = order.get("_dashboard_fee_source") or {}
        native_payment = native.get("payment") or {}
        cohort_proof_rows.append({"user_id": owner_id, "order_number": order.get("order_number"),
                                  "raw_by_source": {"salla_direct": {
                                      "payment_method": native.get("payment_method"),
                                      "payment": {"method": native_payment.get("method")} if isinstance(native_payment, dict) else native_payment,
                                  }}})
    registry_orders = defer_financial_cohort_history(
        attach_financial_cohort_eligibility(
            all_orders, cohort_proof_rows, owner=owner_id, as_of=cohort_cutoff))
    registry_currency = summarize_orders_sar(registry_orders)
    all_orders, financial_cohort = split_financial_cohort(registry_orders)

    parsed_all = await orders_to_parsed_cooperative(all_orders)
    currency_conversion = parsed_all["currency_conversion"]
    currency_conversion_complete = currency_conversion["complete"] is True
    matched_all = match_settings(
        parsed_all,
        [],  # Payment fees are resolved by the shared authority below.
        settings.get("shipping_companies", DEFAULT_SHIPPING_COMPANIES),
    )
    fee_context = await load_dashboard_fee_context(source_db, owner_id, all_orders, as_of=to_date, fee_context=fee_context)
    fee_result = await dashboard_fee_breakdown(
        all_orders, settings.get("payment_methods", DEFAULT_PAYMENT_METHODS), fee_context)
    matched_all.update(payment_breakdown=fee_result["payment_breakdown"],
                       total_payment_fees=fee_result["total_payment_fees"])

    # ── iter-256 — Shipping cost SSOT consolidation ───────────────────
    # Replace match_settings' shipping breakdown with the canonical
    # `shipping_cost_ssot.aggregate_breakdown()` so the dashboard
    # ALWAYS agrees with /api/shipping-ledger (same per-order/per-company
    # math: total = base + tax). Payment breakdown uses its shared authority.
    from shipping_cost_ssot import (
        aggregate_breakdown as _ssot_agg,
        get_company_configs as _ssot_cfgs,
    )
    _ssot_company_cfgs = await _ssot_cfgs(source_db, owner_id)
    _ssot_agg_result = _ssot_agg(all_orders, _ssot_company_cfgs)
    _ssot_breakdown = []
    _ssot_deferred = 0.0
    for pc in _ssot_agg_result["per_company"].values():
        cfg = _ssot_company_cfgs.get(pc["name"]) or {}
        is_deferred = bool(cfg.get("is_deferred", False))
        cfg_has_cost = (
            cfg.get("cost_per_order") is not None
            or cfg.get("cost") is not None
        )
        row = {
            "name":           pc["name"],
            "orders_count":   pc["orders_count"],
            # Dashboard-legacy fields (kept for backward-compat with
            # /api/dashboard consumers and _merge_breakdown):
            "cost_per_order": pc["cost_per_unit"],
            "base_cost":      pc["base"],
            "vat_amount":     pc["tax"],
            "vat_percent":    round(pc["vat_rate"] * 100, 2),
            "total_cost":     pc["total"],
            "matched":        cfg_has_cost,
            "is_deferred":    is_deferred,
            # SSOT canonical per-unit fields — used by the new unified
            # breakdown table in ProfitSummaryCard:
            "cost_per_unit":  pc["cost_per_unit"],
            "tax_per_unit":   pc["tax_per_unit"],
            "total_per_unit": pc["total_per_unit"],
            "vat_rate":       pc["vat_rate"],
        }
        if is_deferred:
            _ssot_deferred += pc["total"]
        _ssot_breakdown.append(row)
    # Authoritative shipping numbers (SSOT-driven):
    matched_all["shipping_breakdown"]      = _ssot_breakdown
    matched_all["total_shipping_cost"]     = round(
        _ssot_agg_result["total_with_tax"], 2)
    matched_all["deferred_shipping_cost"]  = round(_ssot_deferred, 2)

    total_sales = parsed_all["total_sales"]
    registry_sales = registry_currency["total_sar"]
    total_orders = financial_cohort["total_orders"]
    total_fees = matched_all["total_payment_fees"]
    total_shipping = matched_all["total_shipping_cost"]
    deferred_shipping = matched_all.get("deferred_shipping_cost", 0.0)

    # BNPL / electronic / COD split — iter-64 uses the unified
    # normalize_payment_method() so the same classification logic powers
    # Dashboard, Accounts, and Reports.
    from payment_methods import normalize_payment_method as _npm
    total_vat = 0.0
    bnpl_fees = tamara_fees = tabby_fees = emkan_fees = 0.0
    other_payment_fees = 0.0
    bnpl_sales = other_payment_sales = cod_sales = cod_fees = 0.0
    # iter-47 — Bank transfer split into its own KPI card.
    bank_sales = bank_fees = 0.0
    for p in matched_all.get("payment_breakdown", []):
        total_vat += float(p.get("vat_amount", 0) or 0)
        raw_name = p.get("name", "") or ""
        fee = float(p.get("fee_amount", 0) or 0)
        sales = float(p.get("total_sales", 0) or 0)
        sub_key, _disp, parent = _npm(raw_name)
        # Effective bucket: bank rails / salla rails collapse to their parent.
        if parent == "bank_transfer" or sub_key == "bank_transfer":
            bank_fees += fee; bank_sales += sales
        elif sub_key == "tamara":
            tamara_fees += fee; bnpl_fees += fee; bnpl_sales += sales
        elif sub_key == "tabby":
            tabby_fees += fee; bnpl_fees += fee; bnpl_sales += sales
        elif sub_key == "emkan":
            emkan_fees += fee; bnpl_fees += fee; bnpl_sales += sales
        elif sub_key == "cash_on_delivery":
            cod_fees += fee; cod_sales += sales
        else:
            other_payment_fees += fee; other_payment_sales += sales
    for sh in matched_all.get("shipping_breakdown", []):
        total_vat += float(sh.get("vat_amount", 0) or 0)
    incomplete_fee_buckets = {
        parent or key for row in fee_result["payment_breakdown"] if not row["fees_complete"]
        for key, _display, parent in [_npm(row["name"])]
    }
    electronic_fees_complete = not (incomplete_fee_buckets - {
        "bank_transfer", "tamara", "tabby", "emkan", "cash_on_delivery"})
    electronic_before_filter_complete = electronic_fees_complete

    # ── iter-45 — Electronic Net status filter ─────────────────────────────
    # The default `other_payment_sales` / `other_payment_fees` above include
    # EVERY order, even cancelled/refunded/failed ones. Salla's "غير المفوترة"
    # screen only shows transactions actually captured by the gateway. We
    # recompute `other_payment_sales` & `other_payment_fees` using a status
    # filter that mirrors Salla's behaviour. The other buckets (BNPL/COD)
    # stay untouched so we don't break the existing tests/cards.
    elec_excluded_terms = settings.get(
        "electronic_net_excluded_statuses",
    )
    if elec_excluded_terms is None:
        elec_excluded_terms = DEFAULT_ELECTRONIC_NET_EXCLUDED_STATUSES

    def _is_electronic_method(payment_method: str) -> bool:
        """Electronic = Salla card rails (mada, Apple Pay, STC Pay, cards,
        wallet). Bank transfer, BNPL providers, and COD are NOT electronic."""
        sub_key, _disp, parent = _npm(payment_method or "")
        if not sub_key:
            return False
        # The 'salla' parent groups all electronic card rails.
        return parent == "salla"

    # Build a filtered electronic-only order list.
    electronic_orders_included: list[dict] = []
    electronic_orders_excluded: list[dict] = []
    for o in all_orders:
        if not _is_electronic_method(dashboard_payment_method(o)):
            continue
        if _is_excluded_for_electronic_net(o.get("order_status", ""), elec_excluded_terms):
            electronic_orders_excluded.append(o)
        else:
            electronic_orders_included.append(o)

    if electronic_orders_included or electronic_orders_excluded:
        parsed_elec = (
            _dashboard_deepcopy(parsed_all)
            if len(electronic_orders_included) == len(all_orders)
            and all(a is b for a, b in zip(electronic_orders_included, all_orders))
            else await orders_to_parsed_cooperative(electronic_orders_included)
        )
        matched_elec = match_settings(
            parsed_elec,
            [],  # Replaced by the same fee authority as the full cohort.
            settings.get("shipping_companies", DEFAULT_SHIPPING_COMPANIES),
        )
        electronic_fee_result = await dashboard_fee_breakdown(
            electronic_orders_included, settings.get("payment_methods", DEFAULT_PAYMENT_METHODS), fee_context)
        matched_elec.update(payment_breakdown=electronic_fee_result["payment_breakdown"],
                            total_payment_fees=electronic_fee_result["total_payment_fees"])
        electronic_fees_complete = electronic_fee_result["payment_fees_complete"]
        # Override electronic sales/fees with the filtered figures.
        filtered_elec_sales = 0.0
        filtered_elec_fees = 0.0
        for p in matched_elec.get("payment_breakdown", []):
            filtered_elec_sales += float(p.get("total_sales", 0) or 0)
            filtered_elec_fees += float(p.get("fee_amount", 0) or 0)
        # Stash the pre-filter values for transparency in the response.
        electronic_net_breakdown = {
            "included_count": len(electronic_orders_included),
            "excluded_count": len(electronic_orders_excluded),
            "excluded_statuses_active": list(elec_excluded_terms),
            "gross_before_filter": round(other_payment_sales, 2),
            "fees_before_filter": round(other_payment_fees, 2),
            "gross_after_filter": round(filtered_elec_sales, 2),
            "fees_after_filter": round(filtered_elec_fees, 2),
        }
        other_payment_sales = filtered_elec_sales
        other_payment_fees = filtered_elec_fees
    else:
        electronic_net_breakdown = {
            "included_count": 0,
            "excluded_count": 0,
            "excluded_statuses_active": list(elec_excluded_terms),
            "gross_before_filter": round(other_payment_sales, 2),
            "fees_before_filter": round(other_payment_fees, 2),
            "gross_after_filter": round(other_payment_sales, 2),
            "fees_after_filter": round(other_payment_fees, 2),
        }

    # ── Legacy analyses fallback ────────────────────────────────────────────
    # Older Excel uploads (pre unified_orders migration) wrote ONLY aggregate
    # summaries into `analyses` without saving the per-order details, so they
    # don't appear in unified_orders. To prevent the dashboard from showing
    # less data than the Reports page, we add those legacy summaries here.
    # Filter is by analysis.date because per-order dates aren't available.
    #
    # IMPORTANT: when `report_included_statuses` is configured, legacy analyses
    # CANNOT be filtered by per-order status (they lack the data), so we
    # exclude them entirely to avoid skewing the user-curated totals. Users
    # who want their old data filtered by status must reprocess those analyses.
    legacy_analyses: list[dict] = []
    # Keyword sets used by the legacy-analyses payment_breakdown
    # classifier below. Defined here (not in the inline loop) so the
    # references survive even when the new normalize_payment_method
    # path skips the legacy branch.
    tamara_keywords = ("تمارا", "tamara")
    tabby_keywords = ("تابي", "tabby")
    emkan_keywords = ("إمكان", "امكان", "emkan", "amkan")
    cod_keywords = ("عند الاستلام", "عند الاستلم", "cod",
                     "cash on delivery", "cash_on_delivery")
    bank_keywords = (
        "تحويل بنكي", "حوالة بنكية", "تحويل البنك", "تحويل بنوك",
        "bank transfer", "bank_transfer", "wire transfer",
    )
    if include_legacy_analyses and not included_statuses:
        legacy_q: dict = {
            "user_id": owner_id,
            "$or": [
                {"orders_imported": {"$exists": False}},
                {"orders_imported": {"$in": [None, 0]}},
            ],
        }
        if from_date or to_date:
            legacy_q["date"] = {}
            if from_date:
                legacy_q["date"]["$gte"] = from_date
            if to_date:
                legacy_q["date"]["$lte"] = to_date
        legacy_analyses = await source_db.analyses.find(
            legacy_q, {"_id": 0, "report.orders_sample": 0}
        ).to_list(1000)

        for a in legacy_analyses:
            rep = a.get("report") or {}
            s = rep.get("summary") or {}
            total_sales += float(s.get("total_sales") or 0)
            if registry_sales is not None:
                registry_sales += float(s.get("total_sales") or 0)
            total_orders += int(s.get("total_orders") or 0)
            total_fees += float(s.get("total_payment_fees") or 0)
            total_shipping += float(s.get("total_shipping_cost") or 0)
            deferred_shipping += float(s.get("deferred_shipping_cost") or 0)
            for p in rep.get("payment_breakdown", []) or []:
                total_vat += float(p.get("vat_amount", 0) or 0)
                name_lc = (p.get("name", "") or "").strip().lower()
                fee = float(p.get("fee_amount", 0) or 0)
                sales = float(p.get("total_sales", 0) or 0)
                if any(k in name_lc for k in tamara_keywords):
                    tamara_fees += fee; bnpl_fees += fee; bnpl_sales += sales
                elif any(k in name_lc for k in tabby_keywords):
                    tabby_fees += fee; bnpl_fees += fee; bnpl_sales += sales
                elif any(k in name_lc for k in emkan_keywords):
                    emkan_fees += fee; bnpl_fees += fee; bnpl_sales += sales
                elif any(k in name_lc for k in cod_keywords):
                    cod_fees += fee; cod_sales += sales
                elif any(k in name_lc for k in bank_keywords) or name_lc == "bank":
                    bank_fees += fee; bank_sales += sales
                else:
                    other_payment_fees += fee; other_payment_sales += sales
            for sh in rep.get("shipping_breakdown", []) or []:
                total_vat += float(sh.get("vat_amount", 0) or 0)

    # ── Shipping & COD balance splits (Phase 1) ──────────────────────────────
    from shipping_cost_ssot import get_company_configs as _ssot_get_cfgs
    _ssot_cfgs = await _ssot_get_cfgs(source_db, owner_id)
    balances = compute_balances(
        all_orders,
        settings.get("shipping_approved_statuses", DEFAULT_SHIPPING_APPROVED),
        settings.get("cod_approved_statuses", DEFAULT_COD_APPROVED),
        company_cfgs=_ssot_cfgs,
    )
    shipping_balance_approved = balances["shipping"]["total_approved"]
    shipping_balance_unapproved = balances["shipping"]["total_unapproved"]
    cod_balance_approved = balances["cod"]["total_approved"]
    cod_balance_unapproved = balances["cod"]["total_unapproved"]

    # ── Daily costs (still keyed by `date` field) ────────────────────────────
    daily_q = {"user_id": owner_id}
    if from_date or to_date:
        date_filter = {}
        if from_date:
            date_filter["$gte"] = from_date
        if to_date:
            date_filter["$lte"] = to_date
        daily_q["date"] = date_filter
    daily = await source_db.daily_costs.find(daily_q, {"_id": 0}).to_list(1000)

    # ── TikTok Ads daily (pushed by Make.com via /api/webhook/tiktok/...) ────
    tt_q = {"user_id": owner_id}
    if from_date or to_date:
        df = {}
        if from_date: df["$gte"] = from_date
        if to_date: df["$lte"] = to_date
        tt_q["date"] = df
    tt_rows = await source_db.tiktok_ads_daily.find(tt_q, {"_id": 0}).to_list(1000)
    # Iter-160 SSOT (Message #737): TikTok spend from ledger only.
    _fd = from_date or "0000-01-01"
    _td = to_date or "9999-12-31"
    _tt_ledger = await _spend_by_date_from_ledger(
        source_db, owner_id, ("tiktok",), _fd, _td,
    )
    tiktok_spend = sum(_tt_ledger.values())
    tiktok_purchases = sum(int(r.get("purchases") or 0) for r in tt_rows)
    tiktok_revenue = sum(float(r.get("revenue") or 0) for r in tt_rows)
    tiktok_roas = round(tiktok_revenue / tiktok_spend, 2) if tiktok_spend > 0 else 0.0

    # ── Meta Ads daily (pulled via /api/meta/sync from Meta Marketing API) ──
    meta_q = {"user_id": owner_id}
    if from_date or to_date:
        df = {}
        if from_date: df["$gte"] = from_date
        if to_date: df["$lte"] = to_date
        meta_q["date"] = df
    meta_rows = await source_db.meta_ads_daily.find(meta_q, {"_id": 0}).to_list(2000)
    # Iter-160 SSOT (Message #737): Meta spend from ledger only.
    _meta_ledger = await _spend_by_date_from_ledger(
        source_db, owner_id, ("meta", "facebook", "instagram"), _fd, _td,
    )
    meta_spend_total = sum(_meta_ledger.values())
    meta_purchases_total = sum(int(r.get("purchases") or 0) for r in meta_rows)
    meta_revenue_total = sum(float(r.get("revenue") or 0) for r in meta_rows)

    # ── Total ads cost across all platforms ──────────────────────────────
    # Iter-160 SSOT (Message #737): all ad spend must come from
    # ad_account_ledger. We no longer read daily_costs.snapchat_ads/
    # snapchat_ads_2 / instagram_ads / google_ads / tiktok_ads for the
    # aggregate spend. Those manual-entry fields are deprecated for
    # accounting purposes — only the unified ledger counts.
    _snap_ledger = await _spend_by_date_from_ledger(
        source_db, owner_id, ("snapchat", "snap"), _fd, _td,
    )
    snap_spend_total = sum(_snap_ledger.values())
    # Other providers (google/twitter/other) from ledger as well.
    _other_ledger = await _spend_by_date_from_ledger(
        source_db, owner_id, ("google", "twitter", "other"), _fd, _td,
    )
    other_spend_total = sum(_other_ledger.values())
    daily_ads_total = (snap_spend_total + tiktok_spend
                       + meta_spend_total + other_spend_total)
    daily_products_total = sum((d.get("product_costs", 0) or 0) for d in daily)

    # ── Computed product cost from order line-items (iteration 19) ─────────
    # When `unified_orders.total_product_cost` is populated (via webhook
    # ingestion → product_costs.attach_cost_to_order_doc), prefer THAT as
    # the source of truth for product cost — it reflects real SKU-level
    # costs. The legacy `daily_costs.product_costs` (manual entry) stays
    # as a fallback so single-merchant flows without per-SKU costs still
    # work. We take max() per the same dedupe-via-bigger pattern used for
    # TikTok webhook vs daily_costs.
    # Iter-91 Phase 1: subtract COGS for cancelled/refunded orders and
    # scale partial-refund orders proportionally so the profit KPI no
    # longer over-charges product cost on returned goods.
    from order_status_policy import (
        effective_product_cost as _effective_pc,
        get_policy_map as _get_policy_map,
    )
    policy_overrides_pc = await _get_policy_map(source_db, owner_id)
    computed_product_cost = round(sum(
        _effective_pc(o, policy_overrides_pc) for o in all_orders
    ), 2)
    # Distinct missing-cost lines across the filtered orders (UI badge).
    missing_cost_skus: set = set()
    for o in all_orders:
        for ln in (o.get("missing_product_cost_lines") or []):
            key = (ln.get("sku") or ln.get("product_id") or ln.get("name") or "").strip().upper()
            if key:
                missing_cost_skus.add(key)
    # ── Profit-status counts (iteration 24) ───────────────────────────────
    # `profit_status` is set per-order by attach_cost_to_order_doc. It
    # tells the dashboard whether the order's REAL profit can be trusted:
    #   - complete                : every product matched a cost entry
    #   - incomplete_missing_cost : ≥1 product has no cost (UI prompts add)
    #   - incomplete_no_products  : no products[] (typically Excel orders)
    incomplete_profit_orders_count = 0
    no_products_orders_count = 0
    excel_no_products_count = 0
    for o in all_orders:
        ps = (o.get("profit_status") or "").strip()
        ds = (o.get("data_source") or "").strip().lower()
        # Fallback: if profit_status was never written (legacy orders),
        # infer it from products[] presence so the UI still works for
        # pre-iteration-24 data without forcing a backfill.
        if not ps:
            if not (o.get("products") or []):
                ps = "incomplete_no_products"
            elif (o.get("missing_product_cost_lines") or []):
                ps = "incomplete_missing_cost"
            else:
                ps = "complete"
        if ps != "complete":
            incomplete_profit_orders_count += 1
        if ps == "incomplete_no_products":
            no_products_orders_count += 1
            if ds in ("excel", ""):
                excel_no_products_count += 1
    product_cost_effective = max(computed_product_cost, daily_products_total)
    daily_totals = daily_ads_total + product_cost_effective

    # ── Operating Expenses (المصروفات التشغيلية) ─────────────────────────────
    # Aggregate the per-day salary + rental + variable-expense costs over the
    # active date range. When no range is supplied we still aggregate so the
    # dashboard reflects month-to-date operating cost.
    today_d = datetime.now(timezone.utc).date()
    fd = _parse_date_or(from_date, today_d.replace(day=1))
    td = _parse_date_or(to_date, today_d)
    if td < fd:
        fd, td = td, fd
    op_range = await compute_operating_expenses_for_range(source_db, owner_id, fd, td)
    operating_expenses_total = float(op_range.get("operating_total") or 0)
    operating_salaries_total = float(op_range.get("salaries_total") or 0)
    operating_rentals_total = float(op_range.get("rentals_total") or 0)
    operating_prepaid_total = float(op_range.get("prepaid_total") or 0)
    operating_daily_other_total = float(op_range.get("daily_other_total") or 0)

    # Net profit (orders P&L − daily ads − product cost (computed or manual) − operating expenses)
    orders_profit = total_sales - total_fees - total_shipping
    net_profit_adjusted = (
        orders_profit
        - daily_ads_total
        - product_cost_effective
        - operating_expenses_total
    )

    # ── Phase 3: configurable "صافي المبيعات" KPI ────────────────────────────
    # Merchants disagree on what counts as "net sales" — some treat shipping
    # collected as revenue, others net it out; some deduct VAT, others don't.
    # Settings.net_sales_config gives them 7 independent toggles.
    cfg = settings.get("net_sales_config") or DEFAULT_NET_SALES_CONFIG
    regular_shipping = total_shipping - deferred_shipping
    net_sales = total_sales
    if cfg.get("deduct_payment_fees", True):
        net_sales -= total_fees
    if cfg.get("deduct_shipping", True):
        net_sales -= regular_shipping
    if cfg.get("deduct_deferred_shipping", False):
        net_sales -= deferred_shipping
    if cfg.get("deduct_ads", True):
        net_sales -= daily_ads_total
    if cfg.get("deduct_product_costs", True):
        net_sales -= product_cost_effective
    if cfg.get("deduct_vat", False):
        net_sales -= total_vat
    if cfg.get("deduct_daily_expenses", False):
        # daily_expenses_total is currently aliased to daily_products_total;
        # keep this independent for future expansion.
        pass
    if cfg.get("deduct_operating_expenses", True):
        net_sales -= operating_expenses_total

    # ── Monthly trend from unified orders + legacy analyses ─────────────────
    from collections import defaultdict
    monthly_sales = defaultdict(float)
    monthly_unverified_currency: set[str] = set()
    for o in all_orders:
        d = (o.get("order_date") or "")[:7]
        if not d:
            continue
        monthly_sales[d] += 0.0
        amount_sar = order_total_sar(o)
        if amount_sar is None:
            monthly_unverified_currency.add(d)
        else:
            monthly_sales[d] += amount_sar
    for a in legacy_analyses:
        d = (a.get("date") or a.get("created_at") or "")[:7]
        if not d:
            continue
        monthly_sales[d] += float(((a.get("report") or {}).get("summary") or {}).get("total_sales") or 0)
    monthly = sorted([
        {
            "month": k,
            "sales": None if k in monthly_unverified_currency else round(v, 2),
            "known_sales_sar": round(v, 2),
            "currency_conversion_complete": k not in monthly_unverified_currency,
            "profit": 0,
        }
        for k, v in monthly_sales.items()
    ], key=lambda x: x["month"])

    # Recent analyses (informational only — independent of date filter)
    recent = await source_db.analyses.find(
        {"user_id": owner_id},
        {"_id": 0, "report.orders_sample": 0},
    ).sort("created_at", -1).to_list(5)

    # Source breakdown (excel vs make vs unified)
    src_counts = {"excel": 0, "make": 0, "unified": 0}
    for o in registry_orders:
        ds = o.get("data_source") or "unified"
        src_counts[ds] = src_counts.get(ds, 0) + 1

    # Merge live + legacy breakdowns into a single payload
    def _merge_breakdown(live: list[dict], legacy_list: list[dict], key: str) -> list[dict]:
        m: dict[str, dict] = {}
        for b in live:
            n = (b.get("name") or "").strip()
            if not n:
                continue
            cur = m.setdefault(n, {**b, "name": n})
            # ensure numeric sums (live already aggregated; just copy)
        for a in legacy_list:
            for b in (a.get("report") or {}).get(key, []) or []:
                n = (b.get("name") or "").strip()
                if not n:
                    continue
                if n in m:
                    cur = m[n]
                    for f in ("total_sales", "total_cost", "fee_amount", "orders_count", "vat_amount"):
                        if f in b and isinstance(b.get(f), (int, float)):
                            cur[f] = float(cur.get(f, 0) or 0) + float(b.get(f) or 0)
                else:
                    m[n] = {**b, "name": n}
        return list(m.values())

    payment_breakdown_merged = _merge_breakdown(
        matched_all.get("payment_breakdown", []), legacy_analyses, "payment_breakdown",
    )
    shipping_breakdown_merged = _merge_breakdown(
        matched_all.get("shipping_breakdown", []), legacy_analyses, "shipping_breakdown",
    )

    # iter-64 — Roll the per-raw-name payment_breakdown rows up into the
    # SAME canonical buckets used by Accounts (سلة, تحويل بنكي, تابي, …).
    # Each bucket keeps a `sub_methods` array so the UI can still show the
    # original Salla rail / specific bank inside the row.
    def _rollup_payment_breakdown(rows: list[dict]) -> list[dict]:
        buckets: dict[str, dict] = {}
        for r in rows:
            raw = (r.get("name") or "").strip()
            sub_key, sub_disp, parent = _npm(raw)
            if not sub_key:
                # Skip null/unknown markers — they'd pollute the table.
                continue
            top_key = parent or sub_key
            top_disp = PARENT_LABELS.get(parent, sub_disp) if parent else sub_disp
            b = buckets.setdefault(top_key, {
                "name": top_disp,
                "key": top_key,
                "total_sales": 0.0,
                "fee_amount": 0.0,
                "vat_amount": 0.0,
                "orders_count": 0,
                "commission_percent": r.get("commission_percent"),
                "fixed_fee": r.get("fixed_fee"),
                "vat_percent": r.get("vat_percent"),
                "sub_methods": [],
            })
            sales = float(r.get("total_sales") or 0)
            fee   = float(r.get("fee_amount") or 0)
            vat   = float(r.get("vat_amount") or 0)
            cnt   = int(r.get("orders_count") or 0)
            b["total_sales"] += sales
            b["fee_amount"]  += fee
            b["vat_amount"]  += vat
            b["orders_count"] += cnt
            merge_statement_estimate_metadata(b, r)
            # Aggregate sub-methods by their CANONICAL key so multiple raw
            # spellings of e.g. الراجحي collapse into one sub-row.
            sm_idx = {s["key"]: i for i, s in enumerate(b["sub_methods"])}
            if sub_key in sm_idx:
                s = b["sub_methods"][sm_idx[sub_key]]
                s["total_sales"] += sales
                s["fee_amount"]  += fee
                s["orders_count"] += cnt
            else:
                b["sub_methods"].append({
                    "key": sub_key,
                    "display": sub_disp,
                    "name": sub_disp,
                    "total_sales": sales,
                    "fee_amount": fee,
                    "orders_count": cnt,
                })
                s = b["sub_methods"][-1]
            merge_statement_estimate_metadata(s, r)
        # Sort sub_methods by sales desc, round everything for the response.
        out = []
        for b in buckets.values():
            b["sub_methods"].sort(key=lambda s: s["total_sales"], reverse=True)
            for s in b["sub_methods"]:
                s["total_sales"] = round(s["total_sales"], 2)
                s["fee_amount"]  = round(s["fee_amount"], 2)
            b["total_sales"] = round(b["total_sales"], 2)
            b["fee_amount"]  = round(b["fee_amount"], 2)
            b["vat_amount"]  = round(b["vat_amount"], 2)
            out.append(b)
        out.sort(key=lambda x: x["total_sales"], reverse=True)
        return out

    from payment_methods import PARENT_LABELS
    payment_breakdown_merged = _rollup_payment_breakdown(payment_breakdown_merged)
    if not currency_conversion_complete:
        for payment_row in payment_breakdown_merged:
            for field in ("total_sales", "fee_amount", "vat_amount"):
                payment_row[f"known_{field}_sar"] = payment_row.get(field)
                payment_row[field] = None
            payment_row["currency_conversion_complete"] = False
            for sub_method in payment_row.get("sub_methods") or []:
                for field in ("total_sales", "fee_amount"):
                    sub_method[f"known_{field}_sar"] = sub_method.get(field)
                    sub_method[field] = None
                sub_method["currency_conversion_complete"] = False

    # ── Iter-44: Cross-platform ROAS + Average Cost Per Order ────────────
    # ROAS (Return On Ad Spend) — how many SAR of revenue each SAR of ad
    # spend produced. Industry-standard formula is gross sales ÷ total
    # ad spend (matches GA, Meta, TikTok, Snap conventions); merchants
    # who prefer net-sales-based ROAS can flip a setting later.
    # CPA (متوسط تكلفة الطلب) — total ad spend ÷ number of orders.
    # Both surfaced as null when the denominator is 0 so the UI can show
    # "—" instead of "Infinity" or "NaN".
    overall_roas = round(total_sales / daily_ads_total, 2) if daily_ads_total > 0 else None
    avg_cost_per_order = (
        round(daily_ads_total / total_orders, 2) if total_orders > 0 and daily_ads_total > 0 else None
    )

    # ── iter-56 — Payment Adjustments (الاسترجاعات/التسويات) ────────────────
    # Subtract any partial refunds / item removals / cancellations that were
    # recorded against orders in the period (or even pre-period orders whose
    # adjustment date falls in the period — matching Salla's actual wallet
    # behavior). This affects per-provider NET sales; gross sales remain
    # untouched so totals stay traceable to raw orders.
    settlements_by_provider = await aggregate_settlements_by_provider(
        source_db, owner_id, from_date, to_date
    )
    salla_adj         = settlements_by_provider["salla"]["total_adjustment"]
    tamara_adj        = settlements_by_provider["tamara"]["total_adjustment"]
    tabby_adj         = settlements_by_provider["tabby"]["total_adjustment"]
    emkan_adj         = settlements_by_provider["emkan"]["total_adjustment"]
    bank_adj          = settlements_by_provider["bank_transfer"]["total_adjustment"]
    cod_adj           = settlements_by_provider["cod"]["total_adjustment"]
    other_adj         = settlements_by_provider["other"]["total_adjustment"]

    # iter-73 — surface refunded_amount + refunds_count per provider on the
    # payment_breakdown rows so the Reports commission cards can show how
    # much was reversed via settlements for each gateway. The keys of
    # `settlements_by_provider` are the same canonical names used by the
    # rollup ('salla', 'tamara', 'tabby', 'emkan', 'bank_transfer', 'cod').
    _settlement_key_map = {
        "salla": "salla",
        "tamara": "tamara",
        "tabby": "tabby",
        "emkan": "emkan",
        "bank_transfer": "bank_transfer",
        "cash_on_delivery": "cod",
    }
    for _b in payment_breakdown_merged:
        _sk = _settlement_key_map.get(_b.get("key"))
        _agg = settlements_by_provider.get(_sk) if _sk else None
        _b["refunded_amount"] = round(float(_agg.get("total_adjustment") or 0), 2) if _agg else 0.0
        _b["refunds_count"]   = int(_agg.get("count") or 0) if _agg else 0
    total_adjustments = round(
        salla_adj + tamara_adj + tabby_adj + emkan_adj + bank_adj + cod_adj + other_adj, 2,
    )

    # Salla-specific: split adjustments by whether the original order is
    # still within Salla's 14-day pending wallet. Used by the "Salla wallet
    # alert" badge to explain reference mismatches.
    salla_settle_inside = salla_settle_outside = 0.0
    salla_settle_docs = await source_db.payment_adjustments.find(
        {
            "user_id": owner_id,
            "provider": "salla",
            **({"adjusted_at": {**({"$gte": from_date} if from_date else {}),
                                **({"$lte": to_date} if to_date else {})}}
               if (from_date or to_date) else {}),
        },
        {"_id": 0, "adjustment_amount": 1, "order_created_at": 1},
    ).to_list(20000)
    for d in salla_settle_docs:
        if classify_14d_window(d.get("order_created_at", "")) == "inside_14d":
            salla_settle_inside += float(d.get("adjustment_amount", 0) or 0)
        else:
            salla_settle_outside += float(d.get("adjustment_amount", 0) or 0)

    response = {
        "range": {"from_date": from_date, "to_date": to_date},
        "financial_cohort": financial_cohort,
        "payment_fee_authority": {
            "basis": fee_result["basis"],
            "complete": fee_result["payment_fees_complete"],
            "known_payment_fees_sar": fee_result["total_payment_fees"],
            "reason_codes": fee_result["fee_review_reasons"],
            "incomplete_buckets": sorted(incomplete_fee_buckets),
            "electronic_complete": electronic_fees_complete,
            "electronic_before_filter_complete": electronic_before_filter_complete,
            "legacy_analysis_fees_included": bool(legacy_analyses),
            **({key: fee_result[key] for key in (
                "payment_fees_estimated", "payment_fee_estimate_sources", "estimated_orders_count")}
                if fee_result.get("payment_fees_estimated") else {}),
        },
        "totals": {
            "total_sales": (
                round(total_sales, 2) if currency_conversion_complete else None
            ),
            "net_sales": (
                round(net_sales, 2) if currency_conversion_complete else None
            ),
            "accounting_currency": "SAR",
            "sales_currency_conversion_complete": currency_conversion_complete,
            "unverified_currency_orders_count": currency_conversion[
                "unverified_orders_count"
            ],
            "total_orders": int(total_orders),
            "financial_eligible_orders": financial_cohort["financial_eligible_orders"],
            "excluded_unpaid_orders": financial_cohort["excluded_unpaid_orders"],
            "review_orders": financial_cohort["review_orders"],
            "financial_cohort_complete": financial_cohort["complete"],
            # iter-44 — cross-platform marketing KPIs
            "overall_roas": overall_roas if currency_conversion_complete else None,
            "avg_cost_per_order": avg_cost_per_order,
            "total_payment_fees": (
                round(total_fees, 2) if currency_conversion_complete else None
            ),
            "bnpl_fees": round(bnpl_fees, 2) if currency_conversion_complete else None,
            "tamara_fees": round(tamara_fees, 2) if currency_conversion_complete else None,
            "tabby_fees": round(tabby_fees, 2) if currency_conversion_complete else None,
            "emkan_fees": round(emkan_fees, 2) if currency_conversion_complete else None,
            "other_payment_fees": (
                round(other_payment_fees, 2)
                if currency_conversion_complete else None
            ),
            # iter-56 — electronic_net now subtracts Salla settlements too
            "electronic_net": (
                round(other_payment_sales - other_payment_fees - salla_adj, 2)
                if currency_conversion_complete else None
            ),
            "electronic_net_before_settlements": (
                round(other_payment_sales - other_payment_fees, 2)
                if currency_conversion_complete else None
            ),
            # iter-45 — visible filtering metadata for the UI
            "electronic_net_breakdown": electronic_net_breakdown,
            "salla_electronic_net_reference": settings.get("salla_electronic_net_reference"),
            # iter-56 — per-provider adjustment totals + breakdown
            "settlements_total": total_adjustments,
            "settlements_by_provider": settlements_by_provider,
            "salla_settlements_inside_14d": round(salla_settle_inside, 2),
            "salla_settlements_outside_14d": round(salla_settle_outside, 2),
            "bnpl_net": (
                round(bnpl_sales - bnpl_fees - tamara_adj - tabby_adj - emkan_adj, 2)
                if currency_conversion_complete else None
            ),
            # iter-47 — Bank transfer is now a dedicated KPI; the figures
            # below give the UI everything it needs to render the new card.
            "bank_sales": round(bank_sales, 2) if currency_conversion_complete else None,
            "bank_fees": round(bank_fees, 2) if currency_conversion_complete else None,
            "bank_net": (
                round(bank_sales - bank_fees - bank_adj, 2)
                if currency_conversion_complete else None
            ),
            "total_shipping_cost": round(total_shipping, 2),
            "deferred_shipping_cost": round(deferred_shipping, 2),
            "regular_shipping_cost": round(total_shipping - deferred_shipping, 2),
            "expected_salla_transfer": (
                round(
                    total_sales - total_fees
                    - (total_shipping - deferred_shipping),
                    2,
                )
                if currency_conversion_complete else None
            ),
            "total_ads_cost": round(daily_ads_total, 2),
            "total_product_cost": round(product_cost_effective, 2),
            "computed_product_cost": round(computed_product_cost, 2),
            "manual_product_cost": round(daily_products_total, 2),
            "missing_product_cost_count": len(missing_cost_skus),
            # ── Iteration 24: profit-completeness flags ───────────────────
            "incomplete_profit_orders_count": int(incomplete_profit_orders_count),
            "no_products_orders_count": int(no_products_orders_count),
            "excel_no_products_count": int(excel_no_products_count),
            # ── Iter-207c — Salla-reference transparency block ─────────────
            # The platform (Salla) counts every order it creates regardless
            # of status. We count only orders that pass
            # `report_included_statuses`. Surface the gap so the UI can
            # render a badge "+X معلَّق/ملغى بقيمة Y ر.س" next to the main
            # orders count, and a tooltip explaining the methodology.
            "salla_reference_orders_count": int(salla_ref_orders_count),
            "salla_reference_gross": salla_ref_gross,
            "excluded_orders_count": int(
                max(0, salla_ref_orders_count - total_orders)),
            "excluded_gross": (
                round(max(0.0, float(salla_ref_gross) - float(registry_sales)), 2)
                if salla_ref_gross is not None and registry_sales is not None
                else None
            ),
            "daily_expenses_total": round(daily_products_total, 2),
            "net_profit": (
                round(net_profit_adjusted, 2)
                if currency_conversion_complete and financial_cohort["complete"]
                else None
            ),
            "total_vat": round(total_vat, 2) if currency_conversion_complete else None,
            "daily_costs_total": round(daily_totals, 2),
            "daily_ads_total": round(daily_ads_total, 2),
            "daily_products_total": round(daily_products_total, 2),
            # ── Operating Expenses (المصروفات التشغيلية اليومية) ──────────────
            "operating_expenses_total": round(operating_expenses_total, 2),
            "operating_salaries_total": round(operating_salaries_total, 2),
            "operating_salaries_employee": float(op_range.get("salaries_employee") or 0),
            "operating_salaries_household": float(op_range.get("salaries_household") or 0),
            "operating_salaries_charity": float(op_range.get("salaries_charity") or 0),
            "operating_rentals_total": round(operating_rentals_total, 2),
            "operating_prepaid_total": round(operating_prepaid_total, 2),
            "operating_prepaid_by_type": op_range.get("prepaid_by_type") or {},
            "operating_daily_other_total": round(operating_daily_other_total, 2),
            "orders_excel_count": src_counts.get("excel", 0),
            "orders_make_count": src_counts.get("make", 0),
            "legacy_analyses_count": len(legacy_analyses),
            "report_included_statuses_active": bool(included_statuses),
            # ── TikTok Ads (pushed via Make.com webhook) ────────────────────
            "tiktok_spend": round(tiktok_spend, 2),
            "tiktok_purchases": int(tiktok_purchases),
            "tiktok_revenue": round(tiktok_revenue, 2),
            "tiktok_roas": tiktok_roas,
            # ── Meta Ads aggregated total (over the active date range) ──────
            "meta_spend": round(meta_spend_total, 2),
            "meta_purchases": int(meta_purchases_total),
            "meta_revenue": round(meta_revenue_total, 2),
            "meta_roas": round(meta_revenue_total / meta_spend_total, 2) if meta_spend_total > 0 else 0.0,
            # Backward-compatible aliases used by older UI code
            "analyses_count": len(recent),
            "make_orders_count": src_counts.get("make", 0),
            # ── Phase 1: shipping & COD balance splits ───────────────────────
            "shipping_approved": shipping_balance_approved,
            "shipping_unapproved": shipping_balance_unapproved,
            "cod_approved": cod_balance_approved,
            "cod_unapproved": cod_balance_unapproved,
        },
        "net_sales_config": cfg,
        "hide_inferred_date_orders": bool(settings.get("hide_inferred_date_orders")),
        "currency_conversion": currency_conversion,
        "monthly": monthly,
        "payment_breakdown": payment_breakdown_merged,
        "shipping_breakdown": shipping_breakdown_merged,
        "source_breakdown": [
            {"name": k, "count": v} for k, v in src_counts.items() if v > 0
        ],
        "recent_analyses": [
            {
                "id": a["id"],
                "name": a.get("name"),
                "date": a.get("date"),
                "filename": a.get("filename"),
                "total_sales": a["report"]["summary"]["total_sales"],
                "net_profit": a["report"]["summary"]["net_profit"],
                "total_orders": a["report"]["summary"]["total_orders"],
                "orders_imported": int(a.get("orders_imported") or 0),
                "is_legacy": not bool(a.get("orders_imported")),
            } for a in recent
        ],
    }
    return enforce_dashboard_fee_completeness(response)
