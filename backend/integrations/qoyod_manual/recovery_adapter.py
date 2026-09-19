"""Live boundaries for closed 404 recovery. No client exists until invoked."""
from datetime import date, datetime, timezone

from .recovery_404 import Facts, Invoice, Observation, EvidenceError, money


def runtime_identity():
    from release_identity import read_release_identity
    identity = read_release_identity()
    if not identity.get("verified_identity_available"):
        raise ValueError("verified_release_required")
    return identity["release_id"]


def invoice_evidence(node):
    from .send import _extract_qoyod_invoice_total
    total = _extract_qoyod_invoice_total(node)
    if total is None:
        raise EvidenceError("provider_total_unknown")
    # Preserve explicit zero: `or` would discard a settled balance of zero.
    def first(keys):
        for key in keys:
            if node.get(key) is not None:
                value = node[key]
                return value.get("amount") if isinstance(value, dict) else value
        return None
    paid = first(("paid_amount", "amount_paid", "total_paid"))
    remaining = first(("remaining", "outstanding", "balance", "unpaid_amount"))
    if paid is None and remaining is None:
        raise EvidenceError("provider_settlement_unknown")
    total_d = money(str(total))
    if paid is None:
        paid = total_d - money(str(remaining))
    if remaining is None:
        remaining = total_d - money(str(paid))
    if money(str(paid)) + money(str(remaining)) != total_d:
        raise EvidenceError("provider_settlement_inconsistent")
    # This tenant's native Qoyod ledger is SAR. An explicit different currency
    # is preserved and rejected by the recovery verifier, never converted.
    currency = node.get("currency") or "SAR"
    if isinstance(currency, dict):
        currency = currency.get("code")
    return Invoice(str(node.get("reference") or ""), str(node.get("id") or ""),
                   str(total_d), str(paid), str(remaining), str(currency))


