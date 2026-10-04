"""Request-local bounded inputs for the unchanged recurring accrual calculator.

Only Dashboard opts in. Unlike the legacy 5,000/20,000 loader, this reads the
complete owner's cohort in batches. Invoice selection preserves original cursor
order for overlapping actuals and raw-text descending/stable history ordering.
No accrual formula, accumulation order, rounding or durable data is changed.
"""
from time import perf_counter
from uuid import uuid4

from dashboard_spill import _decode, _store_value
from recurring_obligations_routes import (
    INVOICES, OBLIGATIONS, UTILITY_TYPES, _parse_day, _text, obligation_active_on,
)

BATCH_SIZE = 128
OBLIGATION_PROJECTION = dict.fromkeys((
    "id", "status", "start_date", "stopped_at", "auto_renew", "cycle",
    "custom_end_date", "expense_type", "estimation_basis", "period_amount",
), 1) | {"_id": 0}
INVOICE_PROJECTION = dict.fromkeys(("obligation_id", "period_start", "period_end", "amount"), 1) | {"_id": 0}


class DashboardRecurringInputs:
    def __init__(self, store):
        self.store = store
        self.namespace = "recurring-" + uuid4().hex
        self.obligations = store.sequence(self.namespace)
        self.metrics = dict(obligations_loaded=0, invoices_loaded=0,
                            max_mongo_batch=0, max_selected_invoices=0)
        store.execute("""CREATE TABLE IF NOT EXISTS recurring_invoice_inputs (
            namespace TEXT, obligation_id TEXT, ordinal INTEGER,
            start_day TEXT, end_day TEXT, raw_end TEXT,
            positive INTEGER, invalid_amount INTEGER, payload BLOB,
            PRIMARY KEY(namespace, ordinal)) WITHOUT ROWID""")
        store.execute("CREATE INDEX IF NOT EXISTS recurring_actual ON recurring_invoice_inputs(namespace,obligation_id,ordinal)")
        store.execute("CREATE INDEX IF NOT EXISTS recurring_history ON recurring_invoice_inputs(namespace,obligation_id,raw_end DESC,ordinal)")
        store.execute("CREATE INDEX IF NOT EXISTS recurring_invalid ON recurring_invoice_inputs(namespace,obligation_id,invalid_amount,end_day)")

    def add_invoice(self, invoice):
        start, end = _parse_day(invoice.get("period_start")), _parse_day(invoice.get("period_end"))
        try:
            positive, invalid = int(float(invoice.get("amount") or 0) > 0), 0
        except (TypeError, ValueError, OverflowError):
            positive, invalid = 0, 1
        self.store.execute("INSERT INTO recurring_invoice_inputs VALUES (?,?,?,?,?,?,?,?,?)", (
            self.namespace, _text(invoice.get("obligation_id")), self.metrics["invoices_loaded"],
            start.isoformat() if start else None, end.isoformat() if end else None,
            _text(invoice.get("period_end")), positive, invalid, _store_value(invoice)))
        self.metrics["invoices_loaded"] += 1

    def invoices_for_day(self, row, target):
        # Fixed/inactive obligations never consume invoice amounts in the
        # canonical helper, including malformed historical amounts.
        if _text(row.get("expense_type")) not in UTILITY_TYPES or not obligation_active_on(row, target):
            return []
        args = (self.namespace, _text(row.get("id")), target.isoformat())
        actual = self.store.execute("""SELECT payload FROM recurring_invoice_inputs
            WHERE namespace=? AND obligation_id=? AND start_day<=? AND end_day>=?
            ORDER BY ordinal LIMIT 1""", args + (target.isoformat(),)).fetchone()
        if actual:
            selected = [_decode(actual[0])]
        else:
            # Original history filtering evaluates every eligible amount even
            # when the chosen basis is manual. Preserve its validation errors.
            invalid = self.store.execute("""SELECT payload FROM recurring_invoice_inputs
                WHERE namespace=? AND obligation_id=? AND end_day<? AND invalid_amount=1
                ORDER BY ordinal LIMIT 1""", args).fetchone()
            if invalid:
                float(_decode(invalid[0]).get("amount") or 0)
            basis = _text(row.get("estimation_basis") or "last_3_invoices")
            limit = 1 if basis == "last_invoice" else 3 if basis == "last_3_invoices" else 0
            selected = [_decode(record[0]) for record in self.store.execute("""SELECT payload
                FROM recurring_invoice_inputs WHERE namespace=? AND obligation_id=?
                AND end_day<? AND positive=1 ORDER BY raw_end DESC, ordinal ASC LIMIT ?""", args + (limit,))]
        self.metrics["max_selected_invoices"] = max(self.metrics["max_selected_invoices"], len(selected))
        return selected


async def load_dashboard_recurring_inputs(db, user_id, store):
    inputs = DashboardRecurringInputs(store)
    started = perf_counter()
    for collection, projection in ((OBLIGATIONS, OBLIGATION_PROJECTION), (INVOICES, INVOICE_PROJECTION)):
        cursor = db[collection].find({"user_id": user_id}, projection).batch_size(BATCH_SIZE)
        try:
            while True:
                batch = await cursor.to_list(length=BATCH_SIZE)
                if not batch:
                    break
                inputs.metrics["max_mongo_batch"] = max(inputs.metrics["max_mongo_batch"], len(batch))
                for row in batch:
                    if collection == OBLIGATIONS:
                        inputs.obligations.append(row)
                        inputs.metrics["obligations_loaded"] += 1
                    else:
                        inputs.add_invoice(row)
        finally:
            await cursor.close()
    inputs.metrics["load_ms"] = (perf_counter() - started) * 1000
    return inputs
