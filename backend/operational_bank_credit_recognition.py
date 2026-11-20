"""Order-backed bank collections during a single operational refresh."""
from decimal import Decimal

from operational_balance_bank_orders import (
    bank_ledger_paid_amount, is_closed_bank_underpayment, is_mada_bank_split, resolve, transfer_bank,
)
from operational_bank_order_milestone import (
    HISTORY_MISSING, HISTORY_UNPROVEN, PROOF_FIELD, SOURCE_INVALID, reached_in_progress,
)
from operational_bank_receipt_rollout import (
    HISTORICAL_REVIEW, POSTED_REVIEW, needs_historical_review, posted_review_required, recorded_bank_credits,
)
from operational_demand_refresh import refresh_stage


REVIEW_ACTIONS = {
    HISTORY_MISSING: "تحقق من سجل حالة الطلب في سلة لإثبات مروره بقيد التنفيذ.",
    HISTORY_UNPROVEN: "راجع سجل الطلب؛ لم يثبت وصوله إلى قيد التنفيذ.",
    SOURCE_INVALID: "تحقق من هوية الطلب وحداثة حالة سلة.",
    POSTED_REVIEW: "طابق القيد السابق مع كشف البنك؛ لم يثبت بلوغ قيد التنفيذ وقت الاحتساب.",
    HISTORICAL_REVIEW: "طابق الحوالة السابقة مع كشف البنك قبل تسجيلها.",
    "operational_order_bank_receipt_evidence_missing": "راجع مرفق الحوالة أو مرجع دفعتها وطابقها مع البنك.",
    "operational_order_bank_payment_incomplete": "راجع مبلغ الحوالة وطرق الدفع المسجلة في سلة.",
}


class BankCreditRecognition:
    def __init__(self, db, owner, state, data, as_of):
        self.db, self.owner, self.as_of = db, owner, as_of
        self.state, self.data = state or {}, data
        self.recorded = recorded_bank_credits(state)
        self.banks = None
        self.provider_orders = None

    def _has_provider_money(self, order_id):
        engine = self.state.get("engine", {})
        if self.provider_orders is None:
            from operational_financial_activity_runtime import checked_rows
            self.provider_orders = {
                str(fact.get("order_id") or key.partition(":")[2])
                for key, fact in checked_rows(engine.get("facts", {}).items())
                if key.startswith(("capture:", "provider_order_balance:", "provider_split:"))
            }
        return order_id in self.provider_orders or "provider:" + order_id in engine.get("obligations", {})

    async def candidate(self, raw, source, order, method, total_value, total, payment, issues, transactions, conflict):
        mixed = is_mada_bank_split(raw)
        if not mixed and (conflict or not transfer_bank(method)[0]):
            return None
        identity, number = order["id"], order["order_number"]
        existing = self.recorded.get(number)
        scope = dict(owner=self.owner, order_number=number, provider_order_id=identity, as_of=self.as_of)
        try:
            reached = reached_in_progress(raw, source, **scope)
        except ValueError as exc:
            if existing and str(exc) == HISTORY_MISSING:
                # A later order without local history cannot establish an early posting.
                # Its previously booked money remains immutable.
                return None
            raise
        proof = source.get(PROOF_FIELD)
        reached_at = (proof.get("at") if reached and isinstance(proof, dict)
                      and order["status"] not in {"in_progress", "processing", "قيد التنفيذ"} else None)
        if posted_review_required(existing, reached=reached, reached_at=reached_at):
            raise ValueError(POSTED_REVIEW)
        if not reached:
            return None

        native = mixed or method == "bank"
        currency = order["currency"]
        bank_method = "bank" if native else (raw.get("payment") or {}).get("method") or raw.get("payment_method")
        paid = total
        if native:
            paid = bank_ledger_paid_amount(raw, source, currency=currency, total=total_value,
                payment_issues=issues, transaction_rows=transactions, **scope,
                frozen_provider=mixed and (bool(payment) or self._has_provider_money(identity)))
        expanded = mixed or (native and is_closed_bank_underpayment(raw, currency, total_value)) or (
            not native and reached_at is not None)
        if not existing and expanded and needs_historical_review(self.state,
                order_created_at=order["created_at"], reached_at=reached_at):
            raise ValueError(HISTORICAL_REVIEW)
        if native and self.banks is None:
            from operational_bank_catalog import bank_identities
            with refresh_stage("source_bank_catalog"):
                self.banks = await bank_identities(self.db, self.owner, include_routing=True)
        bank = resolve(self.data, self.owner, bank_method, currency, raw=raw, banks=self.banks or ())
        if paid <= 0 or paid != paid.quantize(Decimal(".01")):
            raise ValueError("operational_order_bank_amount_invalid")
        return {"bank_id": bank["id"], "bank_name": bank["name"], "amount": str(paid)}