class ProductionPorts:
    def __init__(self, db, campaign):
        self.db, self.campaign = db, campaign
        self.owner = campaign["orders_owner"]
        self.latest_facts = {}
        self.latest_invoice = {}

    async def authorized(self, release):
        from .auto_send import is_armed
        from integrations.qoyod.candidate_orders import UNIFIED_CANDIDATE_AUTO_FLAG
        settings = await self.db.qoyod_settings.find_one({"user_id": "main"}) or {}
        return (runtime_identity() == release and is_armed(settings)
                and settings.get(UNIFIED_CANDIDATE_AUTO_FLAG) is True)

    async def facts(self, reference):
        from salla_integration.sync import resync_single_order, _salla_order_to_doc
        from integrations.qoyod.candidate_orders import eligible_status_key, payment_eligibility, PAYMENT_ELIGIBLE
        from integrations.qoyod.payment_methods import is_cod_family
        from .send import _find_unified_salla_accounting_canon, _preflight_qoyod_invoice
        refreshed = await resync_single_order(self.db, self.owner, reference)
        if not refreshed.get("ok") or not refreshed.get("found"):
            raise EvidenceError("salla_refresh_failed")
        stored = await self.db.unified_orders.find_one(
            {"user_id": self.owner, "order_number": reference}) or {}
        # Read the just-refreshed Salla source, never the merged historical total.
        raw = (stored.get("raw_by_source") or {}).get("salla_direct")
        if not isinstance(raw, dict):
            raise EvidenceError("fresh_salla_source_missing")
        from .send import _find_salla_accounting_node
        raw = _find_salla_accounting_node({"raw_payload": raw}, reference)
        if not raw:
            raise EvidenceError("fresh_salla_reference_unverified")
        doc = _salla_order_to_doc(raw)
        canon = await _find_unified_salla_accounting_canon(
            self.db, unified_owner_id=self.owner, order_number=reference)
        if not canon:
            raise EvidenceError("fresh_salla_accounting_unverified")
        total = str(doc.get("total_amount"))
        if money(total) != money(str(canon.get("total_amount"))):
            raise EvidenceError("fresh_salla_normalization_mismatch")
        items = canon.get("items") or []
        skus = bool(items) and all(str(x.get("sku") or "").strip() for x in items)
        cod = is_cod_family(doc.get("payment_method"))
        settings = await self.db.qoyod_settings.find_one({"user_id": "main"}) or {}
        expected = total
        if skus and not cod:
            preflight = _preflight_qoyod_invoice(canon=canon, settings=settings, salla_total=float(total))
            expected = str(preflight["qoyod_predicted_total"])
        q = await self.db.qoyod_manual_auto_quarantines.find_one(
            {"_id": f"main:{reference}"}) or {}
        detail = q.get("detail") or {}
        endpoint = str(detail.get("endpoint") or "")
        failure_status = detail.get("status_code")
        if isinstance(detail.get("detail"), dict):
            endpoint = endpoint or str(detail["detail"].get("endpoint") or "")
            failure_status = failure_status or detail["detail"].get("status_code")
        stamp = q.get("last_seen_at")
        prepared = self.campaign["prepared_at"]
        if isinstance(stamp, str):
            stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if isinstance(stamp, datetime) and stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if prepared.tzinfo is None:
            prepared = prepared.replace(tzinfo=timezone.utc)
        status = eligible_status_key(doc.get("order_status_slug"), doc.get("order_status"))
        facts = Facts(reference, date.fromisoformat(str(doc.get("order_date"))[:10]),
            "in_delivery" if status == "delivering" else str(status),
            payment_eligibility(doc) == PAYMENT_ELIGIBLE, cod, skus, total, expected,
            str(canon.get("currency") or canon.get("currency_code") or ""),
            q.get("status") == "open", int(failure_status or 0),
            endpoint.split(" ", 1)[0], endpoint.split(" ", 1)[-1],
            bool(stamp and stamp < prepared), True)
        self.latest_facts[reference] = facts
        return facts

    async def observe(self, reference):
        from integrations.qoyod.credentials import get_api_key
        from .client import ManualQoyodClient, ManualQoyodError, _is_confirmed_empty_list
        key = await get_api_key(self.db, "main")
        if not key:
            raise EvidenceError("qoyod_credentials_missing")
        client = ManualQoyodClient(api_key=key)
        matches, seen = [], set()
        complete = False
        # A complete fresh scan preserves duplicates; the ordinary sender's
        # first-match reference cache is not sufficient as completion proof.
        for page in range(1, 201):
            try:
                body = await client._request("GET", "/invoices", params={"page": page, "limit": 50})
            except ManualQoyodError as exc:
                if _is_confirmed_empty_list(exc):
                    complete = True
                    break
                raise
            rows = client._invoice_rows(body)
            if rows is None:
                raise EvidenceError("provider_page_unknown")
            for row in rows:
                identity = str(row.get("id") or "")
                if not identity or identity in seen:
                    raise EvidenceError("provider_pagination_unverified")
                seen.add(identity)
                if str(row.get("reference") or "") == reference:
                    # Read persisted values, not create-response/local projections.
                    shown = await client.get_invoice(int(identity))
                    if str(shown.get("id") or "") != identity or str(shown.get("reference") or "") != reference:
                        raise EvidenceError("provider_invoice_identity_mismatch")
                    matches.append(invoice_evidence(shown))
            if len(rows) < 50:
                complete = True
                break
        if len(matches) == 1:
            self.latest_invoice[reference] = matches[0]
        row = await self.db.unified_orders.find_one(
            {"user_id": self.owner, "order_number": reference}) or {}
        accounting = row.get("accounting") or {}
        marker = str(accounting.get("invoice_id") or row.get("qoyod_invoice_id") or "")
        reconciled = (accounting.get("status") == "paid" and accounting.get("remaining") == 0)
        return Observation(tuple(matches), complete, True, marker, reconciled)

    async def send_guarded(self, reference):
        from .send import manual_send_one
        from .recovery_campaign import scope_from, CAMPAIGN
        from .recovery_404 import refusal
        previous = self.latest_facts[reference]
        facts = await self.facts(reference)
        reason = refusal(scope_from(self.campaign), reference, facts)
        if reason or facts.total != previous.total:
            raise EvidenceError(reason or "live_total_changed_before_send")
        campaign = await self.db.qoyod_404_campaigns.find_one({"_id": CAMPAIGN})
        if not campaign or campaign["state"] != "active" or not await self.authorized(campaign["release_identity"]):
            raise EvidenceError("activation_revoked")
        await manual_send_one(self.db, user_id="main", orders_user_id=self.owner,
            order_number=reference, actor="404-recovery", allow_historical_positive_total=True,
            recovery_expected_total=facts.total)

    async def reconcile_marker(self, reference, invoice_id):
        from qoyod_order_accounting_sync import sync_unified_order_accounting
        invoice = self.latest_invoice[reference]
        if invoice.invoice_id != invoice_id:
            raise EvidenceError("repair_invoice_identity_mismatch")
        row = await self.db.unified_orders.find_one(
            {"user_id": self.owner, "order_number": reference}) or {}
        result = await sync_unified_order_accounting(self.db, orders_user_id=self.owner,
            order_number=reference, invoice_id=invoice_id,
            payment_id=(row.get("accounting") or {}).get("payment_id"),
            total=float(invoice.total), paid_amount=float(invoice.paid), remaining=0,
            status="paid", source="404_recovery_verified", actor="404-recovery")
        if not result.get("ok"):
            raise EvidenceError("local_marker_repair_failed")
