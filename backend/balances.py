"""Shipping & COD balance computations split by status approval.

Per the requirements:
- Approved shipping balance  = sum of shipping_cost for orders whose order_status
  is in the merchant's `shipping_approved_statuses` setting.
- Unapproved shipping balance = sum for orders in any other status.
- Approved COD = sum of total_amount for COD orders whose order_status is in
  `cod_approved_statuses`. Unapproved COD = the rest.

Each split is grouped per shipping company and per status, with a total.
The data source is the unified_orders collection so Excel + Make are merged.
"""
from typing import Iterable


COD_KEYWORDS = (
    "عند الاستلام", "عند الاستلم", "الدفع عند الاستلام",
    "cod", "cash on delivery", "cash_on_delivery",
)


def _is_cod_method(payment_method: str) -> bool:
    if not payment_method:
        return False
    lc = payment_method.strip().lower()
    return any(k in lc for k in COD_KEYWORDS)


def _norm_status(s: str) -> str:
    return (s or "").strip().lower()


def _match_status(status: str, approved: Iterable[str]) -> bool:
    s = _norm_status(status)
    if not s:
        return False
    for a in approved or []:
        a_norm = _norm_status(a)
        if a_norm and (a_norm == s or a_norm in s or s in a_norm):
            return True
    return False


def compute_balances(orders: list[dict], shipping_approved: list[str],
                     cod_approved: list[str]) -> dict:
    """Return {shipping: {...}, cod: {...}} accounting splits.

    Each side has: total_approved, total_unapproved, by_company {name: {...}},
    by_status {name: {...}}.
    """
    shipping = {
        "total_approved": 0.0,
        "total_unapproved": 0.0,
        "by_company": {},
        "by_status": {},
        "approved_orders": 0,
        "unapproved_orders": 0,
    }
    cod = {
        "total_approved": 0.0,
        "total_unapproved": 0.0,
        "by_company": {},
        "by_status": {},
        "approved_orders": 0,
        "unapproved_orders": 0,
    }

    for o in orders:
        status = (o.get("order_status") or "").strip()
        slug = (o.get("order_status_slug") or "").strip()
        # Check approval against BOTH name and slug for resilience
        is_ship_approved = (
            _match_status(status, shipping_approved)
            or _match_status(slug, shipping_approved)
        )
        is_cod_approved = (
            _match_status(status, cod_approved)
            or _match_status(slug, cod_approved)
        )

        company = (o.get("shipping_company") or "—").strip() or "—"
        ship_cost = float(o.get("shipping_cost") or 0)

        # Shipping bucket
        cbucket = shipping["by_company"].setdefault(
            company, {"name": company, "approved": 0.0, "unapproved": 0.0, "orders": 0}
        )
        sbucket = shipping["by_status"].setdefault(
            status or "—", {"name": status or "—", "amount": 0.0, "orders": 0}
        )
        cbucket["orders"] += 1
        sbucket["orders"] += 1
        sbucket["amount"] += ship_cost
        if is_ship_approved:
            shipping["total_approved"] += ship_cost
            shipping["approved_orders"] += 1
            cbucket["approved"] += ship_cost
        else:
            shipping["total_unapproved"] += ship_cost
            shipping["unapproved_orders"] += 1
            cbucket["unapproved"] += ship_cost

        # COD bucket
        if _is_cod_method(o.get("payment_method") or ""):
            cod_amount = float(o.get("total_amount") or 0)
            cc = cod["by_company"].setdefault(
                company, {"name": company, "approved": 0.0, "unapproved": 0.0, "collected": 0.0, "orders": 0}
            )
            cs = cod["by_status"].setdefault(
                status or "—", {"name": status or "—", "amount": 0.0, "orders": 0}
            )
            cc["orders"] += 1
            cs["orders"] += 1
            cs["amount"] += cod_amount
            if is_cod_approved:
                cod["total_approved"] += cod_amount
                cod["approved_orders"] += 1
                cc["approved"] += cod_amount
            else:
                cod["total_unapproved"] += cod_amount
                cod["unapproved_orders"] += 1
                cc["unapproved"] += cod_amount

    # Round everything
    def _round(d):
        return {k: round(v, 2) if isinstance(v, float) else v for k, v in d.items()}

    shipping["total_approved"] = round(shipping["total_approved"], 2)
    shipping["total_unapproved"] = round(shipping["total_unapproved"], 2)
    shipping["by_company"] = [_round(v) for v in shipping["by_company"].values()]
    shipping["by_status"] = [_round(v) for v in shipping["by_status"].values()]

    cod["total_approved"] = round(cod["total_approved"], 2)
    cod["total_unapproved"] = round(cod["total_unapproved"], 2)
    cod["by_company"] = [_round(v) for v in cod["by_company"].values()]
    cod["by_status"] = [_round(v) for v in cod["by_status"].values()]

    return {"shipping": shipping, "cod": cod}
