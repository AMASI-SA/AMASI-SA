"""Payment-Gateway Metrics — single source of truth (Iter-81).

Replaces ad-hoc per-page aggregations with one canonical service that
every page in the system reads from. Implements the user's required
priority chain:

    1. Settlement-file actual_* fields (Salla / Tamara / Tabby)
    2. Salla Direct
    3. Make.com
    4. Excel
    5. Estimated commission rates  ← fallback

Each row in the response represents ONE canonical payment method with
the fields the merchant listed: gross / fees / fee_vat / refund_full /
refund_partial / net / expected_in_assets / orders_count /
refund_orders_count / cancelled_orders_count.

The canonical method list is the one the user enumerated:
سلة، مدى، Apple Pay، Google Pay، STC Pay، Visa، MasterCard، البطاقة
الائتمانية، البطاقة البنكية، محفظة سلة، تمارا، تابي، إمكان، تحويل بنكي،
والدفع عند الاستلام.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from auth import ensure_user_settings, get_current_user_from_db
from payment_methods import normalize_payment_method


# ── Canonical registry ────────────────────────────────────────────────
# Each entry: canonical_key → (display_ar, type, alias_list, estimated_fee_rate)
# Aliases are case/whitespace-insensitive — they cover every variant we
# have observed coming from Excel / Make / Salla / settlement files.
PAYMENT_METHOD_REGISTRY: dict[str, dict] = {
    "salla": {
        "name_ar": "سلة",
        "type": "gateway",
        "aliases": ["salla", "سلة", "سله", "سلّة", "salla_pay", "سلة باي"],
        "estimated_fee_rate": 2.20,
        "estimated_vat_rate": 15.0,
        "estimated_fixed_fee": 1.0,
    },
    "mada": {
        "name_ar": "مدى",
        "type": "gateway",
        "aliases": ["mada", "مدى"],
        "estimated_fee_rate": 1.0,
        "estimated_vat_rate": 15.0,
        "estimated_fixed_fee": 1.0,
    },
    "applepay": {
        "name_ar": "Apple Pay",
        "type": "gateway",
        "aliases": ["apple pay", "applepay", "apple_pay", "آبل باي", "أبل باي"],
        "estimated_fee_rate": 2.20,
        "estimated_vat_rate": 15.0,
        "estimated_fixed_fee": 1.0,
    },
    "googlepay": {
        "name_ar": "Google Pay",
        "type": "gateway",
        "aliases": ["google pay", "googlepay", "google_pay", "جوجل باي", "قوقل باي"],
        "estimated_fee_rate": 2.20,
        "estimated_vat_rate": 15.0,
        "estimated_fixed_fee": 1.0,
    },
    "stcpay": {
        "name_ar": "STC Pay",
        "type": "gateway",
        "aliases": ["stc pay", "stcpay", "stc_pay", "اس تي سي باي", "stc"],
        "estimated_fee_rate": 1.30,
        "estimated_vat_rate": 15.0,
        "estimated_fixed_fee": 1.0,
    },
    "visa": {
        "name_ar": "Visa",
        "type": "gateway",
        "aliases": ["visa", "فيزا"],
        "estimated_fee_rate": 2.20,
        "estimated_vat_rate": 15.0,
        "estimated_fixed_fee": 1.0,
    },
    "mastercard": {
        "name_ar": "MasterCard",
        "type": "gateway",
        "aliases": ["mastercard", "master card", "ماستر كارد", "ماستركارد"],
        "estimated_fee_rate": 2.20,
        "estimated_vat_rate": 15.0,
        "estimated_fixed_fee": 1.0,
    },
    "credit_card": {
        "name_ar": "البطاقة الائتمانية",
        "type": "gateway",
        "aliases": ["credit", "credit_card", "creditcard", "credit card",
                    "البطاقة الائتمانية",
                    "بطاقة ائتمانية", "بطاقة ائتمان"],
        "estimated_fee_rate": 2.20,
        "estimated_vat_rate": 15.0,
        "estimated_fixed_fee": 1.0,
    },
    "debit_card": {
        "name_ar": "بطاقة بنكية",
        "type": "gateway",
        "aliases": ["debit card", "debit_card", "بطاقة بنكية", "بطاقه بنكيه"],
        "estimated_fee_rate": 2.20,
        "estimated_vat_rate": 15.0,
        "estimated_fixed_fee": 1.0,
    },
    "salla_wallet": {
        "name_ar": "محفظة سلة",
        "type": "gateway",
        "aliases": ["salla wallet", "salla_wallet", "محفظة سلة", "محفظه سلة"],
        "estimated_fee_rate": 0.0,
        "estimated_vat_rate": 0.0,
        "estimated_fixed_fee": 0.0,
    },
    "tamara": {
        "name_ar": "تمارا",
        "type": "gateway",
        "aliases": ["tamara", "تمارا"],
        "estimated_fee_rate": 6.99,
        "estimated_vat_rate": 15.0,
        "estimated_fixed_fee": 1.5,
    },
    "tabby": {
        "name_ar": "تابي",
        "type": "gateway",
        "aliases": ["tabby", "تابي"],
        "estimated_fee_rate": 6.99,
        "estimated_refundable_fee_rate": 4.99,
        "estimated_vat_rate": 15.0,
        "estimated_fixed_fee": 1.0,
    },
    "emkan": {
        "name_ar": "إمكان",
        "type": "gateway",
        "aliases": ["emkan", "إمكان", "امكان"],
        "estimated_fee_rate": 6.99,
        "estimated_vat_rate": 15.0,
        "estimated_fixed_fee": 0.0,
    },
    "bank_transfer": {
        "name_ar": "تحويل بنكي",
        "type": "bank",
        "aliases": ["bank transfer", "bank_transfer", "تحويل بنكي",
                    "حوالة بنكية", "حواله بنكيه", "wire transfer"],
        "estimated_fee_rate": 0.0,
        "estimated_vat_rate": 0.0,
    },
    "cod": {
        "name_ar": "الدفع عند الاستلام",
        "type": "cod",
        "aliases": ["cod", "cash on delivery", "الدفع عند الاستلام",
                    "دفع عند الاستلام", "نقدا عند الاستلام", "cash"],
        "estimated_fee_rate": 0.0,
        "estimated_vat_rate": 0.0,
    },
}


def _build_alias_index() -> dict[str, str]:
    """Reverse-lookup table: normalized alias → canonical_key."""
    out: dict[str, str] = {}
    for key, meta in PAYMENT_METHOD_REGISTRY.items():
        out[_fold(key)] = key
        for alias in meta["aliases"]:
            out[_fold(alias)] = key
    return out


def _fold(s: str) -> str:
    """Lowercase + strip + fold Arabic letter variants so 'البطاقة الإئتمانية'
    matches 'البطاقة الائتمانية'. Mirrors payment_methods._normalize_arabic."""
    if not s:
        return ""
    s = s.strip().lower()
    table = str.maketrans({
        "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
        "ى": "ي",
        "ة": "ه",
        "ـ": "",
    })
    return s.translate(table)


_ALIAS_INDEX = _build_alias_index()


def resolve_canonical(raw: Optional[str]) -> Optional[str]:
    """Map any inbound payment-method string to a canonical_key, or
    None if unrecognized (caller can bucket those under '_other')."""
    if not raw:
        return None
    s = _fold(str(raw))
    if not s:
        return None
    # Exact alias match first
    if s in _ALIAS_INDEX:
        return _ALIAS_INDEX[s]
    # Substring fallback — handles "البطاقة الائتمانية (Visa)" etc.
    for alias, key in sorted(_ALIAS_INDEX.items(), key=lambda item: len(item[0]), reverse=True):
        if alias and alias in s:
            return key
    return None


_METRIC_KEY_BY_SETTING_SUBKEY = {
    "mada": "mada",
    "apple_pay": "applepay",
    "google_pay": "googlepay",
    "stc_pay": "stcpay",
    "visa": "visa",
    "mastercard": "mastercard",
    "credit_card": "credit_card",
    "debit_card": "debit_card",
    "salla_wallet": "salla_wallet",
    "tamara": "tamara",
    "tabby": "tabby",
    "emkan": "emkan",
    "bank_transfer": "bank_transfer",
    "cash_on_delivery": "cod",
}

_SALLA_METRIC_KEYS = frozenset({
    "salla", "mada", "applepay", "googlepay", "stcpay", "visa",
    "mastercard", "credit_card", "debit_card", "salla_wallet",
})

_TAMARA_METRIC_KEYS = frozenset({"tamara"})
_TABBY_METRIC_KEYS = frozenset({"tabby"})


def _configured_fee_rules(settings_doc: dict) -> dict[str, dict]:
    """Translate unified payment-method settings into metric registry keys."""
    rules: dict[str, dict] = {}
    for row in settings_doc.get("payment_methods") or []:
        sub_key, _display, _parent = normalize_payment_method(row.get("name") or "")
        metric_key = _METRIC_KEY_BY_SETTING_SUBKEY.get(sub_key)
        if not metric_key:
            continue
        rules[metric_key] = {
            "estimated_fee_rate": float(row.get("commission_percent") or 0),
            "estimated_fixed_fee": float(row.get("fixed_fee") or 0),
            "estimated_vat_rate": float(row.get("vat_percent") or 0),
        }
    return rules


_SAR_CENT = Decimal("0.01")


def _round_sar(value: Decimal) -> float:
    return float(value.quantize(_SAR_CENT, rounding=ROUND_HALF_UP))


def _round_sar_even(value: Decimal) -> float:
    """Tabby's reports use half-even VAT rounding per displayed fee leg."""
    return float(value.quantize(_SAR_CENT, rounding=ROUND_HALF_EVEN))


