"""Generate synthetic display data through the real read-only report adapter.

Run from repository root with PYTHONPATH=backend. No database or live data.
"""
import json
from pathlib import Path

from operational_balance_store import initial
from operational_financial_position import build_financial_position


START = "2026-11-01T00:00:00+03:00"
STAMP = "2026-11-01T03:00:00+03:00"
state = initial("preview-only-synthetic-owner")
state.update(status="active", started_at=START)
state["engine"] = {"as_of": STAMP, "obligations": {}, "facts": {}, "issues": [], "cod_reports": {}}


def opening(key, kind, name, amount, direction="for_us", currency="SAR"):
    state["openings"][key] = {"id": key, "party_id": key, "party_type": kind, "name": name,
                              "amount": amount, "direction": direction, "currency": currency}


for args in [
    ("bank-main", "bank", "البنك الرئيسي", "65000.00"),
    ("bank-operating", "bank", "البنك التشغيلي", "35000.00"),
    ("network", "pos_receivable", "تسويات الشبكة", "6000.00"),
    ("cash-main", "cash", "الصندوق الرئيسي", "6000.00"),
    ("cash-operating", "cash", "صندوق التشغيل", "4000.00"),
    ("supplier-a", "supplier", "المورد أ", "25000.00", "for_party"),
    ("supplier-b", "supplier", "المورد ب", "15000.00", "for_party"),
    ("supplier-c", "supplier", "المورد ج", "2000.00"),
    ("employee-a", "employee", "الموظف أ", "3000.00"),
    ("employee-b", "employee", "الموظف ب", "8000.00", "for_party"),
    ("external-a", "external_person", "عميل آجل", "15000.00"),
    ("external-b", "external_person", "جهة تمويل", "12000.00", "for_party"),
    ("ad-snap", "ad_account", "حساب إعلاني أ", "2000.00"),
    ("ad-meta", "ad_account", "حساب إعلاني ب", "1000.00"),
    ("ad-due", "ad_account", "حساب إعلاني آجل", "2000.00", "for_party"),
    ("custody-a", "employee_custody", "عهدة تشغيلية", "500.00"),
    ("provider-a", "provider", "منصة دفع أ", "7000.00"),
    ("bank-usd", "bank", "حساب مستقل بالدولار", "1250.50", "for_us", "USD"),
]:
    opening(*args)

for index, (kind, method, approved) in enumerate([
    ("store_driver", "cash", True),
    ("store_driver", "bank_transfer", False),
    ("store_driver", "card_terminal", True),
    ("courier", "cod", True),
], 1):
    order, party = str(900000 + index), f"executor-{index}"
    name = "شركة الشحن أ" if kind == "courier" else f"مندوب تجريبي {index}"
    custody = "200.00" if method in {"cash", "cod"} else "0.00"
    evidence = {"evidence_complete": True, "collection_confirmed": approved,
                "payment_method": method, "gross": "300.00", "collected": "300.00" if approved else "100.00",
                "outstanding": "0.00" if approved else "200.00", "custody_amount": custody,
                "collection_amount": "200.00", "evidence_ids": [f"proof-{index}"]}
    facts = state["engine"]["facts"]
    facts[f"shipping:{order}"] = {"id": party, "party_type": kind, "cost": "20.00", "delivered_at": STAMP}
    facts[f"cod_collection:{order}"] = {"party_id": party, "amount": custody, "evidence": evidence}
    common = {"party_id": party, "party_type": kind, "name": name, "currency": "SAR",
              "business_date": "2026-11-01", "expected": "0.00"}
    state["engine"]["obligations"][f"shipping:{order}"] = {
        **common, "id": f"shipping:{order}", "kind": "shipping", "direction": "payable", "confirmed": "20.00"}
    state["engine"]["obligations"][f"cod:{order}"] = {
        **common, "id": f"cod:{order}", "kind": "cod", "direction": "receivable", "confirmed": custody}
    state["engine"]["cod_reports"][f"cod:{order}"] = {
        "order_id": order, "party_id": party, "party_type": kind, "currency": "SAR",
        "gross": evidence["gross"], "collected": evidence["collected"],
        "customer_outstanding": evidence["outstanding"], "custody_amount": custody,
        "collection_amount": "200.00", "payment_method": method, "evidence_ids": evidence["evidence_ids"]}

costs = {
    "status": "preview", "as_of": STAMP, "engine_as_of": STAMP, "started_at": START,
    "complete": True, "issues": [],
    "currencies": {"SAR": {
        "prestart_supplier_unreceived": "18000.00", "poststart_supplier_unreceived": "7000.00",
        "prestart_supplier_received_uninvoiced": "1000.00", "poststart_supplier_received_uninvoiced": "500.00",
        "prestart_shipping_expected": "4800.00", "current_shipping_expected": "1200.00"}},
    "groups": [
        {"period": "prestart", "kind": "supplier", "stage": "unreceived", "party_id": None,
         "party_name": "منتجات لم تستلم بعد", "party_type": "supplier", "currency": "SAR", "amount": "18000.00", "units": 100, "orders": 80},
        {"period": "poststart", "kind": "supplier", "stage": "unreceived", "party_id": None,
         "party_name": "منتجات قيد التنفيذ", "party_type": "supplier", "currency": "SAR", "amount": "7000.00", "units": 40, "orders": 30},
        {"period": "prestart", "kind": "supplier", "stage": "received_without_approved_invoice", "party_id": "supplier-a",
         "party_name": "المورد أ", "party_type": "supplier", "currency": "SAR", "amount": "1000.00", "units": 5, "orders": 4},
        {"period": "poststart", "kind": "supplier", "stage": "received_without_approved_invoice", "party_id": "supplier-b",
         "party_name": "المورد ب", "party_type": "supplier", "currency": "SAR", "amount": "500.00", "units": 2, "orders": 2},
        {"period": "prestart", "kind": "courier", "stage": "not_delivered", "party_id": "courier-a",
         "party_name": "شحن خارجي", "party_type": "courier", "currency": "SAR", "amount": "4800.00", "units": 120, "orders": 120},
        {"period": "poststart", "kind": "shipping", "stage": "not_delivered", "party_id": "driver-a",
         "party_name": "توصيل محلي", "party_type": "shipping", "currency": "SAR", "amount": "1200.00", "units": 40, "orders": 40},
    ],
}
result = build_financial_position(state, cost_exposure=costs, generated_at=STAMP)
result.update(session_scope="synthetic-preview-only", permissions={"reports": True})
# Known display labels for this isolated fixture, never a production ID fallback.
for currency in result["currencies"].values():
    for row in currency["deliveries"]["rows"]:
        row["order_number"] = "TEST-" + row["order_id"]
path = Path(__file__).with_name("fixture.json")
path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
print(f"Synthetic fixture generated: {path.name}")
