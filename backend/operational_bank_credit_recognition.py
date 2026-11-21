"""Order-backed bank collections during a single operational refresh."""
from collections import defaultdict
from decimal import Decimal

from operational_balance_bank_orders import (
    bank_ledger_paid_amount, frozen_bank_provider_conflict, is_bank_split, is_closed_bank_underpayment, is_original_mada_split,
    is_original_wallet_split, resolve, transfer_bank,
)
from operational_bank_order_milestone import (
    HISTORY_MISSING, HISTORY_UNPROVEN, SOURCE_INVALID, bank_status_evidence,
)
from operational_bank_receipt_rollout import (
    FOUR_STATUS_MARKER, HISTORICAL_REVIEW, MARKER, needs_historical_review, recorded_bank_credits,
)
from operational_demand_refresh import refresh_stage


STATUS_INELIGIBLE = "operational_order_bank_status_ineligible"


class BankReceiptStatusHold(ValueError):
    def __init__(self, status, bank_id):
        super().__init__(STATUS_INELIGIBLE)
        self.details = {"order_status": status, "party_type": "bank", "status": "pending"}
        if bank_id:
            self.details["party_id"] = bank_id


REVIEW_ACTIONS = {
    STATUS_INELIGIBLE: "غير معتمد بسبب حالة الطلب",
    HISTORY_MISSING: "تحقق من سجل حالة الطلب في سلة لتحديد تاريخ أول حالة مؤهلة لاعتماد الحوالة.",
    HISTORY_UNPROVEN: "راجع سجل الطلب؛ لم يثبت تاريخ انتقال الطلب إلى حالة مؤهلة.",
    SOURCE_INVALID: "تحقق من هوية الطلب وحداثة حالة سلة.",
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
        self.provider_facts = None
        self.provider_claims = None
        self.reference_owners = None

    def _index_provider_postings(self):
        from operational_financial_activity_runtime import checked_rows
        from operational_split_payment import split_reference_owners
        engine = self.state.get("engine", {})
        facts = engine.get("facts", {})
        self.provider_facts, self.provider_claims = defaultdict(list), defaultdict(list)
        self.reference_owners = split_reference_owners(facts)
        for key, fact in checked_rows(facts.items()):
            if key.startswith(("capture:", "refund:")):
                order_id = fact.get("order_id")
            elif key.startswith(("provider_split:", "provider_order_balance:")):
                order_id = key.partition(":")[2]
            else:
                continue
            if order_id:
                self.provider_facts[str(order_id)].append((key, fact))
        for key, claim in checked_rows(engine.get("obligations", {}).items()):
            prefix, _, rest = key.removeprefix("credit:").partition(":")
            if prefix == "provider" and rest:
                self.provider_claims[rest.partition(":")[0]].append((key, claim))

    def _provider_conflict(self, raw, order):
        from operational_payment_evidence import _identity
        if self.provider_facts is None:
            self._index_provider_postings()
        order_id = order["id"]
        for leg in raw["payment_methods"]:
            if isinstance(leg, dict) and leg.get("transaction_reference") is not None:
                ref = _identity(leg["transaction_reference"], "operational_order_bank_payment_incomplete")
                if self.reference_owners.get(ref, set()) - {order_id}:
                    return True
        return frozen_bank_provider_conflict(raw, owner=self.owner, order_number=order["order_number"],
            provider_order_id=order_id, currency=order["currency"], facts=self.provider_facts.get(order_id, ()),
            obligations=self.provider_claims.get(order_id, ()))

    async def _bank(self, raw, method, currency, native):
        if native and self.banks is None:
            from operational_bank_catalog import bank_identities
            with refresh_stage("source_bank_catalog"):
                self.banks = await bank_identities(self.db, self.owner, include_routing=True)
        return resolve(self.data, self.owner, method, currency, raw=raw, banks=self.banks or ())

    async def candidate(self, raw, source, order, method, total_value, total, payment, issues, transactions, conflict):
        mixed = is_bank_split(raw)
        if not mixed and (conflict or not transfer_bank(method)[0]):
            return None
        identity, number = order["id"], order["order_number"]
        existing = self.recorded.get(number)
        scope = dict(owner=self.owner, order_number=number, provider_order_id=identity, as_of=self.as_of)
        basis = bank_status_evidence(raw, source, **scope)
        native = mixed or method == "bank"
        currency = order["currency"]
        legacy_payment = raw.get("payment") if isinstance(raw.get("payment"), dict) else {}
        bank_method = "bank" if native else legacy_payment.get("method") or raw.get("payment_method")
        if basis is None:
            if existing:
                return None
            try:
                bank = await self._bank(raw, bank_method, currency, native)
            except ValueError:
                bank = {}
            status = raw.get("status")
            if isinstance(status, dict):
                status = status.get("customized") or status
            name = status.get("name") if isinstance(status, dict) else status
            raise BankReceiptStatusHold(name or order["status"], bank.get("id"))

        paid = total
        if native:
            if raw.get("is_pending_payment") is True:
                raise ValueError("operational_order_bank_payment_incomplete")
            paid = bank_ledger_paid_amount(raw, source, currency=currency, total=total_value,
                payment_issues=issues, transaction_rows=transactions, **scope,
                frozen_provider=mixed and (bool(payment) or self._provider_conflict(raw, order)))
        elif raw.get("payment_methods") is not None:
            # The legacy label permits gross only for an undivided bank payment.
            # Explicit tenders must never turn a partial transfer into the order total.
            from operational_payment_evidence import _amount
            legs = raw["payment_methods"]
            if (not isinstance(legs, list) or len(legs) != 1 or not isinstance(legs[0], dict)
                    or str(legs[0].get("payment_method")).strip().lower() not in {method, "bank"}
                    or legs[0].get("currency") not in (None, currency)
                    or _amount(legs[0].get("amount"), currency) != total):
                raise ValueError("operational_order_bank_payment_incomplete")
        # Existing stores may have unposted receipts predating the four-status policy.
        # A current status confirms eligibility, but does not date the first transition.
        marker = None
        original_mada = mixed and is_original_mada_split(raw, currency, total_value)
        if basis["status"] != "in_progress" or (mixed and not original_mada and not is_original_wallet_split(raw)):
            marker = FOUR_STATUS_MARKER
        elif original_mada or (native and is_closed_bank_underpayment(raw, currency, total_value)):
            marker = MARKER
        if not existing and marker and needs_historical_review(self.state, order_created_at=order["created_at"],
                                                                marker_key=marker):
            basis = bank_status_evidence(raw, source, **scope, require_history=True)
            if needs_historical_review(self.state, order_created_at=order["created_at"],
                                       reached_at=basis["observed_at"], marker_key=marker):
                raise ValueError(HISTORICAL_REVIEW)
        bank = await self._bank(raw, bank_method, currency, native)
        if paid <= 0 or paid != paid.quantize(Decimal(".01")):
            raise ValueError("operational_order_bank_amount_invalid")
        credit = {"bank_id": bank["id"], "bank_name": bank["name"], "amount": str(paid),
                  "preparation_evidence": basis}
        if native:
            from operational_payment_evidence import _identity
            for leg in raw["payment_methods"]:
                if leg["payment_method"] == "bank" and leg.get("transaction_reference") is not None:
                    try:
                        credit["bank_transaction_reference"] = _identity(leg["transaction_reference"], "invalid_reference")
                    except ValueError:
                        # Older single-tender records may contain display-only references.
                        pass
        return credit
