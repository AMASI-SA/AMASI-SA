"""Read PayPal Activity Download CSV as review-only financial evidence.

The official Activity Download is CSV rather than the XLSX formats of the
other providers. Every activity row is *unsupported for automatic accounting*:
withdrawals, conversions and reversals cannot be mistaken for captured sales.
No PII from the Activity Download is stored in normalized rows.
"""
from collections import Counter
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from io import StringIO
import csv
import json
import re


REQUIRED = {"Date", "Type", "Status", "Currency", "Gross", "Fee", "Net", "Transaction ID"}
MAX_ROWS = 10000
MAX_BYTES = 10 * 1024 * 1024
MAX_EVIDENCE_BYTES = 8 * 1024 * 1024


def _digest(data):
    return sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def _day(text):
    value = str(text or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    # Locale-ambiguous strings are not silently interpreted as payment dates.
    return None


def _money(value, issues):
    from operational_provider_statement_intake import _amount
    return _amount(value, issues)


def normalize_paypal_csv(content: bytes) -> dict:
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_BYTES:
        raise ValueError("statement_file_invalid")
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeError:
        raise ValueError("statement_file_invalid") from None
    if "\x00" in decoded:
        raise ValueError("statement_file_invalid")
    reader = csv.reader(StringIO(decoded, newline=""), strict=True)
    try:
        headers = next(reader)
    except (StopIteration, csv.Error):
        raise ValueError("statement_provider_unknown") from None
    headers = [str(h).strip() for h in headers]
    if not REQUIRED.issubset(headers):
        raise ValueError("statement_provider_unknown")
    if len(headers) != len(set(headers)) or len(headers) > 100:
        raise ValueError("statement_header_ambiguous")
    indices = {key: index for index, key in enumerate(headers)}
    rows = []
    fingerprints = Counter()
    transactions = {}
    currencies = set()
    net_total = Decimal("0")
    try:
        for line, cells in enumerate(reader, start=2):
            if line > MAX_ROWS + 1:
                raise ValueError("statement_row_limit")
            if not any(str(value).strip() for value in cells):
                continue
            if len(cells) != len(headers) or any(len(value) > 10000 for value in cells):
                raise ValueError("statement_file_invalid")
            get = lambda key: cells[indices[key]] if key in indices else None
            txid = str(get("Transaction ID") or "").strip()
            if not txid or len(txid) > 128 or re.search(r"[\x00-\x1f\x7f]", txid):
                # Never produce a customer-sale identity from an unverified row.
                raise ValueError("statement_order_reference_invalid")
            issues = ["statement_event_unsupported"]
            gross, signed_fee, net = (_money(get(k), issues) for k in ("Gross", "Fee", "Net"))
            ccy = str(get("Currency") or "").strip().upper()
            if not re.fullmatch(r"[A-Z]{3}", ccy):
                issues.append("statement_currency_missing")
                ccy = None
            else:
                currencies.add(ccy)
            status = str(get("Status") or "").strip()
            kind = str(get("Type") or "").strip()
            if not status or not kind or len(status) > 127 or len(kind) > 160:
                issues.append("statement_event_unsupported")
            when = _day(get("Date"))
            if not when:
                issues.append("statement_event_date_invalid")
            amount = format(abs(Decimal(gross)), ".2f") if gross is not None else None
            fee = format(-Decimal(signed_fee), ".2f") if signed_fee is not None else None
            if gross is not None and signed_fee is not None and net is not None:
                if Decimal(gross) + Decimal(signed_fee) != Decimal(net):
                    issues.append("statement_transfer_mismatch")
            fingerprint = _digest({"provider": "paypal", "transaction_id": txid,
                "currency": ccy, "date": when, "type": kind, "status": status,
                "gross": gross, "signed_fee": signed_fee, "net": net})
            fingerprints[fingerprint] += 1
            transactions.setdefault(txid, []).append(fingerprint)
            if net is not None:
                net_total += Decimal(net)
            rows.append({
                "id": f"row:{line}", "source_row": line, "provider": "paypal",
                "order_number": None, "provider_order_id": txid,
                "event_type": "unsupported", "event_reference": txid,
                "event_date": when, "date_precision": "day" if when else None,
                "native_currency": ccy, "principal_amount": amount,
                "fee_net": fee, "fee_vat": None, "fee_total": None,
                "transferred_amount": net, "fee_variable_net": None,
                "fee_fixed_net": None, "fee_vat_rate": None,
                "fee_component_issues": ["statement_fee_components_unavailable"],
                "payment_method": "paypal", "plan_id": None,
                "installments": None, "merchant_account": None,
                "statement_reference": None, "blockers": sorted(set(issues)),
                "row_fingerprint": fingerprint,
            })
    except csv.Error:
        raise ValueError("statement_file_invalid") from None
    if not rows:
        raise ValueError("statement_rows_missing")
    for row in rows:
        if fingerprints[row["row_fingerprint"]] > 1 or len(transactions[row["event_reference"]]) > 1:
            row["blockers"].append("statement_duplicate_row_ambiguous")
        if len(currencies) > 1:
            row["blockers"].append("statement_currency_conflict")
        row["blockers"] = sorted(set(row["blockers"]))
    currency = next(iter(currencies)) if len(currencies) == 1 else None
    # PayPal Activity Download includes currency transfers, withdrawals and
    # reversals: aggregated 'sales', 'fees' or bank settlements are not proven.
    totals = {key: None for key in ("capture_principal", "refund_principal",
        "fee_net", "fee_vat", "fee_total", "transferred_amount",
        "payout_fee_total", "wallet_deductions")}
    result = {"schema_version": "operational_statement_v1", "provider": "paypal",
              "statement_reference": None, "merchant_account": None,
              "currency": currency, "statement_date": None,
              "rows": rows, "totals": totals}
    result["fingerprint"] = _digest({"schema_version": result["schema_version"],
        "provider": "paypal", "rows": sorted(row["row_fingerprint"] for row in rows)})
    result["file_hash"] = sha256(content).hexdigest()
    if len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()) > MAX_EVIDENCE_BYTES:
        raise ValueError("statement_evidence_size_limit")
    return result