# ── Aggregator ────────────────────────────────────────────────────────
async def compute_metrics(
    db,
    user_id: str,
    *,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> dict:
    """Aggregate unified_orders into per-method metrics with proper
    priority. Returns one row per canonical method (zero rows
    omitted) plus a '_other' bucket for unrecognized methods.

    Priority: when `payment_fee_status == 'actual'` (set by the
    settlement-import service) we use `actual_payment_fee`,
    `actual_payment_vat`, `actual_net_amount`, `actual_refund_amount`,
    `actual_partial_refund_amount`. Otherwise we estimate from
    `total_amount` and the registry's estimated_fee_rate.
    """
    match: dict = {"user_id": user_id}
    date_clause: dict = {}
    if from_date:
        date_clause["$gte"] = from_date
    if to_date:
        date_clause["$lte"] = to_date
    if date_clause:
        match["order_date"] = date_clause

    # Iter-207 — Honour the same `report_included_statuses` setting
    # Profit Summary uses, so the two cards report the SAME order
    # universe. Empty list ⇒ no filter (count everything).
    settings_doc = await ensure_user_settings(db, user_id)
    configured_fee_rules = _configured_fee_rules(settings_doc)
    included_statuses = [
        (s or "").strip().lower()
        for s in (settings_doc.get("report_included_statuses") or [])
        if (s or "").strip()
    ]

    pipeline = [
        {"$match": match},
        {"$project": {
            "_id": 0,
            "order_status": 1,
            "total_amount": {"$ifNull": ["$total_amount", 0]},
            "payment_method": {"$ifNull": ["$payment_method", ""]},
            "actual_payment_method": {"$ifNull": ["$actual_payment_method", ""]},
            "payment_fee_status": {"$ifNull": ["$payment_fee_status", "estimated"]},
            "actual_payment_fee": {"$ifNull": ["$actual_payment_fee", 0]},
            "actual_payment_vat": {"$ifNull": ["$actual_payment_vat", 0]},
            "actual_net_amount": {"$ifNull": ["$actual_net_amount", None]},
            "actual_refund_amount": {"$ifNull": ["$actual_refund_amount", 0]},
            "actual_partial_refund_amount": {"$ifNull": ["$actual_partial_refund_amount", 0]},
        }},
    ]

    # Per-method buckets
    buckets: dict[str, dict] = {}

    # Iter-207c — Top-level "excluded" counter for full transparency.
    # Anything that Salla counts but we DON'T (because it was filtered
    # by report_included_statuses OR classified as pending/cancelled)
    # lands here. Surfaced in the response so the UI can show
    # "+X معلَّق/ملغى بقيمة Y" next to the main count.
    excluded_orders_count = 0
    excluded_gross = 0.0
    salla_reference_count = 0
    salla_reference_gross = 0.0

    def _zero():
        return {
            "orders_count": 0,
            "refunded_orders_count": 0,
            "cancelled_orders_count": 0,
            "pending_orders_count": 0,
            "actual_orders_count": 0,
            "gross": 0.0,
            "fees": 0.0,
            "fees_vat": 0.0,
            "refund_full": 0.0,
            "refund_partial": 0.0,
            "pending_gross": 0.0,
            "net": 0.0,           # gross − fees − vat − refunds
            "expected_in_assets": 0.0,  # net  (settles into the gateway account)
        }

    # Iter-83 — Load the user's order-status policy (confirmed/pending/
    # refunded/cancelled). Bucket assignment is then policy-driven so
    # the merchant can re-classify any status from Settings page.
    from order_status_policy import get_policy_map, resolve_category
    policy_overrides = await get_policy_map(db, user_id)

    async for row in db.unified_orders.aggregate(pipeline):
        # Iter-207c — Always count the row in the Salla reference
        # snapshot (before any filtering), so the UI can compare
        # against the platform.
        row_amount = float(row.get("total_amount") or 0)
        salla_reference_count += 1
        salla_reference_gross += row_amount
        # Iter-207 — Pre-filter to mirror Profit Summary's
        # `report_included_statuses` setting (case-insensitive partial
        # match — same semantics as `_matches_any` in server.py).
        if included_statuses:
            os_lc = (row.get("order_status") or "").strip().lower()
            if not os_lc:
                excluded_orders_count += 1
                excluded_gross += row_amount
                continue
            if not any(s in os_lc or os_lc in s
                       for s in included_statuses):
                excluded_orders_count += 1
                excluded_gross += row_amount
                continue
        raw_method = row.get("actual_payment_method") or row.get("payment_method")
        canon = resolve_canonical(raw_method) or "_other"
        bkt = buckets.setdefault(canon, _zero())

        order_status = (row.get("order_status") or "").strip()
        category = resolve_category(order_status, policy_overrides)

        if category == "cancelled":
            bkt["cancelled_orders_count"] += 1
            # Iter-207c — cancelled rows count toward "excluded".
            excluded_orders_count += 1
            excluded_gross += row_amount
            # Five cancellation rows in the merchant's verified Tamara
            # statements each charged the fixed SAR 1.50 only, plus SAR 0.23
            # VAT, and did not count as captured gross.  Preserve that expense
            # even though the cancelled order itself is excluded from sales.
            if canon in _TAMARA_METRIC_KEYS:
                if row.get("payment_fee_status") == "actual":
                    fee = float(row.get("actual_payment_fee") or 0)
                    vat = float(row.get("actual_payment_vat") or 0)
                    actual_net = row.get("actual_net_amount")
                    net = (
                        float(actual_net)
                        if actual_net is not None else -(fee + vat)
                    )
                else:
                    meta = PAYMENT_METHOD_REGISTRY.get(canon) or {}
                    fee_rule = configured_fee_rules.get(canon) or meta
                    fixed_fee = Decimal(str(
                        fee_rule.get("estimated_fixed_fee", 0.0),
                    ))
                    vat_rate = Decimal(str(
                        fee_rule.get("estimated_vat_rate", 0.0),
                    ))
                    fee = _round_sar(fixed_fee)
                    vat = _round_sar(
                        fixed_fee * vat_rate / Decimal("100"),
                    )
                    net = -(fee + vat)
                bkt["fees"] += fee
                bkt["fees_vat"] += vat
                bkt["net"] += net
                bkt["expected_in_assets"] += net
            continue

        gross = float(row.get("total_amount") or 0)

        if category == "pending":
            # Iter-207 — pending orders are tracked SEPARATELY (their
            # own bucket field) and are NOT included in `orders_count`
            # either — they have not generated revenue yet.
            bkt["pending_orders_count"] += 1
            bkt["pending_gross"] += gross
            # Iter-207c — pending counts toward "excluded" too.
            excluded_orders_count += 1
            excluded_gross += row_amount
            continue

        # Iter-207 — only confirmed / refunded orders increment
        # `orders_count`, keeping the per-gateway count consistent with
        # what actually flows into `gross` / `net`.
        bkt["orders_count"] += 1

        is_actual = (row.get("payment_fee_status") == "actual")

        if is_actual:
            bkt["actual_orders_count"] += 1
            fee = float(row.get("actual_payment_fee") or 0)
            vat = float(row.get("actual_payment_vat") or 0)
            rfull = float(row.get("actual_refund_amount") or 0)
            rpart = float(row.get("actual_partial_refund_amount") or 0)
            net = row.get("actual_net_amount")
            if net is None:
                net = gross - fee - vat - rfull - rpart
            else:
                net = float(net)
        else:
            # Estimated using registry rates
            meta = PAYMENT_METHOD_REGISTRY.get(canon) or {}
            fee_rule = configured_fee_rules.get(canon) or meta
            rate = fee_rule.get("estimated_fee_rate", 0.0)
            vat_rate = fee_rule.get("estimated_vat_rate", 0.0)
            # Iter-118 — fixed per-order fee (e.g. Tabby charges 1 SAR
            # per order in addition to the percentage MDR).
            fixed_fee = fee_rule.get("estimated_fixed_fee", 0.0)
            if category == "refunded":
                # Tamara does not rebate the captured-order commission.  Its
                # refund rows carry zero *new* fee but deduct the refund gross;
                # cumulatively the original fee and VAT remain charged.  Other
                # gateways retain the legacy waived-fee estimate.
                if canon in _TAMARA_METRIC_KEYS:
                    unrounded_fee = (
                        Decimal(str(gross)) * Decimal(str(rate))
                        / Decimal("100")
                        + Decimal(str(fixed_fee))
                    )
                    fee = _round_sar(unrounded_fee)
                    vat = _round_sar(
                        Decimal(str(fee)) * Decimal(str(vat_rate))
                        / Decimal("100")
                    )
                elif canon in _TABBY_METRIC_KEYS:
                    # Tabby reverses only the 4.99% refundable fee leg.
                    # The 2.00% non-refundable leg and SAR 1 fixed charge,
                    # together with their VAT, remain after a full refund.
                    gross_d = Decimal(str(gross))
                    total_rate_d = Decimal(str(rate))
                    refundable_rate_d = min(
                        total_rate_d,
                        Decimal(str(
                            (PAYMENT_METHOD_REGISTRY.get(canon) or {}).get(
                                "estimated_refundable_fee_rate", 4.99,
                            ),
                        )),
                    )
                    nonrefundable_rate_d = max(
                        total_rate_d - refundable_rate_d,
                        Decimal("0"),
                    )
                    nonrefundable_fee = Decimal(str(_round_sar(
                        gross_d * nonrefundable_rate_d / Decimal("100"),
                    )))
                    fixed_fee_d = Decimal(str(fixed_fee))
                    fee = float(nonrefundable_fee + fixed_fee_d)
                    vat_rate_d = Decimal(str(vat_rate)) / Decimal("100")
                    vat = _round_sar(
                        Decimal(str(_round_sar_even(
                            nonrefundable_fee * vat_rate_d,
                        )))
                        + Decimal(str(_round_sar_even(
                            fixed_fee_d * vat_rate_d,
                        )))
                    )
                else:
                    fee = 0.0
                    vat = 0.0
                rfull = gross
                rpart = 0.0
                net = -fee - vat
            else:
                # confirmed
                if canon in _SALLA_METRIC_KEYS:
                    unrounded_fee = (
                        Decimal(str(gross)) * Decimal(str(rate)) / Decimal("100")
                        + Decimal(str(fixed_fee))
                    )
                    fee = _round_sar(unrounded_fee)
                    vat = _round_sar(
                        unrounded_fee * Decimal(str(vat_rate)) / Decimal("100")
                    )
                elif canon in _TAMARA_METRIC_KEYS:
                    # Tamara rounds the percentage fee to halalas per capture,
                    # adds the fixed fee, then calculates VAT on that displayed
                    # rounded total fee.  This differs from Salla's unrounded
                    # VAT basis and is verified by all 298 captured rows.
                    unrounded_fee = (
                        Decimal(str(gross)) * Decimal(str(rate))
                        / Decimal("100")
                        + Decimal(str(fixed_fee))
                    )
                    fee = _round_sar(unrounded_fee)
                    vat = _round_sar(
                        Decimal(str(fee)) * Decimal(str(vat_rate))
                        / Decimal("100")
                    )
                elif canon in _TABBY_METRIC_KEYS:
                    # Four merchant reports (225 captures) prove Tabby
                    # rounds its 4.99% refundable and 2.00% non-refundable
                    # legs separately, then adds SAR 1.00.  VAT is rounded
                    # half-even per displayed fee leg and summed.
                    gross_d = Decimal(str(gross))
                    total_rate_d = Decimal(str(rate))
                    refundable_rate_d = min(
                        total_rate_d,
                        Decimal(str(
                            (PAYMENT_METHOD_REGISTRY.get(canon) or {}).get(
                                "estimated_refundable_fee_rate", 4.99,
                            ),
                        )),
                    )
                    nonrefundable_rate_d = max(
                        total_rate_d - refundable_rate_d,
                        Decimal("0"),
                    )
                    refundable_fee = Decimal(str(_round_sar(
                        gross_d * refundable_rate_d / Decimal("100"),
                    )))
                    nonrefundable_fee = Decimal(str(_round_sar(
                        gross_d * nonrefundable_rate_d / Decimal("100"),
                    )))
                    fixed_fee_d = Decimal(str(fixed_fee))
                    fee = float(
                        refundable_fee + nonrefundable_fee + fixed_fee_d
                    )
                    vat_rate_d = Decimal(str(vat_rate)) / Decimal("100")
                    vat = _round_sar(
                        Decimal(str(_round_sar_even(
                            refundable_fee * vat_rate_d,
                        )))
                        + Decimal(str(_round_sar_even(
                            nonrefundable_fee * vat_rate_d,
                        )))
                        + Decimal(str(_round_sar_even(
                            fixed_fee_d * vat_rate_d,
                        )))
                    )
                else:
                    fee = round(gross * rate / 100 + fixed_fee, 4)
                    vat = round(fee * vat_rate / 100, 4)
                rfull = 0.0
                rpart = 0.0
                net = gross - fee - vat

        if rfull > 0 or rpart > 0:
            bkt["refunded_orders_count"] += 1

        bkt["gross"] += gross
        bkt["fees"] += fee
        bkt["fees_vat"] += vat
        bkt["refund_full"] += rfull
        bkt["refund_partial"] += rpart
        bkt["net"] += net
        bkt["expected_in_assets"] += net

    # Materialize result rows in registry order (deterministic), drop
    # all-zero buckets so the UI stays uncluttered.  Iter-207 — a row
    # is kept if it has ANY activity (confirmed/refunded/pending/
    # cancelled) so a gateway that received only pending orders still
    # surfaces, even though it doesn't contribute to gross yet.
    rows: list[dict] = []
    for key, meta in PAYMENT_METHOD_REGISTRY.items():
        b = buckets.get(key)
        if not b:
            continue
        if (b["orders_count"] + b["pending_orders_count"]
                + b["cancelled_orders_count"]) == 0:
            continue
        rows.append({
            "key": key,
            "name_ar": meta["name_ar"],
            "type": meta["type"],
            "orders_count": b["orders_count"],
            "actual_orders_count": b["actual_orders_count"],
            "refunded_orders_count": b["refunded_orders_count"],
            "cancelled_orders_count": b["cancelled_orders_count"],
            "pending_orders_count": b["pending_orders_count"],
            "gross": round(b["gross"], 2),
            "fees": round(b["fees"], 2),
            "fees_vat": round(b["fees_vat"], 2),
            "refund_full": round(b["refund_full"], 2),
            "refund_partial": round(b["refund_partial"], 2),
            "refund_total": round(b["refund_full"] + b["refund_partial"], 2),
            "pending_gross": round(b["pending_gross"], 2),
            "net": round(b["net"], 2),
            "expected_in_assets": round(b["expected_in_assets"], 2),
            "coverage_pct": round(
                (b["actual_orders_count"] / b["orders_count"]) * 100, 2,
            ) if b["orders_count"] else 0.0,
        })

    # Tail bucket for unrecognized methods so totals reconcile even
    # when the merchant has a new gateway not in the registry yet.
    other = buckets.get("_other")
    if other and other["orders_count"] > 0:
        rows.append({
            "key": "_other",
            "name_ar": "أخرى",
            "type": "unknown",
            "orders_count": other["orders_count"],
            "actual_orders_count": other["actual_orders_count"],
            "refunded_orders_count": other["refunded_orders_count"],
            "cancelled_orders_count": other["cancelled_orders_count"],
            "pending_orders_count": other["pending_orders_count"],
            "gross": round(other["gross"], 2),
            "fees": round(other["fees"], 2),
            "fees_vat": round(other["fees_vat"], 2),
            "refund_full": round(other["refund_full"], 2),
            "refund_partial": round(other["refund_partial"], 2),
            "refund_total": round(other["refund_full"] + other["refund_partial"], 2),
            "pending_gross": round(other["pending_gross"], 2),
            "net": round(other["net"], 2),
            "expected_in_assets": round(other["expected_in_assets"], 2),
            "coverage_pct": 0.0,
        })

    totals = {
        "gross": round(sum(r["gross"] for r in rows), 2),
        "fees": round(sum(r["fees"] for r in rows), 2),
        "fees_vat": round(sum(r["fees_vat"] for r in rows), 2),
        "refund_full": round(sum(r["refund_full"] for r in rows), 2),
        "refund_partial": round(sum(r["refund_partial"] for r in rows), 2),
        "refund_total": round(sum(r["refund_total"] for r in rows), 2),
        "pending_gross": round(sum(r["pending_gross"] for r in rows), 2),
        "net": round(sum(r["net"] for r in rows), 2),
        "orders_count": sum(r["orders_count"] for r in rows),
        "actual_orders_count": sum(r["actual_orders_count"] for r in rows),
        "refunded_orders_count": sum(r["refunded_orders_count"] for r in rows),
        "cancelled_orders_count": sum(r["cancelled_orders_count"] for r in rows),
        "pending_orders_count": sum(r["pending_orders_count"] for r in rows),
        # Iter-207c — transparency block: lets the UI render the
        # "+X معلَّق/ملغى بقيمة Y ر.س" badge next to the main count
        # and a tooltip explaining the gap with the Salla platform.
        "excluded_orders_count": int(excluded_orders_count),
        "excluded_gross": round(excluded_gross, 2),
        "salla_reference_orders_count": int(salla_reference_count),
        "salla_reference_gross": round(salla_reference_gross, 2),
    }

    return {
        "from_date": from_date,
        "to_date": to_date,
        "rows": rows,
        "totals": totals,
        "registry": [
            {"key": k, "name_ar": v["name_ar"], "type": v["type"],
             "estimated_fee_rate": v["estimated_fee_rate"]}
            for k, v in PAYMENT_METHOD_REGISTRY.items()
        ],
    }


# ── Route ─────────────────────────────────────────────────────────────
def attach_payment_gateway_metrics_routes(api_router: APIRouter, db) -> None:
    router = APIRouter(tags=["payment-gateway-metrics"])

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    @router.get("/payment-gateway-metrics")
    async def get_metrics(
        from_date: Optional[str] = Query(default=None),
        to_date: Optional[str] = Query(default=None),
        user: dict = Depends(current_user),
    ):
        return await compute_metrics(db, user["id"], from_date=from_date, to_date=to_date)

    api_router.include_router(router)
