"""Search only the fields already authorized in the workspace ledger projection."""
import re
import unicodedata

from operational_balance_store import fail
from operational_financial_activity_runtime import checked_rows
from operational_workspace_projection import strict_day


_LOOKUP = str.maketrans("أإآٱى٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "ااااي01234567890123456789", "ـ")
_MARKS = re.compile(r"[\u064b-\u065f\u0670]")


def search_text(value):
    return _MARKS.sub("", unicodedata.normalize("NFKC", str(value or "")).translate(_LOOKUP)).strip().casefold()


def check_period(first, last):
    if first is None and last is None:
        return
    try:
        if strict_day(first) <= strict_day(last):
            return
    except (TypeError, ValueError):
        pass
    fail("operational_statement_dates_invalid", "اختر فترة صحيحة من تاريخ إلى تاريخ", 422)


def movement_type(item):
    if item.get("kind") == "transfer" or item.get("workspace_reason") == "transfer":
        return "transfer"
    if item.get("kind") == "settlement_fee":
        return "out"
    if item.get("kind") == "refund" and item.get("status") in {"refunded", "salla_refund_confirmed"}:
        return "out"
    if item.get("kind") in {"correction", "opening"} and not item.get("driver_review_event_id"):
        return None
    return {"incoming": "in", "outgoing": "out"}.get(item.get("direction"))


def filter_activity(rows, *, q="", date_from=None, date_to=None, direction=None, status=None):
    term = search_text(q)
    for row in checked_rows(rows):
        if date_from and not date_from <= row["business_date"] <= date_to:
            continue
        if direction and row.get("movement_type") != direction:
            continue
        if status and (status == "info") != bool(row.get("informational")):
            continue
        if term:
            columns = (row.get(key) for key in ("reference", "order_number", "party_name", "source_label",
                       "description", "note", "reason", "counterparty_name", "customer_name", "from_name", "to_name"))
            recorder = (row.get("recorded_by") or {}).get("name")
            if term not in search_text(recorder) and not (row.get("automatic_history") and term in "النظام تلقائيا") and not any(term in search_text(value) for value in columns):
                continue
        yield row
