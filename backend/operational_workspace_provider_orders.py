"""A scoped order drill-down from the same snapshot that prices each provider."""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from operational_financial_activity_runtime import checked_rows


def money(value):
    return format(value, ".2f") if value is not None else None


def business_date(stamp):
    if not stamp:
        return None
    try:
        value = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        return value.astimezone(ZoneInfo("Asia/Riyadh")).date().isoformat() if value.tzinfo else None
    except ValueError:
        return None


def provider_position(computed, provider, *, show_orders):
    """Keep partial known SAR separate from unresolved foreign balances."""
    scopes = [(currency, r) for currency, group in computed["details"]["provider_balances"].items()
              for r in group if r["provider"] == provider]
    sar = next((r for currency, r in scopes if currency == "SAR"), {})
    foreign = any(currency != "SAR" for currency, _ in scopes)
    complete = bool(sar.get("complete")) and not foreign
    coverage = computed["details"].get("customer_tender_coverage", {})
    if not scopes and provider == "paypal" and coverage.get("paypal_provider_recognized") and coverage.get("uninspected_source_orders") == 0:
        complete = True
        sar = {"net": "0.00", "known_net": "0.00", "paid_orders": 0}
    result = {"currency": "SAR", "complete": complete, "net": sar.get("net") if complete else None,
              "known_net": sar.get("known_net"), "foreign_unconverted": foreign, "as_of": computed.get("as_of")}
    if show_orders:
        count_known = scopes and all(row.get("paid_orders") is not None and row.get("paid_orders_complete")
                                     and row["paid_orders"] == len(row.get("paid_order_ids", [])) for _, row in scopes)
        result["paid_orders"] = (len({oid for _, r in scopes for oid in r.get("paid_order_ids", [])})
                                 if count_known else (sar.get("paid_orders") if not scopes else None))
    return result


class ProviderOrders:
    """Collect already-priced rows only when opening one authorized provider."""
    def __init__(self, provider, state):
        self.provider = provider
        self.order_sources = state.get("engine", {}).get("provider_fee_inputs", {}).get("orders", {})
        self.rows = {}
        self.identities = {}

    def get(self, oid, number=None):
        source = self.order_sources.get(oid, {})
        entry = self.rows.setdefault(oid, {
            "id": oid, "order_number": None, "day": business_date(source.get("created_at")),
            "reference": None, "paid": None, "paid_sar": None, "currency": None,
            "balance_currency": None, "remaining_fee": None, "fee_basis": None, "settled": None, "net": None, "reasons": [],
        })
        if number or source.get("order_number"):
            entry["order_number"] = str(number or source["order_number"])
        return entry

    def observe(self, identity, row, amount, claim, allocation):
        if row["provider"] != self.provider:
            return
        oid = row["order_id"]
        entry = self.get(oid, row.get("order_number"))
        if oid in self.identities:
            self.identities[oid] = None
            entry.update(paid=None, paid_sar=None, settled=None, remaining_fee=None, net=None)
            self.reason(oid, "تحتاج دفعات هذا الطلب إلى مطابقة مراجعها.")
            return
        self.identities[oid] = identity
        payment = row.get("payment") or {}
        page = payment.get("order_page_balance") or {}
        if not entry["day"]:
            entry["day"] = (business_date(row.get("created_at") or page.get("created_at") or (claim or {}).get("created_at"))
                            or (claim or {}).get("business_date"))
        entry.update(currency=row["native_currency"], balance_currency=row["currency"],
                     reference=page.get("transaction_reference") or payment.get("order_page_reference") or payment.get("reference")
                               or payment.get("transaction_reference"))
        if row["paid_proven"]:
            entry["paid"] = money(amount if row["native_currency"] == row["currency"] else row["native_gross"])
            if row["currency"] == "SAR":
                entry["paid_sar"] = money(amount)
            elif amount > 0:
                self.reason(oid, "قيمة الطلب بالريال تحتاج إلى سعر تحويل موثق.")
        if claim:
            entry["settled"] = money(allocation["gross"])

    def priced(self, identity, row, claim, fee, basis):
        if row["provider"] == self.provider and self.identities.get(row["order_id"]) == identity and claim:
            entry = self.rows[row["order_id"]]
            sign = 1 if claim["direction"] == "receivable" else -1
            entry["net"] = money(sign * Decimal(claim["outstanding"]) - fee)
            entry["remaining_fee"] = money(fee)
            entry["fee_basis"] = basis

    def credit(self, obligation):
        """Overpaid claims carry their excess on a separate derived credit."""
        original = obligation.get("original_obligation_id")
        if not original or obligation.get("party_id") != self.provider or not obligation.get("derived_credit"):
            return
        oid = original.removeprefix("provider:").removesuffix(":" + self.provider)
        entry = self.rows.get(oid)
        if self.identities.get(oid) == original and entry and entry["net"] is not None:
            sign = 1 if obligation["direction"] == "receivable" else -1
            entry["net"] = money(Decimal(entry["net"]) + sign * Decimal(obligation["outstanding"]))

    def reason(self, oid, message, number=None):
        entry = self.get(oid, number)
        if message not in entry["reasons"]:
            entry["reasons"].append(message)


def filtered_orders(rows, q, date_from, date_to):
    needle = q.strip().casefold()
    result = []
    undated = 0
    for row in checked_rows(rows):
        if needle and not any(needle in str(row[k] or "").casefold() for k in ("order_number", "id", "reference")):
            continue
        if date_from and not row["day"]:
            undated += 1
            continue
        if date_from and not date_from <= row["day"] <= date_to:
            continue
        result.append(row)
    result.sort(key=lambda row: (row["day"] or "", row["order_number"] or row["id"]), reverse=True)
    return result, undated
