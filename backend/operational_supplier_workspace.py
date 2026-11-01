"""Read-only supplier view of the existing operational balance report.

The report remains the balance authority. This projection never reads invoice
collections, creates a payable, refreshes sources, or enables native GL writes.
"""
from collections import defaultdict
from decimal import Decimal

from operational_balance_service import report


BALANCE_FIELDS = (
    "opening", "expected_payable", "confirmed_payable", "settled",
    "outstanding_payable", "outstanding_receivable",
)
INVOICE_AMOUNTS = ("gross_amount", "returned_amount", "confirmed", "settled", "outstanding")


def _amount(value):
    return format(value, ".2f")


def _supplier_source_issue(issue):
    code = str(issue.get("code", "") if isinstance(issue, dict) else issue)
    source = str(issue.get("source_id", "") if isinstance(issue, dict) else "")
    return (source.startswith(("receipt:", "supplier:"))
            or any(term in code for term in ("supplier", "invoice", "canonical",
                                            "operational_refresh", "operational_source")))


def supplier_workspace(state):
    result = {
        "authority": "operational_balance_report", "status": state["status"],
        "active": state["status"] == "active", "started_at": state.get("started_at"),
        "as_of": state.get("engine", {}).get("as_of"),
        "accounts": [], "invoices": [], "openings": [], "summaries": {},
        "invoice_provenance_incomplete": False, "source_issues_count": 0,
    }
    if not result["active"]:
        return result

    snapshot = report(state)
    for row in snapshot["parties"]:
        if row["party_type"] != "supplier" or not row.get("party_id"):
            continue
        result["accounts"].append({
            "supplier_id": row["party_id"], "name": row["name"], "currency": row["currency"],
            **{field: row[field] for field in BALANCE_FIELDS},
        })

    grouped = {}
    for row in snapshot["obligations"]:
        if row["party_type"] != "supplier" or not row.get("party_id"):
            continue
        if row.get("kind") == "baseline":
            result["openings"].append({
                "id": row["id"], "supplier_id": row["party_id"], "currency": row["currency"],
                "direction": row["direction"], "amount": row["confirmed"],
                "settled": row["settled"], "outstanding": row["outstanding"],
                "approved_at": row.get("approved_at"),
            })
            continue
        if row.get("kind") != "supplier" or row.get("derived_credit") or row.get("direction") != "payable":
            continue
        if not row.get("evidence_ids"):
            continue
        identity = row.get("source_invoice_id")
        if not identity:
            result["invoice_provenance_incomplete"] = True
            continue
        key = (row["party_id"], row["currency"], identity)
        invoice = grouped.setdefault(key, {
            "id": identity, "supplier_id": row["party_id"], "currency": row["currency"],
            "invoice_number": row.get("source_invoice_number") or None,
            "approved_at": row.get("source_invoice_approved_at"),
            **{field: Decimal(0) for field in INVOICE_AMOUNTS},
        })
        for field in INVOICE_AMOUNTS:
            value = row.get(field)
            invoice[field] = (invoice[field] + Decimal(value)
                              if invoice[field] is not None and value is not None else None)
    result["invoices"] = [
        {**row, **{field: _amount(row[field]) if row[field] is not None else None for field in INVOICE_AMOUNTS}}
        for row in grouped.values()
    ]
    result["invoices"].sort(key=lambda row: (str(row.get("approved_at") or ""), row["id"]), reverse=True)

    totals = defaultdict(lambda: {"payable": Decimal(0), "receivable": Decimal(0),
                                  "settled": Decimal(0), "invoice_count": 0})
    for account in result["accounts"]:
        total = totals[account["currency"]]
        for source, target in (("outstanding_payable", "payable"),
                               ("outstanding_receivable", "receivable"), ("settled", "settled")):
            total[target] += Decimal(account[source])
    for invoice in result["invoices"]:
        totals[invoice["currency"]]["invoice_count"] += 1
    result["summaries"] = {
        currency: {field: _amount(value) if isinstance(value, Decimal) else value
                   for field, value in values.items()} for currency, values in sorted(totals.items())
    }
    result["source_issues_count"] = sum(_supplier_source_issue(issue) for issue in snapshot["issues"])
    return result
